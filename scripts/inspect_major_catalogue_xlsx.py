"""Inspect the structured (Excel) MOE major catalogue.

The PDF version could not be parsed reliably and its codes cannot be joined to the plan
data anyway. This Excel version is the structured source the earlier decision asked for,
so the first question is whether it carries all three levels
(门类 → 专业类 → 专业) with codes.

Run: python scripts/inspect_major_catalogue_xlsx.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")

FRAGMENTS = (
    "绝密报考-普通高等学校本科专业目录（2023年完整版）",
    "绝密报考-普通高等学校本科专业目录以及一级学科顶尖院校清单",
    "名称较长（7字及以上）本科专业汇总",
)


def find(fragment: str) -> list[Path]:
    return sorted(
        p for p in NEW_ROOT.rglob("*")
        if p.is_file() and fragment in p.name and p.suffix.lower() in {".xlsx", ".xls"}
    )


def main() -> int:
    seen: set[str] = set()
    for fragment in FRAGMENTS:
        paths = find(fragment)
        if not paths:
            print(f"[{fragment}] 未找到")
            continue
        # Deduplicate by size: the same workbook is copied into many province folders.
        path = min(paths, key=lambda p: len(p.parts))
        key = f"{path.name}:{path.stat().st_size}"
        if key in seen:
            continue
        seen.add(key)

        print("=" * 100)
        print(f"{path.name}")
        print(f"  {path.stat().st_size / 1024:.0f} KB   {path.relative_to(NEW_ROOT)}")
        print(f"  副本数 {len(paths)}")
        print("=" * 100)
        try:
            book = pd.ExcelFile(path, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001 - probing
            print(f"  无法读取: {type(exc).__name__}: {str(exc)[:120]}")
            continue

        print(f"  sheets: {book.sheet_names}")
        for sheet in book.sheet_names[:4]:
            frame = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str)
            print()
            print(f"  --- sheet {sheet!r}  shape={frame.shape} ---")
            with pd.option_context(
                "display.width", 220, "display.max_columns", 14, "display.max_colwidth", 24
            ):
                print("  " + frame.head(8).to_string().replace("\n", "\n  "))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
