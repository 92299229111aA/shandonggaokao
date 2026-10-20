"""Probe the 2026 material before writing a loader: shapes, line, and match rate.

Two things must be measured first, because both decide whether importing is worthwhile:

  1. **Series match rate.** 2026 rows attach to existing cross-year series by
     (院校代码, 专业基名). If they do not match, the outcomes are orphaned rows.
  2. **The 一段 line.** `exam_year_stats.ranked_candidates` is the 一分一段 cumulative
     count *at the 一段线*, and it is the denominator for every rank percentile. The 2026
     files do not state the line, so it must be found in the data or reported as unknown
     rather than invented.

Read-only. Run: python scripts/probe_2026.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.admission.models import AdmissionOutcome, ProgramSeries  # noqa: E402
from app.ingestion.etl_shandong import base_name  # noqa: E402
from app.reference.models import University  # noqa: E402

ROOT = Path(os.environ.get("GAOKAO_DATA_ROOT", Path.cwd())) / "2026"
SCORE_RANK = ROOT / "2026一分一段表.xls"
ROUND1 = ROOT / "山东省2026年普通类常规批第1次志愿投档情况表.xls"

# "17 文科试验班类(不限选考科目类专业)" → seq + name
MAJOR_RE = re.compile(r"^(\S{1,4})\s+(.+)$")
# "A001北京大学" → code + name
UNIVERSITY_RE = re.compile(r"^([A-Z]\d{2,4})(.+)$")


def parse_score_rank() -> None:
    print("=" * 100)
    print("2026 一分一段表")
    print("=" * 100)
    frame = pd.read_excel(SCORE_RANK, sheet_name=0, header=None, dtype=str)
    print(f"  原始 shape {frame.shape}")
    header = frame.iloc[2].tolist()
    print(f"  列头（第 3 行）: {[None if pd.isna(v) else str(v) for v in header][:6]}")

    # 分数段 | 全体本段 | 全体累计 | 选考物理本段 | ...
    data = frame.iloc[3:].copy()
    data.columns = range(frame.shape[1])
    scores, same, cumulative = [], [], []
    for _, row in data.iterrows():
        raw_score = row[0]
        if pd.isna(raw_score):
            continue
        try:
            score = int(float(str(raw_score)))
        except ValueError:
            continue
        scores.append(score)
        same.append(_int_or_none(row[1]))
        cumulative.append(_int_or_none(row[2]))

    print(f"  解析行数 {len(scores)}")
    print(f"  分数范围 {max(scores)} ~ {min(scores)}")
    print(f"  最高分累计 {cumulative[0]:,}   最低分累计 {cumulative[-1]:,}")

    print()
    print("  累计人数在若干分数上的取值（用于定位一段线）:")
    for target in (441, 445, 450, 460, 470, 480, 490, 500):
        match = [(s, c) for s, c in zip(scores, cumulative) if s == target]
        if match:
            print(f"    分数 {target}: 累计 {match[0][1]:,}")

    print()
    print("  最后 5 行（看表格是否到本科线就结束）:")
    for score, count, cum in list(zip(scores, same, cumulative))[-5:]:
        print(f"    {score}  本段 {count}  累计 {cum:,}")


def _int_or_none(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def parse_round1() -> None:
    print()
    print("=" * 100)
    print("2026 普通类常规批第 1 次志愿投档情况表")
    print("=" * 100)
    frame = pd.read_excel(ROUND1, sheet_name=0, header=None, dtype=str)
    print(f"  原始 shape {frame.shape}")
    print(f"  表头（第 2 行）: {[str(v) for v in frame.iloc[1].tolist()]}")

    rows = []
    bad_major = 0
    bad_university = 0
    bad_rank = 0
    for index in range(2, len(frame)):
        major_raw = frame.iat[index, 0]
        university_raw = frame.iat[index, 1]
        requirement = frame.iat[index, 2]
        rank_raw = frame.iat[index, 3]
        if pd.isna(major_raw) or pd.isna(university_raw):
            continue

        major_match = MAJOR_RE.match(str(major_raw).replace("\n", "").strip())
        if not major_match:
            bad_major += 1
            continue
        university_match = UNIVERSITY_RE.match(str(university_raw).replace("\n", "").strip())
        if not university_match:
            bad_university += 1
            continue
        rank = _int_or_none(rank_raw)
        if rank is None:
            bad_rank += 1
            continue

        rows.append(
            {
                "university_code": university_match.group(1),
                "university_name": university_match.group(2).strip(),
                "major_seq": major_match.group(1),
                "major_name": major_match.group(2).strip(),
                "major_base": base_name(major_match.group(2)),
                "requirement": None if pd.isna(requirement) else str(requirement).strip(),
                "min_rank": rank,
            }
        )

    print(f"  解析成功 {len(rows):,} 行")
    print(f"  无法解析: 专业 {bad_major}, 院校 {bad_university}, 位次 {bad_rank}")
    print()

    print("  样例 5 行:")
    for row in rows[:5]:
        print(
            f"    {row['university_code']} {row['university_name'][:12]:<14}"
            f"{row['major_seq']:>3} {row['major_base'][:16]:<18}"
            f"选科={row['requirement'] or '—':<10} 位次={row['min_rank']:,}"
        )

    print()
    print(f"  最低位次范围 {min(r['min_rank'] for r in rows):,} ~ {max(r['min_rank'] for r in rows):,}")
    print(f"  院校数 {len({r['university_code'] for r in rows}):,}")
    print(f"  专业基名数 {len({r['major_base'] for r in rows}):,}")
    print()
    print("  选考科目要求取值（前 10）:")
    for value, count in Counter(r["requirement"] for r in rows).most_common(10):
        print(f"    {value!r:<28} {count:>7,}")

    # ---- match against the series we already store ----
    print()
    print("=" * 100)
    print("与现有专业序列的匹配率（决定 2026 能否作为第 5 年历史）")
    print("=" * 100)
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        existing = session.execute(
            select(ProgramSeries.series_key, ProgramSeries.id, University.name)
            .join(University, University.id == ProgramSeries.university_id)
        ).all()

        # The loader matches on (university_code, major_base), derived from series_key.
        by_code_base: dict[tuple[str, str], int] = {}
        for key, series_id, _name in existing:
            parts = key.split("|")
            if len(parts) >= 3:
                by_code_base.setdefault((parts[0], parts[2]), series_id)

        # university code → canonical name, to detect code/name disagreements in 2026.
        code_to_names: dict[str, set[str]] = {}
        for key, _series_id, name in existing:
            code_to_names.setdefault(key.split("|")[0], set()).add(name)

        matched = [r for r in rows if (r["university_code"], r["major_base"]) in by_code_base]
        print(f"  2026 行 {len(rows):,}")
        print(f"  可匹配到现有序列 {len(matched):,}（{len(matched) / len(rows) * 100:.1f}%）")
        print(f"  无法匹配 {len(rows) - len(matched):,}")

        unknown_codes = {
            r["university_code"] for r in rows if r["university_code"] not in code_to_names
        }
        print()
        print(f"  2026 出现的院校代码里，现有数据没有的: {len(unknown_codes)} 个")
        if unknown_codes:
            print(f"    例如 {sorted(unknown_codes)[:10]}")

        mismatched_names = Counter()
        for row in rows:
            names = code_to_names.get(row["university_code"])
            if names and row["university_name"] not in names:
                mismatched_names[(row["university_name"], tuple(sorted(names))[:1])] += 1
        if mismatched_names:
            print()
            print("  同代码但校名不一致（前 5）:")
            for (name, existing_names), count in mismatched_names.most_common(5):
                print(f"    2026={name!r}  现有={existing_names}  出现 {count} 次")

    # Does 2026 duplicate an existing year?
    with Session(engine) as session:
        years = dict(
            session.execute(
                select(AdmissionOutcome.exam_year, func_count())
                .group_by(AdmissionOutcome.exam_year)
                .order_by(AdmissionOutcome.exam_year)
            ).all()
        )
    print()
    print(f"  现有 outcomes 年份分布: {years}")


def func_count():
    from sqlalchemy import func

    return func.count()


if __name__ == "__main__":
    parse_score_rank()
    parse_round1()
