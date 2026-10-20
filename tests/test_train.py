import json

import joblib

from liquid_biopsy.data import split_features_target
from liquid_biopsy.train import build_pipeline, evaluate


def test_training_writes_all_artifacts(trained_model_dir):
    for name in ("model.joblib", "metadata.json", "metrics.json", "feature_importance.csv", "roc_curve.png"):
        assert (trained_model_dir / name).exists(), name

    meta = json.loads((trained_model_dir / "metadata.json").read_text())
    assert meta["n_features"] == 90
    assert len(meta["features"]) == 90
    assert 0.5 < meta["test_metrics"]["roc_auc"] <= 1.0


def test_smote_is_not_applied_at_prediction_time(trained_model_dir, dataset):
    """SMOTE must only resample during fit; predict returns one row per input row."""
    model = joblib.load(trained_model_dir / "model.joblib")
    X, _ = split_features_target(dataset)
    proba = model.predict_proba(X.head(7))
    assert proba.shape == (7, 2)


def test_evaluate_metric_keys(dataset):
    X, y = split_features_target(dataset)
    model = build_pipeline(random_state=0).set_params(clf__n_estimators=20).fit(X, y)
    metrics, proba = evaluate(model, X, y)
    assert {"roc_auc", "balanced_accuracy", "sensitivity", "specificity", "f1"} <= metrics.keys()
    assert len(proba) == len(y)
