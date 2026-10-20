"""Train, evaluate and package the cancer classifier.

Pipeline (all fitted on the TRAINING split only, so nothing leaks from test):
    SMOTE oversampling -> Random Forest, tuned with stratified k-fold GridSearchCV

Outputs (in models/):
    model.joblib            fitted imblearn Pipeline (SMOTE is skipped at predict time)
    metadata.json           model version, features, metrics, params, library versions
    metrics.json            held-out test metrics (read by CI)
    feature_importance.csv  gene importances
    roc_curve.png           ROC curve on the held-out test set
    reference_stats.json    per-gene training distribution, used by the drift check

Every run is also tracked in MLflow (params, metrics, artifacts).

Run:  python -m liquid_biopsy.train
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import imblearn
import joblib
import numpy as np
import pandas as pd
import sklearn
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split

from . import __version__
from .config import load_params, resolve
from .data import load_dataset, split_features_target
from .reference import build_reference
from .tracking import log_run
from .validate import validate_dataset


def build_pipeline(random_state: int) -> Pipeline:
    return Pipeline(
        steps=[
            ("smote", SMOTE(random_state=random_state)),
            ("clf", RandomForestClassifier(random_state=random_state, n_jobs=-1)),
        ]
    )


def evaluate(model, X: pd.DataFrame, y: pd.Series) -> tuple[dict[str, float], np.ndarray]:
    proba = model.predict_proba(X)[:, 1]
    pred = (proba >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    metrics = {
        "roc_auc": roc_auc_score(y, proba),
        "balanced_accuracy": balanced_accuracy_score(y, pred),
        "accuracy": accuracy_score(y, pred),
        "f1": f1_score(y, pred),
        "sensitivity": tp / (tp + fn) if (tp + fn) else 0.0,  # cancer correctly flagged
        "specificity": tn / (tn + fp) if (tn + fp) else 0.0,  # healthy correctly cleared
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
    }
    return {k: v if isinstance(v, int) else round(float(v), 4) for k, v in metrics.items()}, proba


def git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:7]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return "local"


def save_roc_curve(y_true, proba, auc: float, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fpr, tpr, _ = roc_curve(y_true, proba)
    fig, ax = plt.subplots(figsize=(5, 5), dpi=120)
    ax.plot(fpr, tpr, color="#2563eb", lw=2, label=f"Random Forest (AUC = {auc:.3f})")
    ax.plot([0, 1], [0, 1], color="#9ca3af", ls="--", lw=1, label="Chance")
    ax.set_xlabel("False positive rate (1 - specificity)")
    ax.set_ylabel("True positive rate (sensitivity)")
    ax.set_title("ROC curve, held-out test set")
    ax.legend(loc="lower right", frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def log_to_mlflow(params: dict, metadata: dict, out_dir: Path, model) -> str | None:
    try:
        import mlflow  # noqa: F401
    except ImportError:
        print("MLflow not installed; skipping experiment tracking")
        return None
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", params["artifacts"]["mlflow_tracking_uri"])
    return log_run(
        metadata,
        out_dir,
        tracking_uri,
        params["artifacts"]["mlflow_experiment"],
        model=model,
        tags={"git_sha": git_sha()},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the liquid biopsy classifier")
    parser.add_argument("--params", default=None, help="path to params.yaml")
    parser.add_argument("--no-mlflow", action="store_true", help="skip MLflow tracking")
    parser.add_argument("--no-gate", action="store_true", help="do not fail on the quality gate")
    parser.add_argument("--fast", action="store_true", help="tiny grid for quick test runs")
    args = parser.parse_args(argv)

    params = load_params(args.params) if args.params else load_params()
    seed = params["model"]["random_state"]

    # 1. Load + validate
    df = load_dataset(
        resolve(params["data"]["path"]),
        params["data"]["healthy_prefixes"],
        params["data"].get("exclude_prefixes"),
    )
    report = validate_dataset(df)
    if not report.ok:
        print("Data validation failed:", *report.errors, sep="\n  ")
        return 1
    X, y = split_features_target(df)

    # 2. Split FIRST, then resample only inside training folds (no leakage)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=params["split"]["test_size"], stratify=y, random_state=params["split"]["random_state"]
    )
    print(f"Train: {len(X_train)} samples  Test: {len(X_test)} samples  Genes: {X.shape[1]}")

    # 3. Tune with stratified CV
    grid = params["model"]["param_grid"]
    if args.fast:
        grid = {"clf__n_estimators": [50], "clf__max_features": [8]}
    search = GridSearchCV(
        build_pipeline(seed),
        param_grid=grid,
        scoring="roc_auc",
        cv=StratifiedKFold(n_splits=params["model"]["cv_folds"], shuffle=True, random_state=seed),
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    model = search.best_estimator_
    cv_auc = round(float(search.best_score_), 4)
    print(f"Best params: {search.best_params_}  CV ROC-AUC: {cv_auc}")

    # 4. Evaluate once on the untouched test set
    metrics, test_proba = evaluate(model, X_test, y_test)
    print("Test metrics:", json.dumps(metrics, indent=2))

    # 5. Save artifacts
    out_dir = resolve(params["artifacts"]["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "model.joblib")

    importances = (
        pd.DataFrame({"gene": X.columns, "importance": model.named_steps["clf"].feature_importances_})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )
    importances.to_csv(out_dir / "feature_importance.csv", index=False)
    save_roc_curve(y_test, test_proba, metrics["roc_auc"], out_dir / "roc_curve.png")
    # Drift reference from the real training samples only (no synthetic SMOTE rows)
    (out_dir / "reference_stats.json").write_text(json.dumps(build_reference(X_train)))

    model_version = f"{__version__}+{git_sha()}"
    metadata = {
        "model_version": model_version,
        "git_sha": git_sha(),
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_type": "RandomForestClassifier with SMOTE (imblearn Pipeline)",
        "features": list(X.columns),
        "n_features": int(X.shape[1]),
        "label_names": {"0": "healthy", "1": "cancer"},
        "train_samples": int(len(X_train)),
        "test_samples": int(len(X_test)),
        "class_counts": {"healthy": int((y == 0).sum()), "cancer": int((y == 1).sum())},
        "best_params": {k.replace("clf__", ""): v for k, v in search.best_params_.items()},
        "cv_roc_auc": cv_auc,
        "test_metrics": metrics,
        "top_genes": importances.head(10).to_dict(orient="records"),
        "versions": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "imbalanced-learn": imblearn.__version__,
        },
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    (out_dir / "metrics.json").write_text(json.dumps({"cv_roc_auc": cv_auc, **metrics}, indent=2))
    print(f"Saved model {model_version} to {out_dir}")

    if not args.no_mlflow:
        run_id = log_to_mlflow(params, metadata, out_dir, model)
        if run_id:
            print(f"MLflow run: {run_id}")

    # 6. Quality gate
    gate = params["quality_gate"]
    failures = []
    if metrics["roc_auc"] < gate["min_roc_auc"]:
        failures.append(f"ROC-AUC {metrics['roc_auc']} < {gate['min_roc_auc']}")
    if metrics["balanced_accuracy"] < gate["min_balanced_accuracy"]:
        failures.append(f"balanced accuracy {metrics['balanced_accuracy']} < {gate['min_balanced_accuracy']}")
    if failures:
        print("QUALITY GATE FAILED:", *failures, sep="\n  ")
        return 0 if args.no_gate else 1
    print("Quality gate PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
