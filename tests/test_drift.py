import json
from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app import drift
from app import main as api
from app.db.models import DriftReport, ModelVersion, Prediction, PredictionGene
from app.db.session import get_sessionmaker
from liquid_biopsy.data import split_features_target
from liquid_biopsy.reference import build_reference

SETTINGS = drift.DriftSettings(window_days=7, min_predictions=30, psi_threshold=0.25, max_flagged_share=0.10)


def test_psi_is_small_for_same_distribution_and_large_for_shifted(dataset):
    X, _ = split_features_target(dataset)
    ref = build_reference(X)["genes"]
    gene = X.columns[0]
    assert drift.psi(ref[gene], X[gene].to_numpy()) < 0.01
    assert drift.psi(ref[gene], X[gene].to_numpy() * 8) > 1.0


def test_reference_bins_cover_all_genes(trained_model_dir):
    ref = json.loads((trained_model_dir / "reference_stats.json").read_text())
    meta = json.loads((trained_model_dir / "metadata.json").read_text())
    assert set(ref["genes"]) == set(meta["features"])
    for g in ref["genes"].values():
        assert len(g["expected"]) == len(g["edges"]) + 1
        assert abs(sum(g["expected"]) - 1) < 1e-3


def seed_predictions(db_url, matrix, model_version_id, created_at=None):
    """Insert predictions + their genes directly (faster than going through the API)."""
    created_at = created_at or datetime.now(UTC)
    with get_sessionmaker(db_url)() as session:
        rows = [
            Prediction(
                model_version_id=model_version_id,
                request_id="seed",
                created_at=created_at,
                cancer_probability=0.5,
                label=1,
                latency_ms=1.0,
            )
            for _ in range(len(matrix))
        ]
        session.add_all(rows)
        session.flush()
        session.execute(
            insert(PredictionGene),
            [
                {"prediction_id": r.id, "gene_id": g, "read_count": float(v)}
                for r, (_, sample) in zip(rows, matrix.iterrows(), strict=True)
                for g, v in sample.items()
            ],
        )
        session.commit()


def model_version_id(db_url, trained_model_dir):
    client = TestClient(api.app)
    client.__enter__()
    api.load_artifacts(trained_model_dir)
    api.init_database(db_url)
    client.__exit__(None, None, None)
    with get_sessionmaker(db_url)() as session:
        return session.scalar(select(ModelVersion.id))


def test_drift_check_ok_for_training_like_data(db_url, trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    mv_id = model_version_id(db_url, trained_model_dir)
    seed_predictions(db_url, X, mv_id)
    reference = json.loads((trained_model_dir / "reference_stats.json").read_text())
    with get_sessionmaker(db_url)() as session:
        report = drift.run_drift_check(session, reference, mv_id, SETTINGS)
        assert report.status == "ok", [(g.gene_id, g.psi) for g in report.genes if g.flagged]
        assert report.n_predictions == len(X)
        assert len(report.genes) == 90


def test_drift_check_detects_shifted_inputs(db_url, trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    mv_id = model_version_id(db_url, trained_model_dir)
    seed_predictions(db_url, X * 8, mv_id)  # e.g. a lab that changed its sequencing depth
    reference = json.loads((trained_model_dir / "reference_stats.json").read_text())
    with get_sessionmaker(db_url)() as session:
        report = drift.run_drift_check(session, reference, mv_id, SETTINGS)
        assert report.status == "drift"
        assert report.n_genes_flagged > 45


def test_drift_check_ignores_predictions_outside_the_window(db_url, trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    mv_id = model_version_id(db_url, trained_model_dir)
    seed_predictions(db_url, X * 8, mv_id, created_at=datetime.now(UTC) - timedelta(days=30))
    reference = json.loads((trained_model_dir / "reference_stats.json").read_text())
    with get_sessionmaker(db_url)() as session:
        report = drift.run_drift_check(session, reference, mv_id, SETTINGS)
        assert report.status == "insufficient_data"
        assert report.n_predictions == 0


def test_drift_job_end_to_end(db_url, trained_model_dir, dataset, monkeypatch):
    """The scheduled job entrypoint writes a report that the API then serves."""
    X, _ = split_features_target(dataset)
    mv_id = model_version_id(db_url, trained_model_dir)
    seed_predictions(db_url, X.iloc[:40] * 8, mv_id)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("MODEL_DIR", str(trained_model_dir))
    assert drift.main() == 0

    with get_sessionmaker(db_url)() as session:
        assert session.scalar(select(DriftReport.status)) == "drift"

    client = TestClient(api.app)
    client.__enter__()
    try:
        api.load_artifacts(trained_model_dir)
        api.init_database(db_url)
        body = client.get("/drift/latest").json()
        assert body["status"] == "drift"
        assert body["top_genes"][0]["flagged"] is True
        assert body["top_genes"][0]["psi"] >= body["top_genes"][-1]["psi"]
    finally:
        api.init_database(None)
        client.__exit__(None, None, None)


def test_psi_handles_identical_values():
    ref = {"edges": [1.0], "expected": [0.5, 0.5]}
    assert np.isfinite(drift.psi(ref, np.zeros(10)))
