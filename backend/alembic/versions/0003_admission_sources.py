"""0003 — admission provenance: source_documents and dataset_snapshots.

Nothing downstream may exist without a traceable source. `checksum_sha256` is
unique so the same official file cannot be ingested twice under two identities.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_admission_sources"
down_revision: str | None = "0002_reference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("publisher", sa.String(128)),
        sa.Column("url", sa.String(512)),
        sa.Column("archive_path", sa.String(512)),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("published_on", sa.Date()),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("checksum_sha256", name="uq_source_documents_checksum_sha256"),
        sa.CheckConstraint(
            "quality in ('OFFICIAL','UNIVERSITY','THIRD_PARTY','DEMO')",
            name="ck_source_documents_quality_valid",
        ),
        schema="admission",
    )

    op.create_table(
        "dataset_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_dataset_snapshots_code"),
        sa.CheckConstraint(
            "status in ('DRAFT','VALIDATING','PUBLISHED','RETIRED')",
            name="ck_dataset_snapshots_status_valid",
        ),
        schema="admission",
    )
    op.create_index(
        "ix_dataset_snapshots_status_published_at",
        "dataset_snapshots",
        ["status", "published_at"],
        schema="admission",
    )


def downgrade() -> None:
    op.drop_table("dataset_snapshots", schema="admission")
    op.drop_table("source_documents", schema="admission")
