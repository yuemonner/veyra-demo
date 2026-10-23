import tempfile
from pathlib import Path

from app.safety import RESET_ALLOW_FLAG, is_disposable_test_database, reset_decision


def test_reset_allowed_when_no_runtime_records_exist():
    allowed, _ = reset_decision(0, "postgresql+psycopg://prod/veyra", {})
    assert allowed


def test_reset_refused_for_local_runtime_records_even_with_flag(tmp_path):
    project_db = "sqlite:////Users/someone/veyra-v0-demo/.local-runtime/veyra.db"
    allowed, reason = reset_decision(3, project_db, {RESET_ALLOW_FLAG: "true"})
    assert not allowed and "3 runtime investigation record(s)" in reason


def test_reset_refused_for_postgres_with_records_even_with_flag():
    allowed, _ = reset_decision(1, "postgresql+psycopg://user@host/db", {RESET_ALLOW_FLAG: "true"})
    assert not allowed


def test_reset_allowed_only_for_flagged_temp_database():
    temp_db = f"sqlite:///{Path(tempfile.gettempdir()) / 'veyra-pytest-x' / 'db.sqlite'}"
    assert reset_decision(5, temp_db, {RESET_ALLOW_FLAG: "true"})[0]
    assert not reset_decision(5, temp_db, {})[0]
    assert not reset_decision(5, temp_db, {RESET_ALLOW_FLAG: "yes"})[0]


def test_disposable_database_detection_rejects_relative_and_traversal_paths(tmp_path):
    root = str(tmp_path)
    assert is_disposable_test_database(f"sqlite:///{tmp_path}/a/b.db", root)
    assert not is_disposable_test_database(f"sqlite:///{tmp_path}/../escape.db", root)
    assert not is_disposable_test_database("sqlite:///./veyra.db", root)
    assert is_disposable_test_database("sqlite://", root)
