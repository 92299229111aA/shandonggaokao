"""Measure 选科要求 parse coverage against the real 2022-2025 Shandong files.

This is the gate for the whole rule engine: if coverage is not ~100%, a large share
of plans degrade to UNKNOWN and the scheme has no safety net.

Run: python scripts/verify_subject_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1] / "backend"
def _layout():
    """Resolve the source-data directory from --data-root or GAOKAO_DATA_ROOT."""
    import argparse
    from app.ingestion.data_root import resolve_root

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=None, help="官方源文件目录（或用 GAOKAO_DATA_ROOT）")
    known = parser.parse_known_args()[0]
    return resolve_root(known.data_root)
sys.path.insert(0, str(BACKEND))

from app.ingestion.subject_rules import coverage_report, parse_subject_requirement  # noqa: E402

AGG = _layout().aggregate_dir
PLAN_FILE = next(p for p in AGG.rglob("*.xlsx") if "22-25年全国高校在山东的招生计划" in p.name)
DETAIL_FILE = (
    _layout().root
    / "各省份" / "山东" / "山东" / "山东"
    / "25年招生计划与分数" / "山东2025本科批计划最新.xlsx"
)


def report(label: str, values) -> bool:
    result = coverage_report(list(values))
    print("=" * 100)
    print(f"{label}")
    print("=" * 100)
    print(f"  样本数        : {result['total']:,}")
    print(f"  成功解析      : {result['parsed']:,}")
    print(f"  无法解析      : {result['unparsed']:,}")
    print(f"  覆盖率        : {result['coverage']}%")
    print()
    print("  解析出的形态（可读形式 → 行数）：")
    for value, count in list(result["parsed_values"].items())[:25]:
        print(f"    {count:>8,}  {value}")
    if result["unparsed_values"]:
        print()
        print("  无法解析的原始值：")
        for value, count in result["unparsed_values"].items():
            print(f"    {count:>8,}  {value!r}")
    print()
    print(f"  涉及科目统计: {result['subject_counts']}")
    print()
    blanks = result["unparsed_values"].get("<空>", 0)
    real_unparsed = result["unparsed"] - blanks
    if blanks:
        print(f"  其中空白单元格 {blanks} 处（源文件本身为空，属数据缺失而非解析失败）")
    return real_unparsed == 0


def main() -> int:
    ok = True

    plans = pd.read_excel(PLAN_FILE, dtype=str)
    print(f"汇总文件: {PLAN_FILE.name}  总行数 {len(plans):,}")
    print()

    regular = plans[plans["批次"].astype(str).str.contains("普通类一段", na=False)]
    ok &= report("A. 汇总文件 / 普通类一段 全部年份 (2022-2025)", regular["选科要求"])

    for year in sorted(regular["年份"].dropna().unique()):
        subset = regular[regular["年份"] == year]
        ok &= report(f"B. 汇总文件 / {year} 年 普通类一段", subset["选科要求"])

    detailed = pd.read_excel(DETAIL_FILE, sheet_name="常规批一段", dtype=str)
    print(f"官方格式文件: {DETAIL_FILE.name} / 常规批一段  行数 {len(detailed):,}")
    print()
    ok &= report("C. 官方格式文件 / 常规批一段 (2025)", detailed["选科要求"])

    print("=" * 100)
    print("补充：解析器对「或」/「N选M」/边界输入的行为（正确性比覆盖率更重要）")
    print("=" * 100)
    probes = [
        # (输入, 期望的可读结果)  None 表示应当拒绝解析
        ("不限", "不限"),
        ("物理", "物理"),
        ("物理和化学", "物理+化学"),
        ("物理和化学和生物", "物理+化学+生物"),
        ("物理 或 化学", "物理 或 化学"),
        ("物/化/生(3选1)", "物理 或 化学 或 生物"),
        ("物、化、生(3科必选)", "物理+化学+生物"),
        ("物化生(3科必选)", "物理+化学+生物"),
        ("物理、化学(2科必选)", "物理+化学"),
        ("物理必选", "物理"),
        ("思想政治和历史", "思想政治+历史"),
        ("史/政/地(3选1)", "历史 或 思想政治 或 地理"),
        ("物/化/地(3选1)", "物理 或 化学 或 地理"),
        ("化学,生物", "化学+生物"),
        ("物理化学", "物理+化学"),
        ("物化生(3选2)", None),  # M>1 无法用 DNF 表达，必须拒绝
        ("物理或历史且化学", None),  # 嵌套表达式，必须拒绝
        ("", None),
        (None, None),
        ("详见招生章程", None),
        ("面试合格", None),
    ]
    failures = []
    for probe, expected in probes:
        parsed = parse_subject_requirement(probe)
        actual = None if parsed is None else ("不限" if parsed.unrestricted else parsed.as_text())
        if expected is None:
            mark = "拒绝（安全）" if actual is None else f"⚠ 解析为 {actual}"
            ok = actual is None
        else:
            mark = actual if actual is not None else "拒绝"
            ok = actual == expected
        if not ok:
            failures.append((probe, expected, actual))
        print(f"    {'OK ' if ok else 'FAIL'}  {str(probe)[:22]:<24} → {mark}")

    print()
    print("=" * 100)
    if failures:
        print(f"存在 {len(failures)} 项行为不符合预期：")
        for probe, expected, actual in failures:
            print(f"    {probe!r}  期望 {expected!r}  实际 {actual!r}")
        return 1
    print("结论：" + ("覆盖率达标且边界行为正确，解析器可用于导入" if ok else "覆盖率未达标"))
    print("=" * 100)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
