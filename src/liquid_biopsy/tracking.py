"""Log a trained model's run to MLflow: locally (SQLite) or to Azure Machine Learning.

The same function is used by train.py (local tracking) and by azureml_log.py
(the CD pipeline logs the exact CI-tested model to the Azure ML workspace).
"""

from __future__ import annotations

import os
from pathlib import Path

ARTIFACTS = ("metadata.json", "metrics.json", "feature_importance.csv", "roc_curve.png", "reference_stats.json")
# Recent MLflow saves sklearn models with skops and requires custom types to be trusted
# explicitly. These are exactly the types in our own pipeline.
TRUSTED_TYPES = ["imblearn.over_sampling._smote.base.SMOTE", "imblearn.pipeline.Pipeline", "sklearn.tree._tree.Tree"]


def log_run(
    metadata: dict,
    out_dir: Path,
    tracking_uri: str,
    experiment: str,
    model=None,
    tags: dict | None = None,
    log_model_flavor: bool = True,
) -> str:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    # Inside an Azure ML job the run already exists (MLFLOW_RUN_ID) in its own experiment
    if not os.environ.get("MLFLOW_RUN_ID"):
        mlflow.set_experiment(experiment)

    with mlflow.start_run(run_name=f"rf-{metadata['model_version']}") as run:
        mlflow.set_tags(
            {"model_version": metadata["model_version"], "model_type": "RandomForest+SMOTE", **(tags or {})}
        )
        mlflow.log_params(metadata["best_params"])
        mlflow.log_params({"n_features": metadata["n_features"], "train_samples": metadata["train_samples"]})
        mlflow.log_metric("cv_roc_auc", metadata["cv_roc_auc"])
        mlflow.log_metrics({f"test_{k}": float(v) for k, v in metadata["test_metrics"].items()})
        for name in ARTIFACTS:
            if (out_dir / name).exists():
                mlflow.log_artifact(str(out_dir / name))

        if model is not None and log_model_flavor:
            try:
                try:
                    mlflow.sklearn.log_model(model, name="model", skops_trusted_types=TRUSTED_TYPES)
                except TypeError:  # older MLflow: no skops, and 2.x uses artifact_path
                    try:
                        mlflow.sklearn.log_model(model, name="model")
                    except TypeError:
                        mlflow.sklearn.log_model(model, artifact_path="model")
            except Exception as exc:  # tracking must never break training
                print(f"WARNING: could not log MLflow model flavor ({exc.__class__.__name__}); logging joblib file")
                mlflow.log_artifact(str(out_dir / "model.joblib"))
        else:
            mlflow.log_artifact(str(out_dir / "model.joblib"))
        return run.info.run_id
