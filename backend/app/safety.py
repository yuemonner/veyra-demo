"""Guards against destroying recorded investigation data.

The legacy demo reset drops every table. Runtime case records (sources, runs, append-only
events) must never be destroyed by it outside an explicitly disposable test database.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional

RESET_ALLOW_FLAG = "VEYRA_ALLOW_DESTRUCTIVE_TEST_RESET"
PROTECTED_TABLES = ("runtime_cases", "runtime_sources", "runtime_runs")


class ProtectedDataError(RuntimeError):
    """Raised when an operation would delete protected investigation records."""


def sqlite_path(database_url: str) -> Optional[Path]:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw = database_url[len(prefix):]
    if not raw or raw == ":memory:":
        return None
    return Path(raw).expanduser().resolve()


def is_disposable_test_database(database_url: str, temp_root: Optional[str] = None) -> bool:
    """True only for an in-memory SQLite DB or a SQLite file inside the system temp directory."""
    if database_url in ("sqlite://", "sqlite:///:memory:"):
        return True
    path = sqlite_path(database_url)
    if path is None:
        return False
    root = Path(temp_root or tempfile.gettempdir()).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def reset_decision(
    protected_rows: int,
    database_url: str,
    env: Mapping[str, str],
    temp_root: Optional[str] = None,
) -> tuple[bool, str]:
    """Pure decision: may a destructive demo reset run?"""
    if protected_rows <= 0:
        return True, "No runtime investigation records exist."
    if env.get(RESET_ALLOW_FLAG) == "true" and is_disposable_test_database(database_url, temp_root):
        return True, "Disposable test database explicitly allowed."
    return False, (
        f"Refusing destructive reset: {protected_rows} runtime investigation record(s) exist. "
        "Recorded sources, runs and events are preserved."
    )


def count_protected_rows(engine) -> int:
    """Count rows in protected tables that exist. Imported lazily to keep the pure functions dependency-free."""
    from sqlalchemy import inspect, text

    existing = set(inspect(engine).get_table_names())
    total = 0
    with engine.connect() as connection:
        for table in PROTECTED_TABLES:
            if table in existing:
                total += int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    return total


def assert_reset_permitted(engine, database_url: str, env: Optional[Mapping[str, str]] = None) -> None:
    allowed, reason = reset_decision(count_protected_rows(engine), database_url, env if env is not None else os.environ)
    if not allowed:
        raise ProtectedDataError(reason)
