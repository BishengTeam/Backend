from logging.config import fileConfig
import os

from sqlalchemy import pool

from alembic import context

if os.getenv("TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL_SYNC"):
    if os.getenv("TEST_DATABASE_URL"):
        os.environ.setdefault("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    if os.getenv("TEST_DATABASE_URL_SYNC"):
        os.environ.setdefault("DATABASE_URL_SYNC", os.environ["TEST_DATABASE_URL_SYNC"])
    os.environ.setdefault("JWT_SECRET", "test-secret")

from app.core.database import Base

# Import all models so Base.metadata knows about them
# models/__init__.py is the single source of truth — it imports every model class
import app.models  # noqa: F401

config = context.config

# 优先从 pydantic Settings 获取已组装的 URL（含 URL 编码的密码），不在 env var 中
from app.core.config import settings  # noqa: E402

database_url = (
    os.getenv("TEST_DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL_SYNC")
    or settings.DATABASE_URL_SYNC
    or config.get_main_option("sqlalchemy.url")
)
# 不存入 configparser，避免 %40 等 URL 编码被误解析为插值语法
_migration_db_url = database_url

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine
    connectable = create_engine(_migration_db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
