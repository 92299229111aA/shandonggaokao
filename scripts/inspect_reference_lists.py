"""Inspect the official MOE reference lists supplied in the new material.

Two of them are structured enough to load into the reference schema:

  全国普通高等学校名单（截止2024年6月20日）.xls   — MOE university registry
  普通高等学校本科专业目录（2025年4月）            — MOE major catalogue (PDF)
  2026年普通高等学校本科专业目录（2026年4月）      — newer major catalogue (PDF)

This reports what is actually parseable before anything is written.

Run: python scripts/inspect_reference_lists.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")


def find(fragment: str) -> list[Path]:
    return sorted(
        p for p in NEW_ROOT.rglob("*") if p.is_file() and fragment in p.name
    )


def main() -> int:
    print("=" * 100)
    print("全国普通高等学校名单（教育部）")
    print("=" * 100)
    for path in find("全国普通高等学校名单"):
        print(f"  {path.stat().st_size / 1024 / 1024:.2f}MB  {path.name}")
        for engine in ("xlrd", "openpyxl"):
            try:
                frame = pd.read_excel(path, sheet_name=0, header=None, dtype=str, engine=engine)
                break
            except Exception:  # noqa: BLE001 - probing
                frame = None
        if frame is None:
            print("     无法读取")
            continue
        print(f"     shape={frame.shape}   engine={engine}")
        with pd.option_context("display.width", 220, "display.max_columns", 20, "display.max_colwidth", 26):
            print("     " + frame.head(10).to_string().replace("\n", "\n     "))
        break

    print()
    print("=" * 100)
    print("专业目录 PDF（判断是否可解析）")
    print("=" * 100)
    for fragment in ("普通高等学校本科专业目录（2025", "2026年普通高等学校本科专业目录"):
        for path in find(fragment):
            print(f"  {path.stat().st_size / 1024 / 1024:.2f}MB  {path.name}")
            try:
                import pdfplumber  # type: ignore

                with pdfplumber.open(path) as pdf:
                    print(f"     页数 {len(pdf.pages)}")
                    text = (pdf.pages[0].extract_text() or "")[:300]
                    print(f"     第 1 页文本片段: {text!r}")
            except ImportError:
                print("     （未安装 pdfplumber，跳过文本抽取检查）")
            except Exception as exc:  # noqa: BLE001
                print(f"     读取失败: {type(exc).__name__}: {str(exc)[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
