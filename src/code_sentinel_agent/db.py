from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = RUNTIME_ROOT / "migrations"


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("pragma foreign_keys = on")
    try:
        yield conn
    finally:
        conn.close()


def initialize_database(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            create table if not exists schema_migrations (
              version text primary key,
              applied_at text not null default (datetime('now'))
            )
            """
        )
        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = migration_path.stem
            already_applied = conn.execute(
                "select 1 from schema_migrations where version = ?",
                (version,),
            ).fetchone()
            if already_applied:
                continue
            conn.executescript(migration_path.read_text(encoding="utf-8"))
            conn.execute(
                "insert into schema_migrations(version) values (?)",
                (version,),
            )
        conn.commit()

