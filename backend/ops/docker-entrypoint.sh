#!/bin/sh
# Dev container entrypoint: wait for the DB, apply migrations, optionally seed, then exec CMD.
set -e

if [ "${AUTO_MIGRATE:-false}" = "true" ]; then
  echo "[entrypoint] waiting for database ..."
  python - <<'PY'
import os, sys, time
import psycopg

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
for attempt in range(60):
    try:
        with psycopg.connect(url, connect_timeout=3):
            print("[entrypoint] database is up")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - retry loop
        if attempt == 59:
            raise
        time.sleep(2)
PY

  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head

  if [ "${AUTO_SEED:-true}" = "true" ]; then
    echo "[entrypoint] seeding demo dataset (idempotent)"
    python -m app.ingestion.seed_demo || echo "[entrypoint] seed skipped/failed (non-fatal)"
  fi
fi

exec "$@"
