"""检测并修复：表已有但迁移版本表为空的情况（DB卷持久化场景）"""
import os
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus


def build_url() -> str:
    """从环境变量构建同步数据库 URL"""
    url = os.environ.get('DATABASE_URL_SYNC', '')
    if url:
        return url
    # 从组件构建
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
    tables = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).fetchall()
    table_count = len(tables)
    version_count = c.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
    if table_count > 5 and version_count == 0:
        print(f'Found {table_count} tables but no migration history — stamping head')
        c.execute(text("DELETE FROM alembic_version"))
        c.execute(text("INSERT INTO alembic_version VALUES ('m001_merge_heads')"))
        c.commit()
        print('Stamped m001_merge_heads')
    else:
        print(f'Alembic state OK: {table_count} tables, {version_count} versions')
eng.dispose()
