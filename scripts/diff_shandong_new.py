"""What, in the new material, is both new AND relevant to a Shandong V1 load?

V1 covers Shandong / 夏季高考 / 普通类 / 常规批 / 本科 only. The new material contains
thousands of files for other provinces and other batches; this narrows to what could
actually change the database, and reports whether any 2026 plan data exists at all.

Run: python scripts/diff_material.py  (produces the index)  then this.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

OLD_ROOT = Path(r"D:\BaiduNetdiskDownload\高考志愿")
NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")
EXTENSIONS = {".xlsx", ".xls", ".xlsm"}

# Names that indicate Shandong-relevant content.
SHANDONG_HINTS = ("山东", "山东")
# Names that could carry a future plan year worth loading.
FUTURE_YEAR_HINTS = ("2026", "26年", "26山东")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def index(root: Path) -> dict[str, list[Path]]:
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            try:
                by_hash[sha256(path)].append(path)
            except OSError:
                continue
    return by_hash


def main() -> int:
    old = index(OLD_ROOT)
    new = index(NEW_ROOT)
    only_new = set(new) - set(old)

    new_files = [p for digest in only_new for p in new[digest]]
    shandong = [p for p in new_files if any(h in str(p) for h in SHANDONG_HINTS)]

    print("=" * 100)
    print(f"新增文件中与山东相关的: {len(shandong)} 个")
    print("=" * 100)

    # Group by a coarse category so the list is readable.
    def category(path: Path) -> str:
        name = path.name
        for key, label in (
            ("招生计划", "招生计划"),
            ("专业分数线", "专业分数线"),
            ("专业录取分数", "专业录取分数"),
            ("投档线", "投档线"),
            ("投档", "投档"),
            ("一分一段", "一分一段"),
            ("院校分数", "院校分数"),
            ("院校录取", "院校录取"),
            ("专业目录", "专业目录"),
            ("选科", "选科要求"),
            ("分数线", "分数线"),
        ):
            if key in name:
                return label
        return "其他"

    buckets: dict[str, list[Path]] = defaultdict(list)
    for path in shandong:
        buckets[category(path)].append(path)

    for label, paths in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"\n## {label}（{len(paths)} 个）")
        for path in sorted(paths)[:14]:
            print(f"   {path.stat().st_size / 1024 / 1024:>7.2f}MB  {path.relative_to(NEW_ROOT)}")
        if len(paths) > 14:
            print(f"   … 另有 {len(paths) - 14} 个")

    print()
    print("=" * 100)
    print("是否存在 2026 年（或 26 年）的招生计划数据")
    print("=" * 100)
    future = [
        p for p in new_files
        if any(h in p.name for h in FUTURE_YEAR_HINTS) and "计划" in p.name
    ]
    if future:
        for path in sorted(future):
            print(f"  {path.stat().st_size / 1024 / 1024:>7.2f}MB  {path.relative_to(NEW_ROOT)}")
    else:
        print("  新资料中没有任何文件名含 2026/26年 且含「计划」的 Excel")

    print()
    print("在已接入的资料里搜索同样的东西：")
    old_future = [
        p for p in OLD_ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSIONS
        and any(h in p.name for h in FUTURE_YEAR_HINTS) and "计划" in p.name
    ]
    if old_future:
        for path in sorted(old_future)[:10]:
            print(f"  {path.stat().st_size / 1024 / 1024:>7.2f}MB  {path.name}")
    else:
        print("  已接入资料中同样没有 2026 计划文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
