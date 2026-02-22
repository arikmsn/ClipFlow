from __future__ import annotations

from collections.abc import Generator
from typing import Any

from app.db import get_db_connection


def get_db() -> Generator[Any, None, None]:
    with get_db_connection() as connection:
        yield connection
