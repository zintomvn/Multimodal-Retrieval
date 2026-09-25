"""Persist DRES submission receipts to prevent duplicate answers.

Revision ID: 20260925_0002
Revises: 20260629_0001
Create Date: 2026-09-25 11:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260925_0002"
down_revision = "20260629_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dres_submissions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("evaluation_id", sa.String(length=128), nullable=False),
        sa.Column("evaluation_name", sa.String(length=255), nullable=False),
        sa.Column("media_item_name", sa.String(length=64), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "evaluation_id",
            "media_item_name",
            "timestamp_ms",
            name="uq_dres_submission_answer",
        ),
    )


def downgrade() -> None:
    op.drop_table("dres_submissions")
