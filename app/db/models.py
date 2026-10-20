"""Relational schema for prediction logging and drift monitoring.

    model_versions 1───* predictions 1───* prediction_genes
          │
          └──────────* drift_reports 1───* drift_report_genes

Every prediction is tied to the exact model version that produced it, and every
input gene value is stored as a row so drift can be computed per gene with SQL.
The schema is created and evolved only through Alembic migrations
(app/db/migrations); CI fails if these models and the migrations disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# BIGINT in Postgres, INTEGER in SQLite (so SQLite autoincrement still works in unit tests)
BigId = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    git_sha: Mapped[str] = mapped_column(String(40))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    roc_auc: Mapped[float] = mapped_column(Float)
    balanced_accuracy: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[float] = mapped_column(Float)
    specificity: Mapped[float] = mapped_column(Float)
    first_served_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    predictions: Mapped[list[Prediction]] = relationship(back_populates="model_version")
    drift_reports: Mapped[list[DriftReport]] = relationship(back_populates="model_version")


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    request_id: Mapped[str] = mapped_column(String(36), comment="groups samples sent in one API call")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    cancer_probability: Mapped[float] = mapped_column(Float)
    label: Mapped[int] = mapped_column(SmallInteger, comment="0 = healthy, 1 = cancer")
    latency_ms: Mapped[float] = mapped_column(Float)

    model_version: Mapped[ModelVersion] = relationship(back_populates="predictions")
    genes: Mapped[list[PredictionGene]] = relationship(back_populates="prediction", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_predictions_created_at", "created_at"),
        Index("ix_predictions_model_version_id", "model_version_id"),
    )


class PredictionGene(Base):
    __tablename__ = "prediction_genes"

    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True)
    gene_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    read_count: Mapped[float] = mapped_column(Float)

    prediction: Mapped[Prediction] = relationship(back_populates="genes")


class DriftReport(Base):
    __tablename__ = "drift_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_predictions: Mapped[int] = mapped_column(Integer)
    n_genes_flagged: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), comment="ok | drift | insufficient_data")

    model_version: Mapped[ModelVersion] = relationship(back_populates="drift_reports")
    genes: Mapped[list[DriftReportGene]] = relationship(back_populates="report", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_drift_reports_run_at", "run_at"),)


class DriftReportGene(Base):
    __tablename__ = "drift_report_genes"

    drift_report_id: Mapped[int] = mapped_column(ForeignKey("drift_reports.id", ondelete="CASCADE"), primary_key=True)
    gene_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    psi: Mapped[float] = mapped_column(Float, comment="population stability index vs. training data")
    flagged: Mapped[bool] = mapped_column(Boolean)

    report: Mapped[DriftReport] = relationship(back_populates="genes")
