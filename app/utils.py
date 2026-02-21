from __future__ import annotations

from typing import Any


def normalize_keyword_triggers(raw_value: Any) -> tuple[str, ...]:
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, tuple):
        values = list(raw_value)
    elif raw_value is None:
        values = []
    else:
        values = [raw_value]

    normalized = [str(value).strip().lower() for value in values if str(value).strip()]
    return tuple(normalized)
