"""Test database isolation.

Runs before any test module imports the application. Every pytest session gets its own
throwaway SQLite file inside the system temp directory. The suite refuses to run if the
application ends up bound to any other database, so local records under .local-runtime/
or a configured DATABASE_URL can never be reset by tests.
"""
import os
import shutil
import tempfile

import pytest

_TEST_DIR = tempfile.mkdtemp(prefix="veyra-pytest-")
TEST_DATABASE_URL = f"sqlite:///{_TEST_DIR}/veyra_test.db"

# Override unconditionally: an inherited DATABASE_URL (for example from a preview shell) is ignored.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["VEYRA_TEST_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["VEYRA_ALLOW_DESTRUCTIVE_TEST_RESET"] = "true"
# The legacy reset is refused while the runtime is enabled; tests enable it per test.
os.environ.pop("VEYRA_ENABLE_RUNTIME", None)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def _refuse_non_test_database():
    try:
        from app import db as app_db  # noqa: WPS433 (imported lazily on purpose)
    except ModuleNotFoundError:
        # Pure domain tests run without the web stack installed.
        yield
        return
    if app_db.DATABASE_URL != TEST_DATABASE_URL:
        pytest.exit(
            f"Refusing to run: application database is {app_db.DATABASE_URL!r}, "
            f"expected the isolated test database {TEST_DATABASE_URL!r}.",
            returncode=3,
        )
    yield
