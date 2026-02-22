from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

from app.config import settings


def _db_driver_from_url(database_url: str) -> str:
    scheme = urlparse(database_url).scheme
    if scheme.endswith("+asyncpg"):
        return "asyncpg"
    return "psycopg2"


@contextmanager
def get_db_connection() -> Iterator[object]:
    """Return a database connection chosen from the configured DATABASE_URL.

    - postgresql://...          -> psycopg2 connection
    - postgresql+asyncpg://...  -> asyncpg connection
    """

    driver = _db_driver_from_url(settings.database_url)

    if driver == "asyncpg":
        import asyncio
        import asyncpg

        async def _connect():
            return await asyncpg.connect(settings.database_url)

        async def _close(conn: asyncpg.Connection) -> None:
            await conn.close()

        connection = asyncio.run(_connect())
        try:
            yield connection
        finally:
            asyncio.run(_close(connection))
        return

    import psycopg2

    connection = psycopg2.connect(settings.database_url)
    try:
        yield connection
    finally:
        connection.close()
