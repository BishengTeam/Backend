from urllib.parse import quote_plus
from sqlalchemy import create_engine, text

pw = quote_plus('bisheng6@6@6')
e = create_engine(f'postgresql://bisheng:{pw}@127.0.0.1:3306/wemini_app_dev_0607')
with e.connect() as conn:
    r = conn.execute(text('SELECT id FROM "user" WHERE openid=:oid'), {'oid': 'test_openid_user_001'})
    uid = r.scalar()
    print(uid)
    # Also generate token
    import sys, os
    sys.path.insert(0, '.')
    os.environ.setdefault('JWT_SECRET', 'smoke-jwt-minimum-32-chars-here!!!')
    from app.adapter.security import create_access_token
    token = create_access_token(uid, 'test_openid_user_001')
    print(f'TOKEN={token}')
