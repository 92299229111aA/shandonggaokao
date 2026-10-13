"""0009 — give the plan's per-school 专业序号 its own column.

Migration 0006 stored the Shandong plan file's 专业代码 in `special_requirements` because
adding a column was avoided at the time. That field is read by the rules engine as
unverified 招生章程 text, and any non-empty value makes eligibility UNKNOWN. Since every
one of the 73,198 loaded plans carried a sequence number there, every plan came back
UNKNOWN — and UNKNOWN does not count toward the 保底 requirement, so the safety bucket was
silently disabled for the whole dataset.

This adds `major_sequence` and clears the sequence numbers out of `special_requirements`,
leaving that column for genuine 招生章程 text only.

The data correction is expressed in SQL so it is reproducible on any database restored
from an earlier dump. It is intentionally narrow: only values matching the exact label the
loader wrote ('院校专业序号 …') are moved, so real requirement text is never touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_plan_major_sequence"
down_revision: str | None = "0008_recommendation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LABEL = "院校专业序号 "


def upgrade() -> None:
    op.add_column(
        "admission_plans",
        sa.Column("major_sequence", sa.String(8)),
        schema="admission",
    )

    # Move the wrongly-stored sequence numbers across, then clear the column. Rows whose
    # text is anything else keep it: those may be genuine 招生章程 requirements.
    op.execute(
        sa.text(
            """
            UPDATE admission.admission_plans
               SET major_sequence = substring(special_requirements from :cut),
                   special_requirements = NULL
             WHERE special_requirements LIKE :label
            """
        ).bindparams(cut=len(LABEL) + 1, label=f"{LABEL}%")
    )


def downgrade() -> None:
    # Put the label back before dropping the column so a downgrade is lossless.
    op.execute(
        sa.text(
            """
            UPDATE admission.admission_plans
               SET special_requirements = :label || major_sequence
             WHERE major_sequence IS NOT NULL
               AND special_requirements IS NULL
            """
        ).bindparams(label=LABEL)
    )
    op.drop_column("admission_plans", "major_sequence", schema="admission")
