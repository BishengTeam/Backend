"""Process-wide test configuration.

Application settings are instantiated while test modules are imported.  Keep
tests independent from developer machine dotenv files by installing explicit,
non-production defaults before collection starts.
"""

import os


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
