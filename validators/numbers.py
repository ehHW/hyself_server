from __future__ import annotations


def parse_optional_positive_int(raw_value) -> int | None:
    if raw_value in [None, "", "null", "None"]:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None