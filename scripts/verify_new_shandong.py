"""Verify whether the new Shandong files carry data the database does not already have.

Candidates worth checking:

  1. 山东省2025年普通类常规批第1次志愿投档情况表.xls  — official per-major cut-offs
  2. 2025/2024 夏季高考文化成绩一分一段表            — rank tables

Everything else in the new material is either another province, another batch
(艺术/体育/专科/第2次志愿), or byte-identical to what is already loaded.

Run: python scripts/verify_new_shandong.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")
OLD_SD = Path(
    r"D:\BaiduNetdiskDownload\高考志愿\各省份\山东\山东\山东\25年招生计划与分数\其他资料"
    r"\山东省2025年普通类常规批第1次志愿投档情况表.xls.xls"
)


def find(name_fragment: str) -> list[Path]:
    return sorted(
        p for p in NEW_ROOT.rglob("*")
        if p.is_file() and name_fragment in p.name and p.suffix.lower() in {".xls", ".xlsx", ".x1s"}
    )


def read_any(path: Path) -> pd.DataFrame | None:
    for engine in ("xlrd", "openpyxl", "calamine"):
        try:
            return pd.read_excel(path, sheet_name=0, header=None, dtype=str, engine=engine)
        except Exception:  # noqa: BLE001 - probing multiple engines
            continue
    return None


def main() -> int:
    print("=" * 100)
    print("1) 2025 普通类常规批第 1 次志愿投档情况表")
    print("=" * 100)
    old = read_any(OLD_SD) if OLD_SD.exists() else None
    print(f"  已接入: {OLD_SD.name}")
    print(f"          shape={None if old is None else old.shape}")

    candidates = [
        p for p in find("山东省2025年普通类常规批第1次志愿投档情况表")
    ]
    seen: set[str] = set()
    for path in candidates:
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        frame = read_any(path)
        same = frame is not None and old is not None and frame.shape == old.shape and frame.equals(old)
        print(f"  新资料: {path.relative_to(NEW_ROOT)}")
        print(f"          shape={None if frame is None else frame.shape}   与已接入逐格相同: {same}")

    print()
    print("=" * 100)
    print("2) 山东 2024 / 2025 夏季高考文化成绩一分一段表")
    print("=" * 100)
    for year in ("2024", "2025"):
        for path in find(f"{year}年夏季高考文化成绩一分一段表"):
            frame = read_any(path)
            print(f"  {path.relative_to(NEW_ROOT)}")
            print(f"     shape={None if frame is None else frame.shape}")
            if frame is not None:
                with pd.option_context("display.width", 200, "display.max_columns", 20):
                    print("     " + frame.head(3).to_string().replace("\n", "\n     "))
            break

    print()
    print("=" * 100)
    print("3) 2025 本科批招生计划（与已接入的 22-25 汇总对比）")
    print("=" * 100)
    for path in find("山东2025本科批计划最新") + find("2025年招生计划"):
        print(f"  {path.relative_to(NEW_ROOT)}")
    print("  （若上面为空，说明新资料中没有新的本科计划文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
