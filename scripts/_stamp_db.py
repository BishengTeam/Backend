"""检测并修复：表已有但迁移版本表为空/不完整的情况（DB卷持久化场景）

当 Docker DB 卷持久化时，所有表已存在但 alembic_version 表可能为空。
此时直接标记所有 heads 为已应用，避免重复执行 DDL。
"""
import os
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus


# 所有版本号（按依赖顺序，从最旧到最新）
ALL_REVISIONS = [
    '659fd10dac61',
    '570fce4c60fc',
    'b0a64a214cb4',
    'b1c2d3e4f5a6',
    'c2d3e4f5a6b7',
    'd3e4f5a6b7c8',
    'e1f2a3b4c5d6',
    'f2a3b4c5d6e7',
    'a4b5c6d7e8f9',
    'b6c7d8e9f0a1',
    'c8d9e0f1a2b3',
    'e8f9a0b1c2d3',
    'b1c2d3e4f5a7',
    'a0b1c2d3e4f5',
    'd6e7f8a9b0c1',
    'e7f8a9b0c1d2',
    'f8a9b0c1d2e3',
    'c5d6e7f8a9b0',
    'b0c1d2e3f4a5',
    'f8e586b20e8c',
    'm001_merge_heads',
]


def build_url() -> str:
    """从环境变量构建同步数据库 URL"""
    url = os.environ.get('DATABASE_URL_SYNC', '')
    if url:
        return url
    db_user = os.environ.get('DB_USER', 'bisheng')
    db_password = os.environ.get('DB_PASSWORD', '')
    db_host = os.environ.get('DB_HOST', 'localhost')
    db_port = os.environ.get('DB_PORT', '5432')
    db_name = os.environ.get('DB_NAME', 'wemini_app_dev')
    encoded = quote_plus(db_password)
    return f"postgresql://{db_user}:{encoded}@{db_host}:{db_port}/{db_name}"


url = build_url()
print(f'Connecting to: postgresql://***@{url.split("@")[-1]}')

eng = create_engine(url)
with eng.connect() as c:
    tables = c.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    ).fetchall()
    table_count = len(tables)
    version_count = c.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()

    print(f'Tables: {table_count}, alembic versions: {version_count}')

    if table_count > 5 and version_count == 0:
        print('Tables exist but no migration history — stamping all revisions')
        for rev in ALL_REVISIONS:
            c.execute(text("INSERT INTO alembic_version VALUES (:rev)"), {"rev": rev})
        c.commit()
        print(f'Stamped {len(ALL_REVISIONS)} revisions')
    else:
        print('Alembic state OK, skipping stamp')

eng.dispose()
