"""Full schema comparison between the development database and a freshly-migrated one.

The development database reports `alembic_version = 0008_recommendation` yet lacks four
indexes that migration 0007 creates. A freshly migrated database has them. That means the
migration files were edited after being applied, so the recorded version no longer
describes the actual schema — and nothing detects it, because no test compares a
pre-existing database against the migrations.

This diffs every table, column and index between the two databases so the full extent is
known rather than just the four indexes that happened to surface.

Run: python scripts/audit_schema_drift.py
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect

DEV = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao"
)
FRESH = "postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao_test"

SCHEMAS = ("reference", "admission", "candidate", "recommendation")


def snapshot(url: str) -> dict:
    inspector = inspect(create_engine(url))
    data: dict[str, dict] = {}
    for schema in SCHEMAS:
        data[schema] = {}
        for table in inspector.get_table_names(schema=schema):
            columns = {
                col["name"]: (str(col["type"]), bool(col["nullable"]), col.get("default"))
                for col in inspector.get_columns(table, schema=schema)
            }
            indexes = {
                ix["name"]: (tuple(ix["column_names"]), bool(ix.get("unique")))
                for ix in inspector.get_indexes(table, schema=schema)
                if ix["name"]
            }
            uniques = {
                uc["name"]: tuple(uc["column_names"])
                for uc in inspector.get_unique_constraints(table, schema=schema)
                if uc["name"]
            }
            checks = {
                ck["name"] for ck in inspector.get_check_constraints(table, schema=schema)
                if ck["name"]
            }
            data[schema][table] = {
                "columns": columns,
                "indexes": indexes,
                "uniques": uniques,
                "checks": checks,
            }
    return data


def main() -> int:
    print("读取两个库的结构（测试库为新建并完整迁移）…")
    dev = snapshot(DEV)
    fresh = snapshot(FRESH)
    print()

    differences = 0

    for schema in SCHEMAS:
        dev_tables = set(dev[schema])
        fresh_tables = set(fresh[schema])
        for table in sorted(dev_tables - fresh_tables):
            print(f"[表] 仅开发库有: {schema}.{table}")
            differences += 1
        for table in sorted(fresh_tables - dev_tables):
            print(f"[表] !! 仅测试库有（开发库缺失）: {schema}.{table}")
            differences += 1

        for table in sorted(dev_tables & fresh_tables):
            d, f = dev[schema][table], fresh[schema][table]

            for name in sorted(set(d["columns"]) - set(f["columns"])):
                print(f"[列] 仅开发库有: {schema}.{table}.{name}")
                differences += 1
            for name in sorted(set(f["columns"]) - set(d["columns"])):
                print(f"[列] !! 开发库缺失: {schema}.{table}.{name}")
                differences += 1
            for name in sorted(set(d["columns"]) & set(f["columns"])):
                if d["columns"][name][:2] != f["columns"][name][:2]:
                    print(
                        f"[列类型] {schema}.{table}.{name}: "
                        f"开发库 {d['columns'][name][:2]} vs 测试库 {f['columns'][name][:2]}"
                    )
                    differences += 1

            for name in sorted(set(f["indexes"]) - set(d["indexes"])):
                print(f"[索引] !! 开发库缺失: {schema}.{table}.{name} {f['indexes'][name][0]}")
                differences += 1
            for name in sorted(set(d["indexes"]) - set(f["indexes"])):
                print(f"[索引] 仅开发库有: {schema}.{table}.{name}")
                differences += 1
            for name in sorted(set(d["indexes"]) & set(f["indexes"])):
                if d["indexes"][name][0] != f["indexes"][name][0]:
                    print(
                        f"[索引列] {schema}.{table}.{name}: "
                        f"{d['indexes'][name][0]} vs {f['indexes'][name][0]}"
                    )
                    differences += 1

            for name in sorted(set(f["uniques"]) - set(d["uniques"])):
                print(f"[唯一约束] !! 开发库缺失: {schema}.{table}.{name}")
                differences += 1
            for name in sorted(set(f["checks"]) - set(d["checks"])):
                print(f"[CHECK] !! 开发库缺失: {schema}.{table}.{name}")
                differences += 1

    print()
    print("=" * 100)
    print(f"差异合计: {differences} 处")
    print("=" * 100)
    if differences == 0:
        print("两个库结构一致。")
    else:
        print("开发库与「由迁移文件新建的库」不一致。")
        print("含义：迁移文件在应用之后被修改过，alembic_version 不再描述真实结构；")
        print("且没有任何测试或 CI 步骤会发现这件事。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
