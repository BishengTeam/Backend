import os

import pytest


pytestmark = pytest.mark.integration_db


def test_postgresql_test_database_urls_are_configured():
    database_url = os.getenv("TEST_DATABASE_URL")
    database_url_sync = os.getenv("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip("PostgreSQL integration tests require TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC")

    assert database_url.startswith("postgresql+asyncpg://")
    assert database_url_sync.startswith("postgresql://")
