import sys, os
sys.path.insert(0, '/home/bisheng/work/weMiniApp/Backend')
os.environ['JWT_SECRET'] = 'c7dedee911898aa98d78347653aa235eb8f3d539e2ad58db828ad2458b4543ae'
os.environ['DATABASE_URL'] = 'postgresql+asyncpg://bisheng@127.0.0.1:3306/wemini_app_dev'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0'
from app.port.config import settings
print(f"DATABASE_URL={settings.DATABASE_URL}")
print(f"DB_HOST={settings.DB_HOST}")
print(f"DB_PORT={settings.DB_PORT}")
print(f"DB_USER={settings.DB_USER}")
