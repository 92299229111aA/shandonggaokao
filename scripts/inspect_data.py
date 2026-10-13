"""Inspect Excel/CSV data files without guessing their shape.

Usage: python scripts/inspect_data.py <file> [--sheet N] [--rows N]
Prints sheet names, dimensions, header row candidates and sample rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def readable_columns(columns) -> list[str]:
    return [str(c).replace("\n", " ").strip() for c in columns]


def inspect(path: Path, sheet: int | str | None, nrows: int) -> None:
    print("=" * 100)
    print(f"FILE: {path.name}   ({path.stat().st_size / 1024 / 1024:.2f} MB)")
    print("=" * 100)

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        book = pd.ExcelFile(path, engine="openpyxl")
        print(f"sheets ({len(book.sheet_names)}): {book.sheet_names[:20]}")
        targets = [sheet] if sheet is not None else book.sheet_names[:1]
    elif suffix == ".xls":
        book = pd.ExcelFile(path, engine="xlrd")
        print(f"sheets ({len(book.sheet_names)}): {book.sheet_names[:20]}")
        targets = [sheet] if sheet is not None else book.sheet_names[:1]
    else:
        targets = [None]

    for target in targets:
        print()
        print(f"--- sheet: {target!r} ---")
        if target is None:
            frame = pd.read_csv(path, nrows=nrows, dtype=str, header=None)
        else:
            # header=None first so we can see whether row 0 is a title or the header
            frame = pd.read_excel(path, sheet_name=target, header=None, nrows=nrows, dtype=str)

        print(f"shape (first {nrows} rows x {frame.shape[1]} cols)")
        print()
        print("前 8 行原始内容：")
        with pd.option_context("display.max_columns", 200, "display.width", 250):
            print(frame.head(8).to_string())
        print()

    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        return


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    sheet: int | str | None = None
    nrows = 12
    args = sys.argv[2:]
    for index, arg in enumerate(args):
        if arg == "--sheet" and index + 1 < len(args):
            sheet = args[index + 1]
        if arg == "--rows" and index + 1 < len(args):
            nrows = int(args[index + 1])
    if not path.exists():
        print(f"not found: {path}")
        return 1
    inspect(path, sheet, nrows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
