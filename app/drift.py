"""Scheduled drift check: are recent inputs still distributed like the training data?

For every gene, recent read counts from the prediction_genes table are binned with
the training-time bin edges (reference_stats.json) and compared using the
Population Stability Index:

    PSI = sum over bins of (actual% - expected%) * ln(actual% / expected%)

Rule of thumb: PSI < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 major shift.
The report and the per-gene scores are written to drift_reports /
drift_report_genes, and a DRIFT_DETECTED log line is emitted for alerting.

Runs as an Azure Container Apps scheduled job and as a Kubernetes CronJob:
    python -m app.drift
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db.models import DriftReport, DriftReportGene, ModelVersion, Prediction, PredictionGene
from .db.session import database_url, get_sessionmaker

log = logging.getLogger("liquid-biopsy-api.drift")

EPS = 1e-4


@dataclass
class DriftSettings:
    window_days: int = int(os.environ.get("DRIFT_WINDOW_DAYS", 7))
    min_predictions: int = int(os.environ.get("DRIFT_MIN_PREDICTIONS", 30))
    psi_threshold: float = float(os.environ.get("DRIFT_PSI_THRESHOLD", 0.25))
    # The report is "drift" when more than this share of genes is flagged
    max_flagged_share: float = float(os.environ.get("DRIFT_MAX_FLAGGED_SHARE", 0.10))


def psi(reference: dict, values: np.ndarray) -> float:
    x = np.log1p(np.asarray(values, dtype=float))
    edges = np.asarray(reference["edges"], dtype=float)
    expected = np.clip(np.asarray(reference["expected"], dtype=float), EPS, None)
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=len(expected))
    actual = np.clip(counts / max(len(x), 1), EPS, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def run_drift_check(
    session: Session,
    reference: dict,
    model_version_id: int,
    settings: DriftSettings | None = None,
    now: datetime | None = None,
) -> DriftReport:
    settings = settings or DriftSettings()
    window_end = now or datetime.now(UTC)
    window_start = window_end - timedelta(days=settings.window_days)

    in_window = (Prediction.created_at >= window_start) & (Prediction.created_at <= window_end)
    n_predictions = session.scalar(select(func.count(Prediction.id)).where(in_window)) or 0

    report = DriftReport(
        model_version_id=model_version_id,
        run_at=window_end,
        window_start=window_start,
        window_end=window_end,
        n_predictions=int(n_predictions),
        n_genes_flagged=0,
        status="insufficient_data",
    )

    if n_predictions >= settings.min_predictions:
        rows = session.execute(
            select(PredictionGene.gene_id, PredictionGene.read_count)
            .join(Prediction, Prediction.id == PredictionGene.prediction_id)
            .where(in_window)
        ).all()
        by_gene: dict[str, list[float]] = {}
        for gene_id, count in rows:
            by_gene.setdefault(gene_id, []).append(count)

        for gene_id, ref in reference["genes"].items():
            values = by_gene.get(gene_id)
            if not values:
                continue
            score = psi(ref, np.asarray(values))
            report.genes.append(
                DriftReportGene(gene_id=gene_id, psi=round(score, 6), flagged=score > settings.psi_threshold)
            )
        report.n_genes_flagged = sum(g.flagged for g in report.genes)
        flagged_share = report.n_genes_flagged / max(len(reference["genes"]), 1)
        report.status = "drift" if flagged_share > settings.max_flagged_share else "ok"

    session.add(report)
    session.commit()
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    url = database_url()
    if not url:
        log.error("DATABASE_URL is not set")
        return 1

    model_dir = Path(os.environ.get("MODEL_DIR", Path(__file__).resolve().parents[1] / "models"))
    reference = json.loads((model_dir / "reference_stats.json").read_text())
    metadata = json.loads((model_dir / "metadata.json").read_text())

    SessionLocal = get_sessionmaker(url)
    with SessionLocal() as session:
        mv = session.scalar(select(ModelVersion).where(ModelVersion.version == metadata["model_version"]))
        if mv is None:
            log.warning("Model %s has not served any predictions yet; nothing to check", metadata["model_version"])
            return 0
        report = run_drift_check(session, reference, mv.id)
        top = sorted(report.genes, key=lambda g: g.psi, reverse=True)[:5]
        summary = {
            "report_id": report.id,
            "status": report.status,
            "model_version": metadata["model_version"],
            "n_predictions": report.n_predictions,
            "n_genes_flagged": report.n_genes_flagged,
            "top_genes": [{"gene": g.gene_id, "psi": g.psi} for g in top],
        }
    if report.status == "drift":
        log.warning("DRIFT_DETECTED %s", json.dumps(summary))
    else:
        log.info("drift_check %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
