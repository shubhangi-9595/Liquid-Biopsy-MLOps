"""Request / response models for the prediction API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    samples: list[dict[str, float]] = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "One object per sample, mapping Ensembl gene ID -> raw read count for every gene listed by GET /model-info."
        ),
    )


class Prediction(BaseModel):
    label: int = Field(..., description="0 = healthy, 1 = cancer")
    label_name: str
    cancer_probability: float = Field(..., ge=0, le=1)
    prediction_id: int | None = Field(None, description="Row ID in the predictions table (when a database is set)")


class PredictResponse(BaseModel):
    model_version: str
    request_id: str
    predictions: list[Prediction]
    ignored_genes: list[str] = Field(default_factory=list, description="Genes sent that the model does not use")


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str | None = None
    database: str = Field(..., description="connected | not_configured | error")


class ModelVersionSummary(BaseModel):
    model_version: str
    git_sha: str
    test_roc_auc: float
    n_predictions: int
    share_predicted_cancer: float | None
    avg_cancer_probability: float | None
    avg_latency_ms: float | None
    last_prediction_at: datetime | None


class DriftGene(BaseModel):
    gene_id: str
    psi: float
    flagged: bool


class DriftReportOut(BaseModel):
    report_id: int
    model_version: str
    run_at: datetime
    window_start: datetime
    window_end: datetime
    status: str
    n_predictions: int
    n_genes_flagged: int
    top_genes: list[DriftGene]
