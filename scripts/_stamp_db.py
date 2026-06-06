"""检测并修复：表已有但迁移版本表为空的情况（DB卷持久化场景）"""
import os
from sqlalchemy import create_engine, text

url = os.environ.get('DATABASE_URL_SYNC', os.environ.get('DATABASE_URL', '').replace('+asyncpg', '+psycopg2'))
if not url:
    print('No database URL found, skipping stamp check')
    exit(0)

eng = create_engine(url)
with eng.connect() as c:
    tables = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).fetchall()
    table_count = len(tables)
    version_count = c.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
    if table_count > 5 and version_count == 0:
        print(f'Found {table_count} tables but no migration history — stamping head')
        c.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('b0c1d2e3f4a5')"))
        c.commit()
    else:
        print(f'Alembic state OK: {table_count} tables, {version_count} versions')
eng.dispose()
