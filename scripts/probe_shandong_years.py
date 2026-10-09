"""Compare the 2023 / 2024 / 2025 Shandong files to see whether cross-year
matching on 院校代码 + 专业代码 is viable for Program Series alignment.
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


BASE = _layout().root / "各省份" / "山东" / "山东" / "山东"
OLD = BASE / "24年及以前（分数计划）"


def peek(label: str, path: Path, header: int | None = 0, engine: str | None = None) -> pd.DataFrame | None:
    if not path.exists():
        print(f"[{label}] 文件不存在: {path.name}")
        return None
    try:
        frame = pd.read_excel(path, sheet_name=0, header=header, dtype=str, engine=engine)
    except Exception as exc:  # noqa: BLE001 - probing
        print(f"[{label}] 读取失败: {type(exc).__name__}: {str(exc)[:100]}")
        return None
    print(f"[{label}] {path.name}  shape={frame.shape}")
    if header == 0:
        print(f"    columns: {list(frame.columns)}")
        with pd.option_context("display.max_columns", 200, "display.width", 300, "display.max_colwidth", 40):
            print(frame.head(3).to_string().replace("\n", "\n    "))
    else:
        with pd.option_context("display.max_columns", 200, "display.width", 300, "display.max_colwidth", 40):
            print(frame.head(5).to_string().replace("\n", "\n    "))
    print()
    return frame


def main() -> int:
    print("#" * 110)
    print("# 2023 / 2024 招生计划")
    print("#" * 110)
    peek("2023 计划(本科)", OLD / "山东_招生计划_2017-2023" / "山东_招生计划_2023普通类本科招生计划-6.21版.xlsx")
    peek("2024 计划(本科)", OLD / "山东2024年普通类招生计划(本科).xlsx")

    print("#" * 110)
    print("# 2023 / 2024 投档线（ACS 的历史依据）")
    print("#" * 110)
    peek("2023 院校分数", OLD / "山东_投档线_2017-2023" / "山东-2023-院校分数.xlsx")
    peek("2024 专业录取分", OLD / "2024年全国高等院校在山东的专业录取分数线.xlsx")

    print("#" * 110)
    print("# 22-25 汇总文件（可能一次覆盖多年）")
    print("#" * 110)
    agg = _layout().aggregate_dir
    candidate = None
    for folder in agg.iterdir():
        if folder.is_dir() and "山东" in folder.name:
            candidate = folder
            break
    if candidate:
        for sub in candidate.rglob("*.xlsx"):
            if "22-25" in sub.name:
                peek(f"汇总 {sub.name}", sub)
    return 0


if __name__ == "__main__":
    sys.exit(main())
