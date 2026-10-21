"""Build 专业名称 → 门类/专业类 from the structured catalogue, and measure the match rate.

Why names and not codes: the Shandong plan files carry a per-school major sequence number
(2 characters, e.g. 'E1'), not the MOE 6-digit code, so names are the only join key —
already established by scripts/audit_major_codes.py.

This reports the match rate before anything is written. A low rate would mean the mapping
is guesswork and not worth loading.

Levels in the catalogue:
    门类      哲学 / 经济学 / 法学 … (12 values in the 2023 edition)
    专业类    哲学类 / 经济学类 …    (the 4-digit prefix)
    专业      哲学 010101 / 逻辑学 010102 …

Note that 专业类 names appear both as standalone rows (e.g. 计算机类) and as a column on
programme rows, so both programme names and class names can be looked up.

Run: python scripts/probe_major_mapping.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.admission.models import AdmissionPlan, ProgramSeries  # noqa: E402
from app.reference.models import Major  # noqa: E402

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")
CATALOGUE_FRAGMENT = "绝密报考-普通高等学校本科专业目录（2023年完整版）"

# Column positions in the catalogue sheet (row 1 is the header).
COL_DISCIPLINE = 2  # 门类
COL_CLASS = 3  # 专业类
COL_CODE = 5  # 专业代码
COL_NAME = 6  # 专业名称

DISCIPLINE_CODE_BY_NAME = {
    "哲学": "01", "经济学": "02", "法学": "03", "教育学": "04", "文学": "05",
    "历史学": "06", "理学": "07", "工学": "08", "农学": "09", "医学": "10",
    "军事学": "11", "管理学": "12", "艺术学": "13", "交叉学科": "14",
}


def find_catalogue() -> Path:
    paths = sorted(
        p for p in NEW_ROOT.rglob("*.xlsx") if CATALOGUE_FRAGMENT in p.name
    )
    if not paths:
        raise SystemExit(f"找不到 {CATALOGUE_FRAGMENT}")
    return paths[0]


def parse_catalogue(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Return (by programme name, by major-class name)."""
    frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    by_name: dict[str, dict[str, str]] = {}
    by_class: dict[str, dict[str, str]] = {}

    current_discipline: str | None = None
    for index in range(len(frame)):
        discipline = _text(frame.iat[index, COL_DISCIPLINE])
        major_class = _text(frame.iat[index, COL_CLASS])
        code = _text(frame.iat[index, COL_CODE])
        name = _text(frame.iat[index, COL_NAME])

        if discipline:
            current_discipline = discipline
        if not name or name in {"专业名称", "专业"}:
            continue

        entry = {
            "discipline_name": current_discipline or "",
            "discipline_code": DISCIPLINE_CODE_BY_NAME.get(current_discipline or "", ""),
            "class_name": major_class or "",
            "class_code": code[:4] if code and len(code) >= 4 else "",
            "major_code": code or "",
        }
        by_name[name] = entry
        if major_class and major_class.endswith("类"):
            by_class[major_class] = {**entry, "class_code": code[:4] if code and len(code) >= 4 else ""}
    return by_name, by_class


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", "").strip()
    return None if not text or text.lower() == "nan" else text


def main() -> int:
    path = find_catalogue()
    print(f"目录文件: {path.name}")
    print(f"           {path.relative_to(NEW_ROOT)}")
    by_name, by_class = parse_catalogue(path)
    print(f"解析出 专业名称 {len(by_name):,} 条，专业类名称 {len(by_class):,} 条")
    disciplines = sorted({e["discipline_name"] for e in by_name.values() if e["discipline_name"]})
    print(f"覆盖门类 {len(disciplines)} 个: {disciplines}")
    print()

    # ---- match against the majors we actually store ----
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stored = list(session.execute(select(Major.id, Major.code, Major.name)).all())
        series_names = list(
            session.execute(select(ProgramSeries.canonical_name).distinct()).scalars()
        )

    matched_major: list[tuple[int, str, dict[str, str]]] = []
    unmatched_major: list[str] = []
    for major_id, _code, name in stored:
        entry = by_name.get(name) or by_class.get(name)
        if entry:
            matched_major.append((major_id, name, entry))
        else:
            unmatched_major.append(name)

    print("=" * 100)
    print("reference.majors 匹配情况")
    print("=" * 100)
    rate = len(matched_major) / len(stored) * 100 if stored else 0
    print(f"  专业共 {len(stored):,}   匹配 {len(matched_major):,}（{rate:.1f}%）  未匹配 {len(unmatched_major):,}")
    print()
    print("  未匹配样例:", unmatched_major[:20])
    print()

    from collections import Counter

    distribution = Counter(e["discipline_name"] for _i, _n, e in matched_major)
    print("  匹配后的门类分布:")
    for name, count in distribution.most_common():
        print(f"    {name or '<空>':<10} {count:>5}")

    print()
    print("=" * 100)
    print("program_series 匹配情况（决定学科筛选能否生效）")
    print("=" * 100)
    series_matched = sum(1 for n in series_names if n in by_name or n in by_class)
    print(f"  专业序列名称 {len(series_names):,}   能匹配到目录 {series_matched:,}"
          f"（{series_matched / len(series_names) * 100:.1f}%）")
    sample_unmatched = [n for n in series_names if n not in by_name and n not in by_class][:15]
    print("  未匹配样例:", sample_unmatched)

    # How many real plans would gain a discipline through that link?
    print()
    with Session(engine) as session:
        total_plans = session.execute(
            select(AdmissionPlan.id).where(AdmissionPlan.is_demo.is_(False))
        ).scalars().all()
    print(f"  非 DEMO 计划总数 {len(total_plans):,}")
    print(f"  → 若按名称建立 major_id 关联，可覆盖的计划比例上限 ≈ "
          f"{series_matched / len(series_names) * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
