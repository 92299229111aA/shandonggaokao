"""Locate the official source workbooks.

The real-data loaders need to know where the Shandong source files live. That path is
machine-specific, so it is never hardcoded: pass it explicitly, or set
`GAOKAO_DATA_ROOT`, or let the default apply.

    python -m app.ingestion.etl_shandong --data-root /path/to/高考志愿
    GAOKAO_DATA_ROOT=/path/to/高考志愿 python -m app.ingestion.etl_shandong

The expected layout is documented in docs/DATA_IMPORT.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "GAOKAO_DATA_ROOT"


class DataRootNotFound(RuntimeError):
    """Raised with an actionable message when the source files cannot be located."""


@dataclass(frozen=True, slots=True)
class SourceLayout:
    """Resolved paths to the workbooks the ETL reads."""

    root: Path

    @property
    def aggregate_dir(self) -> Path:
        """Folder holding the multi-year plan / score / university workbooks."""
        return self.root / "2026高考志愿填报大数据(1)"

    @property
    def score_rank_dir(self) -> Path:
        """Folder holding 山东{year}年的一分一段表.xlsx."""
        return (
            self.aggregate_dir
            / "14、山东-2026志愿填报资料【先保存才能用】"
            / "14、山东-2026志愿填报资料【永久更新】"
            / "2、山东高考录取数据22-25【持续更新】"
            / "一分一段"
        )

    def find(self, pattern: str) -> Path:
        """First workbook under the aggregate folder whose name contains `pattern`."""
        if not self.aggregate_dir.exists():
            raise DataRootNotFound(self._help(f"未找到目录 {self.aggregate_dir}"))
        matches = sorted(p for p in self.aggregate_dir.rglob("*.xlsx") if pattern in p.name)
        if not matches:
            raise DataRootNotFound(self._help(f"未找到名称包含 {pattern!r} 的 .xlsx 文件"))
        return matches[0]

    def score_rank_file(self, year: int) -> Path:
        path = self.score_rank_dir / f"山东{year}年的一分一段表.xlsx"
        if not path.exists():
            raise DataRootNotFound(self._help(f"未找到 {path}"))
        return path

    def _help(self, problem: str) -> str:
        return (
            f"{problem}\n"
            f"请用 --data-root 指定数据目录，或设置环境变量 {ENV_VAR}。\n"
            f"当前数据根目录: {self.root}\n"
            "目录结构见 docs/DATA_IMPORT.md。"
        )


def resolve_root(explicit: str | Path | None = None) -> SourceLayout:
    """Resolve the data root from an explicit path, the env var, or the default.

    An explicit or environment-provided path that does not exist is an error; only the
    built-in default is allowed to be absent, and it is reported when actually used.
    """
    if explicit is not None:
        root = Path(explicit).expanduser()
        if not root.exists():
            raise DataRootNotFound(f"指定的数据目录不存在: {root}")
        return SourceLayout(root)

    from_env = os.environ.get(ENV_VAR)
    if from_env:
        root = Path(from_env).expanduser()
        if not root.exists():
            raise DataRootNotFound(f"{ENV_VAR} 指向的目录不存在: {root}")
        return SourceLayout(root)

    # No portable default exists — the files come from a commercial compilation that is
    # not redistributable. Keep the historical location as a convenience only.
    return SourceLayout(Path(r"D:\BaiduNetdiskDownload\高考志愿"))


__all__ = ["ENV_VAR", "DataRootNotFound", "SourceLayout", "resolve_root"]
