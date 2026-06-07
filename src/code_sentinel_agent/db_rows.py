from __future__ import annotations

import json
from typing import Any


def json_object(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def sqlite_table_columns(conn: Any, table: str) -> list[str]:
    return [column[1] for column in conn.execute(f"pragma table_info({table})").fetchall()]


def sqlite_row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    return dict(zip(columns, row, strict=True))


def validate_status(status: str, allowed: set[str], *, label: str) -> None:
    if status not in allowed:
        raise ValueError(f"invalid {label} status: {status}")
