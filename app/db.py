from __future__ import annotations

import os
import traceback
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse


def _normalize_postgres_dsn(database_url: str) -> str:
    parsed = urlparse(database_url)
    scheme = parsed.scheme
    if scheme in {"postgres", "postgresql"}:
        return database_url
    if scheme in {"postgres+asyncpg", "postgresql+asyncpg"}:
        return database_url.replace("+asyncpg", "", 1)
    raise ValueError(f"Unsupported database URL scheme: {scheme}")


@contextmanager
def get_db_connection() -> Iterator[object]:
    """Yield a psycopg2 PostgreSQL connection (Supabase-compatible)."""

    import psycopg2

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is not configured")

    dsn = _normalize_postgres_dsn(database_url)
    connection = None
    try:
        connection = psycopg2.connect(dsn)
        yield connection
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise
    finally:
        if connection is not None:
            connection.close()
