"""Inspect the 投档情况表 (cut-off results) which is the historical basis for ACS."""

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
FILE = BASE / "25年招生计划与分数" / "其他资料" / "山东省2025年普通类常规批第1次志愿投档情况表.xls.xls"


def main() -> int:
    print(f"file: {FILE.name}  exists={FILE.exists()}")
    print()

    # Try each engine; the double .xls suffix may actually hide xlsx or real xls.
    frame = None
    for engine in ("xlrd", "openpyxl", "calamine"):
        try:
            frame = pd.read_excel(FILE, sheet_name=0, header=None, dtype=str, engine=engine)
            print(f"读取成功，engine={engine}")
            break
        except Exception as exc:  # noqa: BLE001 - probing
            print(f"  engine={engine} 失败: {type(exc).__name__}: {str(exc)[:120]}")
    if frame is None:
        print("无法读取该文件")
        return 1

    print(f"shape = {frame.shape}")
    print()
    print("前 12 行原始内容：")
    with pd.option_context("display.max_columns", 200, "display.width", 320, "display.max_colwidth", 45):
        print(frame.head(12).to_string())
    print()

    # Detect the header row: first row where most cells are non-null text
    best_row, best_score = 0, -1
    for index in range(min(10, len(frame))):
        score = int(frame.iloc[index].notna().sum())
        if score > best_score:
            best_row, best_score = index, score
    print(f"最可能的表头行 = {best_row}（非空 {best_score} 个单元格）")
    header = [str(v).strip() for v in frame.iloc[best_row].tolist()]
    print("表头:", header)
    print()

    body = frame.iloc[best_row + 1 :].copy()
    body.columns = header
    print("按该表头解析后的前 6 行：")
    with pd.option_context("display.max_columns", 200, "display.width", 320, "display.max_colwidth", 45):
        print(body.head(6).to_string())
    print()

    for column in header[:12]:
        if column in body.columns:
            values = body[column].dropna().astype(str).str.strip()
            if 0 < values.nunique() <= 12:
                print(f"[{column}] 取值分布（{values.nunique()} 种）：")
                for value, count in Counter(values).most_common(12):
                    print(f"    {count:>9,}  {value}")
                print()
            else:
                print(f"[{column}] 去重数 {values.nunique()}，样例: {list(values.head(3))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
