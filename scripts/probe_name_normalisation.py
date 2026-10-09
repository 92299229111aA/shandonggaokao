"""Does normalising the qualifier away improve cross-year name alignment?

Real Shandong major names carry qualifiers that drift between years:
    「临床医学(八年制本博连读)(色盲考生不予录取)」
    「计算机类(中外合作办学)」
    「机械设计制造及其自动化（校企合作）」
This measures how much of the residual mismatch is explained by qualifiers, and
whether aggressive normalisation helps or hurts.
"""

from __future__ import annotations

import re
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

# Qualifiers that describe delivery mode rather than the programme itself.
QUALIFIER_HINTS = (
    "中外合作", "校企合作", "地方专项", "国家专项", "高校专项", "公费", "委培",
    "师范", "定向", "订单", "预科", "少数民族", "农村", "走读", "菁英", "实验班",
    "拔尖", "基地班", "卓越", "创新班", "本博连读", "本硕连读", "五年制", "八年制",
)


def base_name(value: object, *, aggressive: bool) -> str:
    text = "" if value is None else str(value)
    text = text.replace("（", "(").replace("）", ")")
    if not aggressive:
        return text.split("(")[0].strip()
    # drop every parenthetical, then any trailing qualifier phrase
    text = re.sub(r"\([^)]*\)", "", text)
    for hint in QUALIFIER_HINTS:
        text = text.replace(hint, "")
    text = re.sub(r"[\s\-—·]+", "", text)
    return text.strip()


def keys(frame: pd.DataFrame, uni_col: str, major_col: str) -> set[tuple]:
    subset = frame[[uni_col, major_col]].dropna()
    subset = subset[(subset[uni_col] != "") & (subset[major_col] != "")]
    return set(map(tuple, subset.values))


def main() -> int:
    plans = pd.read_excel(PLAN_FILE, dtype=str)
    regular = plans[plans["批次"].astype(str).str.contains("普通类一段", na=False)].copy()
    regular = regular[regular["院校代码"].notna() & regular["专业代码"].notna()]

    for aggressive in (False, True):
        label = "激进（剥离所有括号与限定词）" if aggressive else "保守（仅取第一个括号前）"
        regular["U"] = regular["院校名称"].map(lambda v: base_name(v, aggressive=aggressive))
        regular["M"] = regular["专业名称"].map(lambda v: base_name(v, aggressive=aggressive))
        sets = {
            year: keys(group, "U", "M")
            for year, group in regular.groupby("年份")
        }
        print(f"--- {label} ---")
        for a, b in (("2023", "2024"), ("2024", "2025")):
            inter = len(sets[a] & sets[b])
            print(f"    {a}->{b}: {inter:>7,} / {len(sets[b]):>7,} = {inter / max(1, len(sets[b])) * 100:>5.1f}%")
        triple = len(sets["2023"] & sets["2024"] & sets["2025"])
        print(f"    2023∩2024∩2025 = {triple:,}")
        print()

        if not aggressive:
            # How many names carry a qualifier at all?
            with_qualifier = regular["专业名称"].astype(str).str.contains(r"[（(]").mean()
            print(f"    含括号限定语的专业名占比: {with_qualifier * 100:.1f}%")
            samples = [
                n for n in regular["专业名称"].dropna().unique()
                if "(" in str(n).replace("（", "(")
            ][:8]
            print("    样例：")
            for sample in samples:
                print(f"      {sample}  ->  {base_name(sample, aggressive=False)}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
