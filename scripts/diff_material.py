"""Find what is genuinely new across the whole supplied material.

Hashes every workbook under both roots and groups them by content. Files present only
in the new material are the delta worth loading; files whose hash already appears in the
ingested set are duplicates and can be skipped.

Also looks specifically for 2026 plan data, which is the one thing that would actually
change recommendations.

Run: python scripts/diff_material.py
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

OLD_ROOT = Path(r"D:\BaiduNetdiskDownload\高考志愿")
NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")
EXTENSIONS = {".xlsx", ".xls", ".xlsm"}


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
    print("建立索引（这一步要读完所有 Excel，需要一两分钟）…")
    old = index(OLD_ROOT)
    new = index(NEW_ROOT)
    old_hashes = set(old)
    new_hashes = set(new)
    print(f"  已接入: {len(old)} 个唯一文件 / {sum(len(v) for v in old.values()):,} 个副本")
    print(f"  新资料: {len(new)} 个唯一文件 / {sum(len(v) for v in new.values()):,} 个副本")
    print()

    only_new = new_hashes - old_hashes
    both = new_hashes & old_hashes
    print("=" * 100)
    print(f"重复（内容已接入）: {len(both)} 个唯一文件")
    print(f"新增（仅新资料有）: {len(only_new)} 个唯一文件")
    print("=" * 100)
    print()

    if only_new:
        print("新增文件清单：")
        for digest in sorted(only_new):
            for path in new[digest]:
                size = path.stat().st_size / 1024 / 1024
                print(f"  {size:>7.2f}MB  {path.relative_to(NEW_ROOT)}")
        print()

    # Anything mentioning 2026 plans?
    print("=" * 100)
    print("含 2026 招生计划的文件（唯一会改变推荐结果的东西）")
    print("=" * 100)
    hits = [
        p
        for p in NEW_ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSIONS and "2026" in p.name and "计划" in p.name
    ]
    if hits:
        for path in hits:
            print(f"  {path.stat().st_size / 1024 / 1024:>7.2f}MB  {path.relative_to(NEW_ROOT)}")
    else:
        print("  未发现文件名同时含「2026」与「计划」的 Excel")
    print()

    print("=" * 100)
    print("按目录统计新增文件")
    print("=" * 100)
    by_dir: dict[str, int] = defaultdict(int)
    for digest in only_new:
        for path in new[digest]:
            parts = path.relative_to(NEW_ROOT).parts
            by_dir["/".join(parts[:3])] += 1
    for directory, count in sorted(by_dir.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {count:>4}  {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
