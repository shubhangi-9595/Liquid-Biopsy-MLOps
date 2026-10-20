"""Initial schema: model versions, predictions, per-gene inputs, drift reports.

Revision ID: 0001
Revises:
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("git_sha", sa.String(40), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("roc_auc", sa.Float(), nullable=False),
        sa.Column("balanced_accuracy", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.Float(), nullable=False),
        sa.Column("specificity", sa.Float(), nullable=False),
        sa.Column("first_served_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "predictions",
        sa.Column("id", BigId, primary_key=True),
        sa.Column("model_version_id", sa.Integer(), sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False, comment="groups samples sent in one API call"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancer_probability", sa.Float(), nullable=False),
        sa.Column("label", sa.SmallInteger(), nullable=False, comment="0 = healthy, 1 = cancer"),
        sa.Column("latency_ms", sa.Float(), nullable=False),
    )
    op.create_index("ix_predictions_created_at", "predictions", ["created_at"])
    op.create_index("ix_predictions_model_version_id", "predictions", ["model_version_id"])

    op.create_table(
        "prediction_genes",
        sa.Column(
            "prediction_id",
            BigId,
            sa.ForeignKey("predictions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("gene_id", sa.String(20), primary_key=True),
        sa.Column("read_count", sa.Float(), nullable=False),
    )

    op.create_table(
        "drift_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_version_id", sa.Integer(), sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_predictions", sa.Integer(), nullable=False),
        sa.Column("n_genes_flagged", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, comment="ok | drift | insufficient_data"),
    )
    op.create_index("ix_drift_reports_run_at", "drift_reports", ["run_at"])

    op.create_table(
        "drift_report_genes",
        sa.Column(
            "drift_report_id",
            sa.Integer(),
            sa.ForeignKey("drift_reports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("gene_id", sa.String(20), primary_key=True),
        sa.Column("psi", sa.Float(), nullable=False, comment="population stability index vs. training data"),
        sa.Column("flagged", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("drift_report_genes")
    op.drop_index("ix_drift_reports_run_at", table_name="drift_reports")
    op.drop_table("drift_reports")
    op.drop_table("prediction_genes")
    op.drop_index("ix_predictions_model_version_id", table_name="predictions")
    op.drop_index("ix_predictions_created_at", table_name="predictions")
    op.drop_table("predictions")
    op.drop_table("model_versions")
