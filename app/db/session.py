"""Engine and session handling. The database is optional: without DATABASE_URL the
API still serves predictions, it just doesn't store them."""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def normalize_url(url: str) -> str:
    """Use the psycopg 3 driver for any Postgres URL (Azure gives plain postgresql:// URLs)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def database_url() -> str | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    return normalize_url(url) if url else None


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    kwargs = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    return create_engine(url, **kwargs)


def get_sessionmaker(url: str) -> sessionmaker:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)
