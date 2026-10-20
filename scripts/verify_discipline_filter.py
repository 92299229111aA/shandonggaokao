"""Prove the 学科 filter now changes the preference score.

Before the major-catalogue mapping, `MajorSelector.discipline_code` could never match:
`program_series.major_id` was NULL everywhere, so no candidate carried a discipline and
selecting 工学 had literally no effect on the 35%-weighted 专业匹配 dimension.

This drives the real scorer (`PreferenceService.evaluate`) over real 2025 candidates,
twice: once with no subject preference and once preferring 工学, and checks that the
专业匹配 dimension actually separates the 工学 plans from the rest.

Run: python scripts/verify_discipline_filter.py
"""

from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.admission.models import AdmissionCycle, DatasetSnapshot  # noqa: E402
from app.admission.repository import PlanRepository  # noqa: E402
from app.common.enums import PreferenceDimension  # noqa: E402
from app.scoring.preference import (  # noqa: E402
    MajorSelector,
    PreferenceInput,
    PreferenceService,
    PreferenceWeights,
)

DISCIPLINE_CODE = "08"
DISCIPLINE_NAME = "工学"
SAMPLE = 4000


def to_input(row: dict) -> PreferenceInput:
    return PreferenceInput(
        major_id=row.get("major_id"),
        discipline_code=row.get("discipline_code"),
        major_names=(row.get("major_name") or "",),
        major_text=row.get("plan_name") or row.get("series_name") or "",
        interest_keywords=(),
        university_id=int(row.get("university_id") or 0),
        university_name=row.get("university_name") or "",
        university_traits={
            "is_985": bool(row.get("is_985")),
            "is_211": bool(row.get("is_211")),
            "is_double_first_class": bool(row.get("is_double_first_class")),
        },
        province_code=row.get("province_code"),
        city_code=row.get("city_code"),
        tuition_fee=int(row.get("tuition_fee") or 0),
        tuition_budget=None,
        tuition_floor=None,
        academic_score=None,
        career_score=None,
        personal_score=None,
    )


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        cycle = session.execute(
            select(AdmissionCycle).where(AdmissionCycle.exam_year == 2025)
        ).scalars().first()
        snapshot = session.execute(
            select(DatasetSnapshot).where(DatasetSnapshot.code == "SD-2022-2026")
        ).scalar_one()
        rows = PlanRepository(session).list_candidates(
            cycle_id=cycle.id, snapshot_id=snapshot.id, limit=SAMPLE, offset=0
        )

    candidates = [to_input(row) for row in rows]
    with_discipline = sum(1 for c in candidates if c.discipline_code)
    print(f"候选 {len(candidates):,}   带门类 {with_discipline:,}"
          f"（{with_discipline / len(candidates) * 100:.1f}%）")
    distribution = Counter(c.discipline_code for c in candidates if c.discipline_code)
    print(f"门类分布: {dict(distribution.most_common())}")
    print()

    service = PreferenceService()

    # Preference A: nothing specified — 专业匹配 has no selector, so it must not
    # discriminate (an unset dimension is dropped from the denominator, never scored 0).
    weights_neutral = PreferenceWeights()
    # Preference B: 工学 preferred.
    weights_discipline = PreferenceWeights(
        major_includes=(MajorSelector(discipline_code=DISCIPLINE_CODE, weight=100.0),)
    )

    print("=" * 100)
    print("同批候选，唯一差别是「是否偏好工学」")
    print("=" * 100)
    buckets: dict[str, list[float]] = {"工学": [], "其他": []}
    neutral_scores: list[float] = []
    discipline_scores: list[float] = []

    for candidate in candidates:
        neutral = service.evaluate(candidate=candidate, weights=weights_neutral)
        preferred = service.evaluate(candidate=candidate, weights=weights_discipline)
        neutral_scores.append(neutral.score)
        discipline_scores.append(preferred.score)

        dimension = next(
            (d for d in preferred.dimensions if d.dimension == PreferenceDimension.MAJOR.value),
            None,
        )
        key = "工学" if candidate.discipline_code == DISCIPLINE_CODE else "其他"
        if dimension is not None and dimension.available:
            buckets[key].append(dimension.score)

    print(f"  无偏好时 总分: 中位 {statistics.median(neutral_scores):.2f}")
    print(f"  偏好工学后 总分: 中位 {statistics.median(discipline_scores):.2f}")
    print()

    for label in ("工学", "其他"):
        values = buckets[label]
        if values:
            print(f"  {label:<4} 候选 {len(values):>5} 条   专业匹配分: "
                  f"中位 {statistics.median(values):.2f}   最小 {min(values):.2f}   最大 {max(values):.2f}")
        else:
            print(f"  {label:<4} 无数据")

    print()
    ok = True
    if not buckets["工学"]:
        print("  !! 没有任何候选带 08 门类 —— 映射未生效")
        ok = False
    if not buckets["其他"]:
        print("  !! 没有对照候选，无法判断")
        ok = False
    if buckets["工学"] and buckets["其他"]:
        major_median = statistics.median(buckets["工学"])
        other_median = statistics.median(buckets["其他"])
        gap = major_median - other_median
        print(f"  专业匹配分差值（工学 − 其他）= {gap:+.2f}")
        if gap <= 0:
            print("  !! 偏好工学并未提高工学候选的专业匹配分 —— 筛选仍未生效")
            ok = False
        else:
            print("  学科筛选已生效：偏好工学确实提高了工学候选的专业匹配分。")

    print()
    print("结论:", "通过" if ok else "失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
