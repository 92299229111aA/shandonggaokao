"""Inspect the 2026 material before writing any loader.

The 2026 folder is a different shape from the 2022-2025 set: it is a post-hoc publication
(投档情况表 already contains the cutoffs), not a plan book. This prints each candidate
file's sheet layout so the decision about what to import is based on the actual columns.

Read-only. Run: python scripts/inspect_2026.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

ROOT = Path(os.environ.get("GAOKAO_DATA_ROOT", Path.cwd())) / "2026"

# The files that could plausibly matter. 提前批 (early batch) is out of scope: the product
# targets 普通类常规批.
TARGETS = [
    "2026一分一段表.xls",
    "山东省2026年普通类常规批第1次志愿投档情况表.xls",
    "山东省2026年普通类常规批第2次志愿投档情况表.xls",
    "山东省2026年普通类常规批第3次志愿投档情况表.xls",
    "山东省2026年普通类常规批第2次志愿剩余本科计划.xls",
    "山东省2026年普通类常规批第3次志愿院校专业计划.xls",
]


def show(path: Path, rows: int = 6) -> None:
    print("=" * 108)
    print(f"{path.name}   ({path.stat().st_size / 1024:.0f} KB)")
    print("=" * 108)
    try:
        book = pd.ExcelFile(path, engine="xlrd")
    except Exception as exc:  # noqa: BLE001 - probing
        print(f"  无法读取: {type(exc).__name__}: {str(exc)[:100]}")
        return
    print(f"  sheets: {book.sheet_names}")
    for sheet in book.sheet_names[:3]:
        frame = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str)
        print(f"\n  --- sheet {sheet!r}  shape={frame.shape} ---")
        with pd.option_context(
            "display.width", 240, "display.max_columns", 20, "display.max_colwidth", 18
        ):
            print("  " + frame.head(rows).to_string().replace("\n", "\n  "))


def main() -> int:
    if not ROOT.exists():
        print(f"目录不存在: {ROOT}")
        return 1
    for name in TARGETS:
        path = ROOT / name
        if not path.exists():
            print(f"!! 缺少 {name}")
            continue
        show(path)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
