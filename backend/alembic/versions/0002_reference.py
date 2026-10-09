"""0002 — reference schema: universities, campuses, majors (教育部专业目录).

`majors` is a dictionary, not admission data: it holds the 教育部 major catalogue
that 招生计划 rows point at. Real catalogues are loaded by the ETL in phase 9.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_reference"
down_revision: str | None = "0001_schemas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "universities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ministry_code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("short_name", sa.String(64)),
        sa.Column("province_code", sa.String(2), nullable=False),
        sa.Column("province_name", sa.String(32), nullable=False),
        sa.Column("city_code", sa.String(8)),
        sa.Column("city_name", sa.String(32)),
        sa.Column("is_985", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_211", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_double_first_class", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_public", sa.Boolean()),
        sa.Column("website", sa.String(255)),
        sa.Column("remark", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ministry_code", name="uq_universities_ministry_code"),
        sa.CheckConstraint("length(name) > 0", name="ck_universities_name_not_blank"),
        sa.CheckConstraint("length(province_code) = 2", name="ck_universities_province_code_len"),
        schema="reference",
    )
    op.create_index(
        "ix_universities_province_city", "universities", ["province_code", "city_code"], schema="reference"
    )

    op.create_table(
        "campuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "university_id",
            sa.Integer(),
            sa.ForeignKey("reference.universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("province_code", sa.String(2)),
        sa.Column("city_code", sa.String(8)),
        sa.Column("city_name", sa.String(32)),
        sa.Column("address", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("university_id", "name", name="uq_campuses_university_id_name"),
        schema="reference",
    )
    op.create_index("ix_campuses_university_id", "campuses", ["university_id"], schema="reference")

    op.create_table(
        "majors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        # MAJOR_CLASS = 专业类 (4-digit), MAJOR = 专业 (6-digit)
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("discipline_code", sa.String(2)),
        sa.Column("discipline_name", sa.String(32)),
        sa.Column("category_code", sa.String(4)),
        sa.Column("category_name", sa.String(64)),
        sa.Column("degree_awarded", sa.String(32)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_majors_code"),
        sa.CheckConstraint("level in ('MAJOR_CLASS','MAJOR')", name="ck_majors_level_valid"),
        schema="reference",
    )
    op.create_index("ix_majors_discipline_code", "majors", ["discipline_code"], schema="reference")


def downgrade() -> None:
    op.drop_table("majors", schema="reference")
    op.drop_table("campuses", schema="reference")
    op.drop_table("universities", schema="reference")
