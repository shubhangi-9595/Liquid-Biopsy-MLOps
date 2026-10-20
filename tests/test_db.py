"""Database tests: run on SQLite always, and on real Postgres when TEST_DATABASE_URL is set."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main as api
from app.db.models import Base, ModelVersion, Prediction, PredictionGene
from app.db.session import get_engine, get_sessionmaker
from liquid_biopsy.data import split_features_target


def test_migrations_match_models(db_url):
    """Fails if someone changes app/db/models.py without writing a migration."""
    with get_engine(db_url).connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_migrations_are_idempotent(db_url):
    from app.db.migrate import run_migrations

    run_migrations(db_url)  # second run must be a no-op


def make_client(db_url, trained_model_dir):
    client = TestClient(api.app)
    client.__enter__()
    api.load_artifacts(trained_model_dir)
    api.init_database(db_url)
    return client


def test_predictions_are_stored_with_their_genes(db_url, trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    samples = [X.iloc[i].to_dict() for i in range(3)]
    client = make_client(db_url, trained_model_dir)
    try:
        assert client.get("/health").json()["database"] == "connected"
        body = client.post("/predict", json={"samples": samples}).json()
        ids = [p["prediction_id"] for p in body["predictions"]]
        assert all(isinstance(i, int) for i in ids)

        with get_sessionmaker(db_url)() as session:
            rows = session.scalars(select(Prediction).where(Prediction.id.in_(ids))).all()
            assert len(rows) == 3
            assert {r.request_id for r in rows} == {body["request_id"]}
            n_genes = session.scalar(
                select(func.count()).select_from(PredictionGene).where(PredictionGene.prediction_id.in_(ids))
            )
            assert n_genes == 3 * 90
            # the stored input is exactly what was sent
            gene = X.columns[0]
            stored = session.scalar(
                select(PredictionGene.read_count).where(
                    PredictionGene.prediction_id == ids[0], PredictionGene.gene_id == gene
                )
            )
            assert stored == float(samples[0][gene])
            mv = session.scalar(select(ModelVersion))
            assert mv.version == body["model_version"]
    finally:
        client.__exit__(None, None, None)


def test_monitoring_summary_aggregates_per_model_version(db_url, trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    client = make_client(db_url, trained_model_dir)
    try:
        client.post("/predict", json={"samples": [X.iloc[i].to_dict() for i in range(4)]})
        summary = client.get("/monitoring/summary").json()
        assert len(summary) == 1
        assert summary[0]["n_predictions"] == 4
        assert 0 <= summary[0]["share_predicted_cancer"] <= 1
        assert summary[0]["last_prediction_at"] is not None
    finally:
        client.__exit__(None, None, None)


def test_drift_endpoint_404_before_first_report(db_url, trained_model_dir):
    client = make_client(db_url, trained_model_dir)
    try:
        assert client.get("/drift/latest").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_api_keeps_serving_when_database_is_down(trained_model_dir, dataset):
    X, _ = split_features_target(dataset)
    client = TestClient(api.app)
    client.__enter__()
    try:
        api.load_artifacts(trained_model_dir)
        api.init_database("postgresql+psycopg://nobody:wrong@127.0.0.1:1/none?connect_timeout=1", attempts=1)
        assert client.get("/health").json()["database"] == "error"
        r = client.post("/predict", json={"samples": [X.iloc[0].to_dict()]})
        assert r.status_code == 200
        assert r.json()["predictions"][0]["prediction_id"] is None
        assert client.get("/monitoring/summary").status_code == 503
    finally:
        api.init_database(None)
        client.__exit__(None, None, None)
