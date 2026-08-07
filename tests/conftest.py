"""Process-wide test configuration.

Application settings are instantiated while test modules are imported.  Keep
tests independent from developer machine dotenv files by installing explicit,
non-production defaults before collection starts.
"""

import os

import pytest


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault(
    "JWT_SECRET",
    "test-only-jwt-secret-that-is-at-least-32-characters",
)
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://test:test@127.0.0.1:5432/wemini_app_test",
    ),
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    os.getenv(
        "TEST_DATABASE_URL_SYNC",
        "postgresql://test:test@127.0.0.1:5432/wemini_app_test",
    ),
)


def pytest_collection_modifyitems(config, items):
    """Keep a local test run deterministic when PostgreSQL is unavailable.

    Database tests must never silently fall back to the developer database.  A
    missing pair of explicit test URLs therefore skips only tests marked
    ``integration_db``; unit tests still collect and run normally.
    """

    del config
    if os.getenv("TEST_DATABASE_URL") and os.getenv("TEST_DATABASE_URL_SYNC"):
        return

    skip_integration = pytest.mark.skip(
        reason=(
            "PostgreSQL integration tests require TEST_DATABASE_URL and "
            "TEST_DATABASE_URL_SYNC"
        )
    )
    for item in items:
        if item.get_closest_marker("integration_db") is not None:
            item.add_marker(skip_integration)
