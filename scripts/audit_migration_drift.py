"""Audit migration/model drift against the live database, separating real gaps from noise.

`alembic check` flags ~70 differences on the development database. Many look like the
known false positive where a `server_default` exists in the DDL but the model declares
only a Python-side `default=` (D-005). Others — missing indexes — would be a real defect
if the database genuinely lacks them.

This asks the database directly instead of trusting the diff summary.

Run: python scripts/audit_migration_drift.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.registry import ALL_MODELS  # noqa: E402

# Indexes `alembic check` proposed adding.
SUSPECT_INDEXES = (
    ("candidate", "location_preferences", "ix_location_preferences_preference_profile_id"),
    ("candidate", "major_preferences", "ix_major_preferences_preference_profile_id"),
    ("candidate", "university_preferences", "ix_university_preferences_preference_profile_id"),
    ("candidate", "other_preferences", "ix_other_preferences_preference_profile_id"),
)


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    inspector = inspect(engine)

    print("=" * 100)
    print("1) alembic check 建议新增的索引，数据库里到底有没有")
    print("=" * 100)
    for schema, table, index_name in SUSPECT_INDEXES:
        try:
            existing = {ix["name"] for ix in inspector.get_indexes(table, schema=schema)}
        except Exception as exc:  # noqa: BLE001 - a missing table is itself a finding
            print(f"  {schema}.{table:<24} 读取失败: {type(exc).__name__}")
            continue
        mark = "存在" if index_name in existing else "!! 缺失"
        print(f"  {schema}.{table:<24} {index_name:<52} {mark}")
        if index_name not in existing:
            print(f"      该表现有索引: {sorted(existing)}")

    print()
    print("=" * 100)
    print("2) 模型声明的索引 vs 数据库实际索引")
    print("=" * 100)
    missing_total = 0
    for model in ALL_MODELS:
        table = model.__tablename__
        schema = model.__table__.schema
        declared = {ix.name for ix in model.__table__.indexes if ix.name}
        if not declared:
            continue
        try:
            actual = {ix["name"] for ix in inspector.get_indexes(table, schema=schema)}
        except Exception:  # noqa: BLE001
            continue
        missing = declared - actual
        if missing:
            missing_total += len(missing)
            print(f"  !! {schema}.{table}: 模型声明但库里没有 -> {sorted(missing)}")
    if missing_total == 0:
        print("  模型声明的索引全部存在于数据库 ✓")
    print()

    print("=" * 100)
    print("3) 各表行数与 server_default 抽样（判断 D-005 的假设是否成立）")
    print("=" * 100)
    with engine.connect() as conn:
        for schema, table, column in (
            ("recommendation", "schemes", "lock_version"),
            ("reference", "universities", "is_985"),
            ("recommendation", "sessions", "status"),
            ("recommendation", "schemes", "title"),
        ):
            row = conn.execute(
                text(
                    "select column_default, is_nullable from information_schema.columns "
                    "where table_schema = :s and table_name = :t and column_name = :c"
                ),
                {"s": schema, "t": table, "c": column},
            ).fetchone()
            default = row[0] if row else "<列不存在>"
            print(f"  {schema}.{table}.{column:<20} server_default={default}")
    print()

    print("=" * 100)
    print("4) 结论")
    print("=" * 100)
    print("  - modify_default 类差异：DDL 有 server_default 而模型只写 Python default，")
    print("    属已知噪音（D-005），不影响运行。")
    print("  - remove_table alembic_version：autogenerate 自身的记账表，属误报。")
    print("  - add_index 类差异：若上面第 1 节显示索引确实缺失，则是真实缺陷。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
