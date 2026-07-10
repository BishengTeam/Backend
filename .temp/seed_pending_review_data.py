"""生成待审核测试数据 — 所有 target_type"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio

os.environ.setdefault("DB_USER", "bisheng")
os.environ.setdefault("DB_PASSWORD", "bisheng6@6@6")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "wemini_app_dev")
os.environ.setdefault("JWT_SECRET", "c7dedee911898aa98d78347653aa235eb8f3d539e2ad58db828ad2458b4543ae")

from app.adapter.database import get_db_ctx
from sqlalchemy import text
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)

async def seed():
    async with get_db_ctx() as db:
        # 先查现有用户
        users = (await db.execute(text("SELECT id, openid FROM \"user\" ORDER BY id LIMIT 5"))).fetchall()
        uid_list = [u[0] for u in users]
        print(f"现有用户: {uid_list}")

        inserted = []

        # ── 1. UserRealname 待审核 ──
        for uid in uid_list[:3]:
            exists = (await db.execute(
                text("SELECT 1 FROM user_realname WHERE user_id = :uid"), {"uid": uid}
            )).scalar()
            if not exists:
                await db.execute(text("""
                    INSERT INTO user_realname (user_id, real_name, id_card_number, gender, status, created_at, updated_at)
                    VALUES (:uid, :name, :idcard, :gender, 'pending', :now, :now)
                """), {
                    "uid": uid,
                    "name": f"测试用户{uid}",
                    "idcard": f"11010119900101{uid:04d}",
                    "gender": "男" if uid % 2 else "女",
                    "now": now,
                })
                inserted.append(f"UserRealname(user_id={uid})")

        # ── 2. UserStudent 待审核 ──
        for uid in uid_list[:2]:
            exists = (await db.execute(
                text("SELECT 1 FROM user_student WHERE user_id = :uid"), {"uid": uid}
            )).scalar()
            if not exists:
                await db.execute(text("""
                    INSERT INTO user_student (user_id, school, major, education, status, created_at, updated_at)
                    VALUES (:uid, :school, :major, :edu, 'pending', :now, :now)
                """), {
                    "uid": uid,
                    "school": "清华大学",
                    "major": "计算机科学",
                    "edu": "本科",
                    "now": now,
                })
                inserted.append(f"UserStudent(user_id={uid})")

        # ── 3. UserEnterprise 待审核 ──
        for uid in uid_list[2:4]:
            exists = (await db.execute(
                text("SELECT 1 FROM user_enterprise WHERE user_id = :uid"), {"uid": uid}
            )).scalar()
            if not exists:
                await db.execute(text("""
                    INSERT INTO user_enterprise (user_id, company_name, position, status, created_at, updated_at)
                    VALUES (:uid, :company, :position, 'pending', :now, :now)
                """), {
                    "uid": uid,
                    "company": "字节跳动",
                    "position": "工程师",
                    "now": now,
                })
                inserted.append(f"UserEnterprise(user_id={uid})")

        # ── 4. 创建 paid 订单（需要先有 certification + inventory + price_config） ──
        # 查已有认证类型
        certs = (await db.execute(
            text("SELECT code, name FROM certification WHERE is_active = true LIMIT 3")
        )).fetchall()
        if certs:
            cert_code = certs[0][0]
            # 确保有 price_config
            pc = (await db.execute(
                text("SELECT 1 FROM price_config WHERE product_type = :code"), {"code": cert_code}
            )).scalar()
            if not pc:
                await db.execute(text("""
                    INSERT INTO price_config (product_type, user_type, price, is_active, created_at, updated_at)
                    VALUES (:code, 'student', 30000, true, :now, :now)
                """), {"code": cert_code, "now": now})

            # 确保有 inventory
            inv = (await db.execute(
                text("SELECT id FROM inventory WHERE cert_type = :code AND status = 'active' LIMIT 1"),
                {"code": cert_code}
            )).fetchone()
            inv_id = inv[0] if inv else None

            # 创建 paid 订单
            uid = uid_list[0]
            for i in range(3):
                await db.execute(text("""
                    INSERT INTO "order" (user_id, order_kind, product_type, candidate_name, candidate_phone,
                        price, status, out_trade_no, created_at, updated_at, expires_at, inventory_id)
                    VALUES (:uid, 'certification', :code, :name, :phone,
                        :price, 'paid', :trade_no, :now, :now, :exp, :inv)
                """), {
                    "uid": uid,
                    "code": cert_code,
                    "name": f"考生{uid}_{i}",
                    "phone": f"1380013{8000+uid+i:04d}",
                    "price": 30000,
                    "trade_no": f"test_paid_{uid}_{i}_{int(now.timestamp())}",
                    "exp": now + timedelta(days=1),
                    "inv": inv_id,
                    "now": now,
                })
                inserted.append(f"Order(user_id={uid}, status=paid) #{i+1}")

        await db.commit()

        print(f"\n写入 {len(inserted)} 条待审核数据:")
        for s in inserted: print(f"  + {s}")

        # 验证
        counts = {}
        for t in ["user_realname", "user_student", "user_enterprise"]:
            r = await db.execute(text(f"SELECT count(*) FROM {t} WHERE status = 'pending'"))
            counts[t] = r.scalar()
        r = await db.execute(text("SELECT count(*) FROM \"order\" WHERE status = 'paid'"))
        counts["order(paid)"] = r.scalar()
        print(f"\n待审核数据统计:")
        for k, v in counts.items(): print(f"  {k}: {v}")

asyncio.run(seed())
