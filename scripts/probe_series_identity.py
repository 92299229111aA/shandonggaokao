"""Decide how Program Series should align across years: official codes or names?

The 2022-2025 aggregate shows only ~57% of (院校代码, 专业代码) pairs recur the next
year, which suggests codes are not a stable identity. But that number counts plans,
not programmes — a code can disappear and reappear, and names carry qualifiers that
drift. This measures both, so the choice is made on evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

def _layout():
    """Resolve the source-data directory from --data-root or GAOKAO_DATA_ROOT.

    The workbooks come from a non-redistributable compilation, so their location is
    never hardcoded. See docs/DATA_IMPORT.md.
    """
    import argparse

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.ingestion.data_root import resolve_root

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="官方源文件目录（或用 GAOKAO_DATA_ROOT）")
    return resolve_root(parser.parse_known_args()[0].data_root)


AGG = _layout().aggregate_dir
PLAN_FILE = next(p for p in AGG.rglob("*.xlsx") if "22-25年全国高校在山东的招生计划" in p.name)


def keys(frame: pd.DataFrame, columns: list[str]) -> set[tuple]:
    subset = frame[columns].dropna()
    return set(map(tuple, subset.values))


def main() -> int:
    plans = pd.read_excel(PLAN_FILE, dtype=str)
    regular = plans[plans["批次"].astype(str).str.contains("普通类一段", na=False)].copy()
    regular = regular[regular["院校代码"].notna() & regular["专业代码"].notna()]

    # Normalise the major name the way a human would: drop the qualifier in
    # parentheses, which is where 「(中外合作办学)」 etc. drifts year to year.
    regular["专业基名"] = (
        regular["专业名称"].astype(str).str.split(r"[（(]").str[0].str.strip()
    )
    regular["院校基名"] = (
        regular["院校名称"].astype(str).str.split(r"[（(]").str[0].str.strip()
    )

    years = sorted(regular["年份"].dropna().unique())
    print(f"普通类一段，有效行 {len(regular):,}，年份 {years}")
    print()

    by_year = {year: regular[regular["年份"] == year] for year in years}

    code_sets = {y: keys(by_year[y], ["院校代码", "专业代码"]) for y in years}
    name_sets = {y: keys(by_year[y], ["院校基名", "专业基名"]) for y in years}

    print(f"{'对比':<14}{'代码键':>28}{'名称键':>28}")
    print("-" * 72)
    for a, b in zip(years, years[1:], strict=False):
        code_inter = len(code_sets[a] & code_sets[b])
        name_inter = len(name_sets[a] & name_sets[b])
        code_pct = code_inter / max(1, len(code_sets[b])) * 100
        name_pct = name_inter / max(1, len(name_sets[b])) * 100
        print(
            f"{a}->{b:<9}{code_inter:>10,} / {len(code_sets[b]):>6,} = {code_pct:>5.1f}%"
            f"{name_inter:>10,} / {len(name_sets[b]):>6,} = {name_pct:>5.1f}%"
        )
    print()

    # How many programmes are reachable with a 3-year chain in either scheme?
    def chain(reference: str) -> None:
        sets = code_sets if reference == "代码" else name_sets
        if len(years) < 3:
            return
        a, b, c = years[-3], years[-2], years[-1]
        triple = sets[a] & sets[b] & sets[c]
        pairs = (sets[a] & sets[b]) | (sets[b] & sets[c]) | (sets[a] & sets[c])
        print(
            f"  {reference}键：三年连续可对齐 {len(triple):,} 个；"
            f"任意两年可对齐 {len(pairs):,} 个（占最新年 {len(sets[c]):,} 的 {len(pairs) / max(1, len(sets[c])) * 100:.1f}%）"
        )

    print("2023-2025 三年连续覆盖情况：")
    chain("代码")
    chain("名称")
    print()

    # Secondary check: when the code matches, does the name match too?
    common = code_sets["2024"] & code_sets["2025"]
    names_24 = keys(by_year["2024"], ["院校代码", "专业代码", "专业基名"])
    names_25 = keys(by_year["2025"], ["院校代码", "专业代码", "专业基名"])
    same_name = {k for k in (names_24 & names_25)}
    print(f"2024∩2025 代码相同 {len(common):,} 个，其中专业基名也相同 {len(same_name):,} 个 "
          f"（{len(same_name) / max(1, len(common)) * 100:.1f}%）—— 代码相同但名称变了说明是专业更名")
    return 0


if __name__ == "__main__":
    sys.exit(main())
