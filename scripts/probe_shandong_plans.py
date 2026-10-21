"""Focused inspection of the Shandong files that matter for V1.

Prints column names, value distributions for the columns that drive the rule
engine (选科要求), and sample rows for the 常规批一段 sheet.
"""

from __future__ import annotations

import sys
from collections import Counter
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


BASE = _layout().root / "各省份" / "山东" / "山东" / "山东"


def show_frame(label: str, frame: pd.DataFrame, rows: int = 6) -> None:
    print("=" * 110)
    print(f"{label}   shape={frame.shape}")
    print("=" * 110)
    print("columns:", list(frame.columns))
    print()
    with pd.option_context("display.max_columns", 200, "display.width", 300, "display.max_colwidth", 60):
        print(frame.head(rows).to_string())
    print()


def value_counts(frame: pd.DataFrame, column: str, limit: int = 40, label: str = "") -> None:
    if column not in frame.columns:
        print(f"[{label or column}] 列不存在")
        return
    counts = Counter(frame[column].fillna("<空>").astype(str).str.strip())
    print(f"[{label or column}] 共 {len(counts)} 种取值，前 {limit} 种：")
    for value, count in counts.most_common(limit):
        print(f"    {count:>7,}  {value}")
    print()


def main() -> int:
    plan_file = BASE / "25年招生计划与分数" / "山东2025本科批计划最新.xlsx"
    book = pd.ExcelFile(plan_file, engine="openpyxl")
    print(f"计划文件 sheets: {book.sheet_names}")
    print()

    target = "常规批一段"
    frame = pd.read_excel(plan_file, sheet_name=target, dtype=str)
    show_frame(f"计划文件 / {target}", frame, rows=5)

    value_counts(frame, "选科要求", limit=60, label="选科要求（决定规则引擎）")
    value_counts(frame, "专业类型", limit=20)
    value_counts(frame, "科类", limit=10)
    value_counts(frame, "批次", limit=10)
    value_counts(frame, "省份", limit=5)

    print(f"院校去重数: {frame['院校名称'].nunique()}")
    print(f"院校代码去重数: {frame['院校代码'].nunique()}")
    print(f"选科要求为空的行数: {frame['选科要求'].isna().sum()}")
    print()

    rank_file = BASE / "25年招生计划与分数" / "山东2025年的一分一段表.xlsx"
    rank = pd.read_excel(rank_file, sheet_name=0, dtype=str)
    show_frame("一分一段表", rank, rows=4)
    value_counts(rank, "批次", limit=10)
    print("最后 3 行（对应最低分）：")
    with pd.option_context("display.max_columns", 200, "display.width", 300):
        print(rank.tail(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
