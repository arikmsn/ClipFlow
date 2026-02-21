from __future__ import annotations

from typing import Any


def get_db() -> Any:
    """Database dependency.

    Production wiring should override this with a real connection/session provider.
    """
    raise RuntimeError("get_db dependency is not configured")
