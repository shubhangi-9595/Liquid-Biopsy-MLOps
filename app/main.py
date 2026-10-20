"""FastAPI service that serves the liquid biopsy cancer classifier.

Endpoints
    GET  /health               liveness / readiness probe (Azure Container Apps, Kubernetes)
    GET  /model-info           model version, test metrics, required genes, top genes
    POST /predict              cancer probability for 1-100 samples (stored in Postgres when configured)
    GET  /monitoring/summary   predictions per model version (SQL join + aggregation)
    GET  /drift/latest         latest scheduled drift report
    GET  /docs                 interactive Swagger UI

Configuration (environment variables)
    MODEL_DIR                                folder with model.joblib + metadata.json
    DATABASE_URL                             Postgres URL; optional, enables prediction logging
    RUN_MIGRATIONS                           "true" (default) applies DB migrations on startup
    APPLICATIONINSIGHTS_CONNECTION_STRING    optional, sends telemetry to Azure Application Insights

Research demo only: not a medical device and not for clinical decisions.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import case, func, insert, select

from .db.models import DriftReport, DriftReportGene, ModelVersion, PredictionGene
from .db.models import Prediction as PredictionRow
from .db.session import database_url, get_sessionmaker
from .schemas import (
    DriftGene,
    DriftReportOut,
    HealthResponse,
    ModelVersionSummary,
    Prediction,
    PredictRequest,
    PredictResponse,
)
from .telemetry import setup_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("liquid-biopsy-api")
setup_telemetry()

MODEL_DIR = Path(os.environ.get("MODEL_DIR", Path(__file__).resolve().parents[1] / "models"))
LABEL_NAMES = {0: "healthy", 1: "cancer"}

state: dict = {
    "model": None,
    "metadata": None,
    "sessionmaker": None,
    "db_status": "not_configured",
    "model_version_id": None,
}


# ----------------------------------------------------------------------------- setup


def load_artifacts(model_dir: Path = MODEL_DIR) -> None:
    model_path = model_dir / "model.joblib"
    meta_path = model_dir / "metadata.json"
    if not model_path.exists() or not meta_path.exists():
        log.error("No model found in %s. Run `python -m liquid_biopsy.train` first.", model_dir)
        state["model"], state["metadata"], state["model_version_id"] = None, None, None
        return
    state["model"] = joblib.load(model_path)
    state["metadata"] = json.loads(meta_path.read_text())
    log.info("Loaded model %s (%d genes)", state["metadata"]["model_version"], state["metadata"]["n_features"])
    register_model_version()


def init_database(url: str | None = None, attempts: int | None = None) -> None:
    """Connect to the database (if configured), migrate it, and register the model version.

    Retries for a while because the database may still be starting (a Postgres pod next to
    the API in Kubernetes, or an Azure server waking up). If it never comes up the API
    keeps serving predictions without storing them, and /health reports database=error.
    """
    url = url or database_url()
    state["sessionmaker"], state["model_version_id"] = None, None
    if not url:
        state["db_status"] = "not_configured"
        log.info("DATABASE_URL not set: predictions will not be stored")
        return
    attempts = attempts or int(os.environ.get("DB_CONNECT_ATTEMPTS", 10))
    for attempt in range(1, attempts + 1):
        try:
            if os.environ.get("RUN_MIGRATIONS", "true").lower() == "true":
                from .db.migrate import run_migrations

                run_migrations(url)
            state["sessionmaker"] = get_sessionmaker(url)
            state["db_status"] = "connected"
            register_model_version()
            return
        except Exception as exc:
            state["sessionmaker"] = None
            if attempt < attempts:
                log.warning("Database not reachable (attempt %d/%d): %s", attempt, attempts, exc.__class__.__name__)
                time.sleep(3)
    log.error("Database unavailable after %d attempts: serving predictions without storing them", attempts)
    state["db_status"] = "error"


def register_model_version() -> None:
    """Make sure the loaded model has a row in model_versions (get or create)."""
    meta, SessionLocal = state["metadata"], state["sessionmaker"]
    if meta is None or SessionLocal is None:
        return
    with SessionLocal() as session:
        mv = session.scalar(select(ModelVersion).where(ModelVersion.version == meta["model_version"]))
        if mv is None:
            m = meta["test_metrics"]
            mv = ModelVersion(
                version=meta["model_version"],
                git_sha=meta.get("git_sha", meta["model_version"].split("+")[-1]),
                trained_at=datetime.fromisoformat(meta["trained_at"]),
                roc_auc=m["roc_auc"],
                balanced_accuracy=m["balanced_accuracy"],
                sensitivity=m["sensitivity"],
                specificity=m["specificity"],
            )
            session.add(mv)
            session.commit()
        state["model_version_id"] = mv.id


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_artifacts()
    init_database()
    yield


app = FastAPI(
    title="Liquid Biopsy Cancer Classifier",
    description=(
        "Classifies blood platelet RNA-seq profiles (tumor-educated platelets, GEO GSE68086) "
        "as cancer or healthy. **Research demo only, not for clinical use.**"
    ),
    version="2.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log.info(
        "%s %s -> %d in %.1f ms",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - start) * 1000,
    )
    return response


def require_model():
    if state["model"] is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return state["model"], state["metadata"]


def require_db():
    if state["sessionmaker"] is None:
        raise HTTPException(status_code=503, detail=f"Database {state['db_status'].replace('_', ' ')}")
    return state["sessionmaker"]


# ----------------------------------------------------------------------------- endpoints


@app.get("/", include_in_schema=False)
def root():
    return {"service": "liquid-biopsy-cancer-classifier", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse)
def health():
    loaded = state["model"] is not None
    return HealthResponse(
        status="ok" if loaded else "degraded",
        model_loaded=loaded,
        model_version=state["metadata"]["model_version"] if loaded else None,
        database=state["db_status"],
    )


@app.get("/model-info")
def model_info():
    _, meta = require_model()
    return {
        "model_version": meta["model_version"],
        "trained_at": meta["trained_at"],
        "model_type": meta["model_type"],
        "test_metrics": meta["test_metrics"],
        "cv_roc_auc": meta["cv_roc_auc"],
        "n_features": meta["n_features"],
        "required_genes": meta["features"],
        "top_genes": meta["top_genes"],
    }


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest):
    start = time.perf_counter()
    model, meta = require_model()
    features: list[str] = meta["features"]

    sent = set().union(*(s.keys() for s in request.samples))
    for i, sample in enumerate(request.samples):
        missing = [g for g in features if g not in sample]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Sample {i} is missing {len(missing)} required genes, e.g. {missing[:5]}",
            )
        negative = [g for g in features if sample[g] < 0]
        if negative:
            raise HTTPException(status_code=422, detail=f"Sample {i} has negative read counts for {negative[:5]}")

    X = pd.DataFrame([{g: s[g] for g in features} for s in request.samples], columns=features)
    proba = model.predict_proba(X)[:, 1]
    labels = (proba >= 0.5).astype(int)
    request_id = str(uuid.uuid4())
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    ids = store_predictions(request_id, X, proba, labels, latency_ms)
    predictions = [
        Prediction(
            label=int(lab),
            label_name=LABEL_NAMES[int(lab)],
            cancer_probability=round(float(p), 4),
            prediction_id=ids[i] if ids else None,
        )
        for i, (p, lab) in enumerate(zip(proba, labels, strict=True))
    ]
    # One structured line per request, queryable in Azure Log Analytics / Application Insights
    log.info(
        "prediction %s",
        json.dumps(
            {
                "request_id": request_id,
                "model_version": meta["model_version"],
                "n": len(predictions),
                "probs": [p.cancer_probability for p in predictions],
                "latency_ms": latency_ms,
            }
        ),
    )
    return PredictResponse(
        model_version=meta["model_version"],
        request_id=request_id,
        predictions=predictions,
        ignored_genes=sorted(sent - set(features)),
    )


def store_predictions(request_id: str, X: pd.DataFrame, proba, labels, latency_ms: float) -> list[int] | None:
    """Write predictions and their 90 input genes; storage problems never fail the request."""
    SessionLocal, mv_id = state["sessionmaker"], state["model_version_id"]
    if SessionLocal is None or mv_id is None:
        return None
    try:
        with SessionLocal() as session:
            rows = [
                PredictionRow(
                    model_version_id=mv_id,
                    request_id=request_id,
                    cancer_probability=float(p),
                    label=int(lab),
                    latency_ms=latency_ms,
                )
                for p, lab in zip(proba, labels, strict=True)
            ]
            session.add_all(rows)
            session.flush()  # assigns the primary keys
            gene_rows = [
                {"prediction_id": row.id, "gene_id": gene, "read_count": float(value)}
                for row, (_, sample) in zip(rows, X.iterrows(), strict=True)
                for gene, value in sample.items()
            ]
            session.execute(insert(PredictionGene), gene_rows)
            session.commit()
            return [row.id for row in rows]
    except Exception:
        log.exception("Could not store predictions for request %s", request_id)
        return None


@app.get("/monitoring/summary", response_model=list[ModelVersionSummary])
def monitoring_summary():
    """Predictions per model version: one SQL query with a LEFT JOIN and GROUP BY."""
    SessionLocal = require_db()
    stmt = (
        select(
            ModelVersion.version,
            ModelVersion.git_sha,
            ModelVersion.roc_auc,
            func.count(PredictionRow.id),
            func.avg(case((PredictionRow.label == 1, 1.0), else_=0.0)),
            func.avg(PredictionRow.cancer_probability),
            func.avg(PredictionRow.latency_ms),
            func.max(PredictionRow.created_at),
        )
        .outerjoin(PredictionRow, PredictionRow.model_version_id == ModelVersion.id)
        .group_by(ModelVersion.id)
        .order_by(ModelVersion.first_served_at.desc(), ModelVersion.id.desc())
    )
    with SessionLocal() as session:
        result = session.execute(stmt).all()

    def rnd(x, n=4):
        return None if x is None else round(float(x), n)

    return [
        ModelVersionSummary(
            model_version=v,
            git_sha=sha,
            test_roc_auc=auc,
            n_predictions=n,
            share_predicted_cancer=rnd(share) if n else None,
            avg_cancer_probability=rnd(avg_p) if n else None,
            avg_latency_ms=rnd(avg_lat, 2) if n else None,
            last_prediction_at=last,
        )
        for v, sha, auc, n, share, avg_p, avg_lat, last in result
    ]


@app.get("/drift/latest", response_model=DriftReportOut)
def latest_drift_report():
    SessionLocal = require_db()
    with SessionLocal() as session:
        row = session.execute(
            select(DriftReport, ModelVersion.version)
            .join(ModelVersion, ModelVersion.id == DriftReport.model_version_id)
            .order_by(DriftReport.run_at.desc(), DriftReport.id.desc())
            .limit(1)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="No drift report yet (the drift job runs daily)")
        report, version = row
        genes = session.scalars(
            select(DriftReportGene)
            .where(DriftReportGene.drift_report_id == report.id)
            .order_by(DriftReportGene.psi.desc())
            .limit(10)
        ).all()
        return DriftReportOut(
            report_id=report.id,
            model_version=version,
            run_at=report.run_at,
            window_start=report.window_start,
            window_end=report.window_end,
            status=report.status,
            n_predictions=report.n_predictions,
            n_genes_flagged=report.n_genes_flagged,
            top_genes=[DriftGene(gene_id=g.gene_id, psi=g.psi, flagged=g.flagged) for g in genes],
        )
