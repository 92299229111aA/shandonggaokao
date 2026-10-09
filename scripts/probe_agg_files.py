"""Final structural checks before building the real ETL.

1. Does the aggregated plan file really cover 2022-2025 for the regular batch?
2. Can we join it to the cut-off file on 院校代码 + 专业代码?
3. Where do 中外合作 / 民办 flags live (needed for hard-exclude preferences)?
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
SCORE_FILE = next(p for p in AGG.rglob("*.xlsx") if "22-25年全国高校在山东的专业录取分数" in p.name)
UNI_FILE = next(p for p in AGG.rglob("*.xlsx") if "22-25年全国高校在山东的院校录取分数" in p.name)

print("=" * 110)
print("1) 招生计划：年份 x 批次 覆盖情况")
print("=" * 110)
plans = pd.read_excel(PLAN_FILE, dtype=str)
print(f"总行数 {len(plans):,}")
cross = pd.crosstab(plans["年份"], plans["批次"])
print(cross.to_string())
print()

regular = plans[plans["批次"].astype(str).str.contains("普通类一段", na=False)]
print(f"普通类一段 行数: {len(regular):,}")
print("普通类一段 按年份:")
print(regular["年份"].value_counts().sort_index().to_string())
print()
print(f"专业代码 空值: {regular['专业代码'].isna().sum():,}")
print(f"选科要求 空值: {regular['选科要求'].isna().sum():,}")
print(f"院校代码 空值: {regular['院校代码'].isna().sum():,}")
print()

# Cross-year code stability: does the same (院校代码, 专业代码) recur across years?
print("=" * 110)
print("2) 跨年代码稳定性（决定 program_series 能否用官方代码做自然键）")
print("=" * 110)
key = regular[["院校代码", "专业代码", "年份"]].dropna()
per_year = {year: set(map(tuple, group[["院校代码", "专业代码"]].values)) for year, group in key.groupby("年份")}
years = sorted(per_year)
for year in years:
    print(f"  {year}: {len(per_year[year]):,} 个 (院校代码,专业代码) 组合")
if len(years) >= 2:
    for a, b in zip(years, years[1:], strict=False):
        inter = len(per_year[a] & per_year[b])
        print(f"  {a} ∩ {b} = {inter:,}  （{b} 年的 {inter / max(1, len(per_year[b])) * 100:.1f}% 可在 {a} 年找到同代码）")
print()

print("=" * 110)
print("3) 录取分数文件：位次是否齐备")
print("=" * 110)
scores = pd.read_excel(SCORE_FILE, dtype=str)
print(f"总行数 {len(scores):,}  columns={list(scores.columns)}")
score_regular = scores[scores["批次"].astype(str).str.contains("普通类一段", na=False)]
print(f"普通类一段 行数: {len(score_regular):,}")
print("按年份:")
print(score_regular["年份"].value_counts().sort_index().to_string())
print(f"最低位次 空值: {score_regular['最低位次'].isna().sum():,}")
print()

# Join feasibility
print("=" * 110)
print("4) 计划 ↔ 录取 的连接可行性（院校代码 + 专业代码）")
print("=" * 110)
p25 = set(map(tuple, regular[regular["年份"] == "2025"][["院校代码", "专业代码"]].dropna().values))
s25 = set(map(tuple, score_regular[score_regular["年份"] == "2025"][["院校代码", "专业代码"]].dropna().values))
print(f"2025 计划组合: {len(p25):,}")
print(f"2025 录取组合: {len(s25):,}")
print(f"交集: {len(p25 & s25):,}  （计划侧命中率 {len(p25 & s25) / max(1, len(p25)) * 100:.1f}%）")
print()

print("=" * 110)
print("5) 院校属性：公办/民办、中外合作、985/211")
print("=" * 110)
unis = pd.read_excel(UNI_FILE, dtype=str)
print(f"院校录取分数文件 columns={list(unis.columns)}")
if "学校性质" in unis.columns:
    print("学校性质 分布:", unis["学校性质"].value_counts().to_dict())
for column in ("是否985", "是否211", "学校所在"):
    if column in unis.columns:
        values = unis[column].dropna()
        print(f"{column}: 去重 {values.nunique()}，样例 {list(values.unique()[:6])}")
print()
print("招生类型（计划文件，可识别中外合作/地方专项）:")
print(regular["招生类型"].value_counts().head(12).to_string())
