"""Cross-check the newly supplied 2025 一分一段表 against the ingested one.

The 一段 population is the denominator for every rank percentile, so it is worth an
independent confirmation. The new file uses the official two-row header form
(分数段 / 全体 → 本段人数 / 累计人数) rather than the flattened single-header form that
was ingested, which makes it a genuine independent source for the same number.

Run: python scripts/verify_yifenyiduan.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.admission.models import ExamYearStats, ScoreRankSegment  # noqa: E402

NEW_TABLE = Path(
    r"D:\BaiduNetdiskDownload\志愿高报资料\11\25-26嗨写志愿高报资料汇总"
    r"\26最新高考志Y系列资料\26志愿高报资料汇总\26嗨写志愿高考填报相关文件汇总"
    r"\全国各省一分一段表（25年）\山东\2025年夏季高考文化成绩一分一段表.xls"
)
# 2025 普通类一段 control line, from the ingested table.
FIRST_TIER_LINE = 441


def read_official_form(path: Path) -> list[tuple[int, int, int]]:
    """Return [(score, same_score_count, cumulative)] for the 一段 range."""
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str, engine="xlrd")
    # Row 0 is the title, row 1 names the groups, row 2 names the sub-columns.
    rows: list[tuple[int, int, int]] = []
    for index in range(3, len(frame)):
        raw_score = str(frame.iat[index, 0]).strip()
        if not raw_score or raw_score.lower() == "nan":
            continue
        try:
            score = int(float(raw_score.split("-")[0]))
        except ValueError:
            continue
        same = frame.iat[index, 1]
        cumulative = frame.iat[index, 2]
        try:
            same_n = int(float(str(same).replace(",", "")))
            cumulative_n = int(float(str(cumulative).replace(",", "")))
        except ValueError:
            continue
        rows.append((score, same_n, cumulative_n))
    return rows


def main() -> int:
    print("=" * 100)
    print("2025 一分一段表：新资料（官方表头） vs 已接入")
    print("=" * 100)
    if not NEW_TABLE.exists():
        print(f"  找不到 {NEW_TABLE}")
        return 1

    official = read_official_form(NEW_TABLE)
    print(f"  新文件解析出 {len(official):,} 行")
    above_line = [r for r in official if r[0] >= FIRST_TIER_LINE]
    new_population = above_line[-1][2] if above_line else None
    print(f"  最高分 {official[0][0]}  最低分 {official[-1][0]}")
    print(f"  一段线 {FIRST_TIER_LINE} 分的累计人数 = {new_population:,}")
    print()

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stored_population = session.execute(
            select(ExamYearStats.ranked_candidates).where(
                ExamYearStats.exam_year == 2025,
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        stored_rows = {
            score: (same, cumulative)
            for score, same, cumulative in session.execute(
                select(
                    ScoreRankSegment.score,
                    ScoreRankSegment.same_score_count,
                    ScoreRankSegment.cumulative_rank,
                ).where(ScoreRankSegment.exam_year == 2025)
            ).all()
        }

    print(f"  数据库中的 2025 一段达线人数 = {stored_population:,}")
    print(f"  与独立来源一致: {new_population == stored_population}")
    print()

    mismatches = [
        (score, same, cumulative, stored_rows.get(score))
        for score, same, cumulative in above_line
        if stored_rows.get(score) != (same, cumulative)
    ]
    print(f"  逐行比对（一段范围 {len(above_line):,} 行）：")
    print(f"    不一致 {len(mismatches):,} 行")
    for score, same, cumulative, stored in mismatches[:10]:
        print(f"      分数 {score}: 新资料 ({same}, {cumulative})  数据库 {stored}")
    print()
    if not mismatches and new_population == stored_population:
        print("结论：新一分一段表与已接入的数据完全一致，是重复来源。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
