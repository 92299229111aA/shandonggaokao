"""把 PostgreSQL 里的真实数据导出成随安装包分发的 SQLite 文件。

**为什么是两步、两个进程**：模型的 schema 与外键目标在 import 时就按方言定型了
（见 app/db/dialect.py）。同一个进程不可能既是 PostgreSQL 又是 SQLite ——
PG 模式下外键带 `schema.table`，而 SQLite 建表会忽略 schema，
两边对不上就会报 "unknown database reference"。

所以拆成：
    1. dump —— PostgreSQL 模式下把每张表写到 staging 目录（JSON Lines）
    2. load —— SQLite 模式下建表并灌入 staging

构建脚本用不同的 GAOKAO_DB_MODE 分别调用这两步；桌面版只随包分发第 2 步的产物。

用法：
    GAOKAO_DB_MODE=postgres python -m app.ingestion.export_sqlite dump --out build/staging
    GAOKAO_DB_MODE=sqlite   python -m app.ingestion.export_sqlite load --staging build/staging --out build/gaokao.db --verify
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

# 导入以注册全部模型（不导入的话 Base.metadata 是空的）
from app.admission import models as _admission_models  # noqa: F401
from app.candidate import models as _candidate_models  # noqa: F401
from app.config import get_settings
from app.db.base import Base
from app.recommendation import models as _recommendation_models  # noqa: F401
from app.reference import models as _reference_models  # noqa: F401

# 桌面版随包分发哪些数据？
#
# 只带参考数据与招生数据。candidate / recommendation 两个 schema 是「用户自己的东西」
# （档案、偏好、推荐会话、方案、体检报告），必须从空库开始 ——
# 否则安装包里会带着开发机上上百个测试档案和几十万条推荐条目。
SHIPPED_SCHEMAS: tuple[str, ...] = ("reference", "admission")


def tables_to_ship(schemas: tuple[str, ...]) -> list:
    """按 schema 过滤出要随包分发的表，保持外键依赖顺序。"""
    if not schemas:
        return list(Base.metadata.sorted_tables)
    return [t for t in Base.metadata.sorted_tables if (t.schema or "") in schemas]


def _json_default(value: Any) -> Any:
    """把数据库返回值转成可 JSON 化的形式，再原样读回来。"""
    if isinstance(value, datetime | date | time):
        return {"__type__": type(value).__name__, "__value__": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type__": "Decimal", "__value__": str(value)}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "__value__": value.hex()}
    raise TypeError(f"无法序列化的类型: {type(value).__name__}")


def _json_hook(value: dict[str, Any]) -> Any:
    marker = value.get("__type__")
    if marker is None:
        return value
    raw = value.get("__value__")
    if not isinstance(raw, str):
        return value
    if marker == "datetime":
        return datetime.fromisoformat(raw)
    if marker == "date":
        return date.fromisoformat(raw)
    if marker == "time":
        return time.fromisoformat(raw)
    if marker == "Decimal":
        return Decimal(raw)
    if marker == "bytes":
        return bytes.fromhex(raw)
    return value


def dump(
    out_dir: Path, *, source_url: str | None = None, schemas: tuple[str, ...] = SHIPPED_SCHEMAS
) -> dict[str, int]:
    """第 1 步：PostgreSQL → staging 目录。"""
    settings = get_settings()
    if settings.is_sqlite:
        print("!! dump 必须在 PostgreSQL 模式下运行（GAOKAO_DB_MODE=postgres）")
        return {}

    out_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(source_url or settings.database_url, future=True)
    counts: dict[str, int] = {}

    tables = tables_to_ship(schemas)
    with Session(engine) as session:
        for table in tables:
            rows = session.execute(select(table)).mappings().all()
            # JSON Lines：逐行写，内存占用与表大小无关
            target = out_dir / f"{table.name}.jsonl"
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(json.dumps(dict(row), ensure_ascii=False, default=_json_default))
                    handle.write("\n")
            counts[table.name] = len(rows)

    engine.dispose()
    total = sum(counts.values())
    print(f"  导出 staging：{total:,} 行 / {len(counts)} 张表 → {out_dir}")
    return counts


def load(
    staging: Path,
    out_path: Path,
    *,
    schemas: tuple[str, ...] = SHIPPED_SCHEMAS,
    batch_size: int = 2000,
) -> dict[str, int]:
    """第 2 步：staging → SQLite 文件，按外键顺序建表并灌入。"""
    settings = get_settings()
    if not settings.is_sqlite:
        print("!! load 必须在 SQLite 模式下运行（GAOKAO_DB_MODE=sqlite）")
        return {}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    engine = create_engine(f"sqlite:///{out_path.as_posix()}", future=True)
    Base.metadata.create_all(engine)

    counts: dict[str, int] = {}
    # SQLite 模式下所有表的 schema 都是 None，按 schema 过滤会把表全筛掉。
    # 桌面库需要完整的 32 张表，staging 里没有的表自然就是空表。
    tables = list(Base.metadata.sorted_tables)
    with Session(engine) as session:
        for table in tables:
            source = staging / f"{table.name}.jsonl"
            if not source.exists():
                counts[table.name] = 0
                continue
            batch: list[dict[str, Any]] = []
            written = 0
            with source.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    batch.append(json.loads(line, object_hook=_json_hook))
                    if len(batch) >= batch_size:
                        session.execute(table.insert(), batch)
                        written += len(batch)
                        batch.clear()
                if batch:
                    session.execute(table.insert(), batch)
                    written += len(batch)
            session.commit()
            counts[table.name] = written

    engine.dispose()
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"  载入 SQLite：{sum(counts.values()):,} 行 / {len(counts)} 张表")
    print(f"  文件 {out_path}  {size_mb:.1f} MB")
    return counts


def verify(
    staging: Path, out_path: Path, *, schemas: tuple[str, ...] = SHIPPED_SCHEMAS
) -> bool:
    """逐表比对 staging 行数与 SQLite 实际行数。"""
    engine = create_engine(f"sqlite:///{out_path.as_posix()}", future=True)
    mismatched: list[str] = []
    tables = list(Base.metadata.sorted_tables)
    with Session(engine) as session:
        for table in tables:
            source = staging / f"{table.name}.jsonl"
            expected = 0
            if source.exists():
                with source.open("r", encoding="utf-8") as handle:
                    expected = sum(1 for line in handle if line.strip())
            actual = int(session.execute(select(func.count()).select_from(table)).scalar_one())
            if expected != actual:
                mismatched.append(f"{table.name}: staging {expected} != sqlite {actual}")
    engine.dispose()

    if mismatched:
        print("  !! 行数不一致：")
        for item in mismatched:
            print(f"     {item}")
        return False
    print(f"  校验通过：{len(tables)} 张表行数与 staging 一致")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite 数据文件导出（两步）")
    sub = parser.add_subparsers(dest="command", required=True)

    dump_parser = sub.add_parser("dump", help="PostgreSQL → staging")
    dump_parser.add_argument("--out", required=True, help="staging 目录")
    dump_parser.add_argument("--source", default=None, help="源库 URL")
    dump_parser.add_argument(
        "--schemas",
        default=",".join(SHIPPED_SCHEMAS),
        help="随包分发的 schema（逗号分隔；传空字符串表示全部）",
    )

    load_parser = sub.add_parser("load", help="staging → SQLite")
    load_parser.add_argument("--staging", required=True, help="staging 目录")
    load_parser.add_argument("--out", required=True, help="输出 .db 路径")
    load_parser.add_argument("--verify", action="store_true", help="灌入后逐表比对行数")
    load_parser.add_argument(
        "--schemas",
        default=",".join(SHIPPED_SCHEMAS),
        help="随包分发的 schema（逗号分隔；传空字符串表示全部）",
    )

    args = parser.parse_args(argv)

    schemas = tuple(item.strip() for item in args.schemas.split(",") if item.strip())

    if args.command == "dump":
        counts = dump(Path(args.out), source_url=args.source, schemas=schemas)
        return 0 if counts else 2

    counts = load(Path(args.staging), Path(args.out), schemas=schemas)
    if not counts:
        return 2
    if args.verify:
        return 0 if verify(Path(args.staging), Path(args.out), schemas=schemas) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
