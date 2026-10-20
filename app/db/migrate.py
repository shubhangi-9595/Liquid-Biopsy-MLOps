"""Apply database migrations (alembic upgrade head).

Runs automatically when the API starts (RUN_MIGRATIONS=true), so a new image that
needs a schema change migrates the database on deploy. On Postgres a session-level
advisory lock makes this safe when several replicas start at the same time: one
migrates, the others wait and then find nothing left to do.

Run manually:  DATABASE_URL=... python -m app.db.migrate
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .session import database_url, get_engine

log = logging.getLogger("liquid-biopsy-api.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ADVISORY_LOCK_ID = 727_272_001  # any constant shared by all replicas


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def run_migrations(url: str) -> None:
    engine = get_engine(url)
    with engine.connect() as conn:
        is_pg = conn.dialect.name == "postgresql"
        if is_pg:
            conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": ADVISORY_LOCK_ID})
            conn.commit()
        try:
            cfg = alembic_config(url)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            if is_pg:
                conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": ADVISORY_LOCK_ID})
                conn.commit()
    log.info("Database schema is up to date")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    url = database_url()
    if not url:
        print("DATABASE_URL is not set")
        return 1
    run_migrations(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
