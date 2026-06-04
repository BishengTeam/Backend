"""测试数据初始化：用户 / 订单 / 积分 / 库存 / 课程 / 优惠券 / Banner / Zone / 快速提问

用法：
    python scripts/seed_testdata.py          # 幂等：已存在则跳过
    python scripts/seed_testdata.py --force  # 先清理再重建
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.adapter.database import async_session_factory
from app.domain.user.src.index import AdminUser, PointsHistory, User, UserIdentity, UserPoints
from app.models.banner import Banner
from app.domain.certification.src.index import Certification, Course, CourseEnrollment
from app.domain.order.src.index import Coupon, Inventory, Order, PriceConfig, UserCoupon
from app.domain.community.src.index import QuickQuestion
from app.models.zone import Zone

# ── 测试用户定义 ──────────────────────────────────────────────
TEST_USERS = [
    {
        "openid": "test_openid_user_001",
        "phone": "13800000001",
        "is_active": True,
        "identity": {"user_type": "student", "real_name": "张三", "id_card_number": "110101199001011234"},
    },
    {
        "openid": "test_openid_user_002",
        "phone": "13800000002",
        "is_active": True,
        "identity": {"user_type": "enterprise", "real_name": "李四", "id_card_number": "110101199102022345"},
    },
    {
        "openid": "test_openid_user_003",
        "phone": "13800000003",
        "is_active": True,
        "identity": {"user_type": "student", "real_name": "王五", "id_card_number": "110101199203033456"},
    },
    {
        "openid": "test_openid_user_004",
        "phone": "13800000004",
        "is_active": True,
        "identity": None,  # 未实名
    },
    {
        "openid": "test_openid_user_005",
        "phone": "13800000005",
        "is_active": False,  # 已注销
        "identity": None,
    },
]

TEST_OPENIDS = [u["openid"] for u in TEST_USERS]
NOW = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# 清理
# ═══════════════════════════════════════════════════════════════
async def clean_test_data(db):
    """删除所有测试数据：按外键依赖逆序删除。"""
    # 1. 找到测试用户 ID
    result = await db.execute(select(User.id).where(User.openid.in_(TEST_OPENIDS)))
    user_ids = [row[0] for row in result.all()]

    # 2. 删除用户关联数据
    user_dependent_tables = [
        "points_history",
        "user_points",
        "user_identity",
        "user_coupon",
        "course_enrollment",
        '"order"',
        "ticket",
        "agreement",
        "quiz_record",
        "quiz_checkin",
        "competition_reg",
        "conversation",
    ]
    if user_ids:
        for table in user_dependent_tables:
            await db.execute(text(f"DELETE FROM {table} WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text('DELETE FROM "user" WHERE id = ANY(:ids)'), {"ids": user_ids})

    # 3. 删除全局测试数据（按依赖顺序）
    await db.execute(text("DELETE FROM inventory_record"))
    await db.execute(text("DELETE FROM inventory"))
    await db.execute(text("DELETE FROM user_coupon"))  # 可能还有残留
    await db.execute(text("DELETE FROM coupon"))
    await db.execute(text("DELETE FROM course_enrollment"))
    await db.execute(text("DELETE FROM course"))
    await db.execute(text("DELETE FROM banner"))
    await db.execute(text("DELETE FROM zone"))
    await db.execute(text("DELETE FROM quick_question"))
    await db.commit()
    print("  🧹 测试数据已清理")


# ═══════════════════════════════════════════════════════════════
# 第 2 层：用户生态
# ═══════════════════════════════════════════════════════════════
async def seed_users(db) -> dict[str, int]:
    """创建 5 个测试用户，返回 {openid: user_id} 映射。"""
    user_map: dict[str, int] = {}
    for u in TEST_USERS:
        user = User(openid=u["openid"], phone=u["phone"], is_active=u["is_active"])
        db.add(user)
        await db.flush()
        user_map[u["openid"]] = user.id

    await db.commit()
    print(f"  ✓ 测试用户 ({len(TEST_USERS)} 个)")
    return user_map


async def seed_user_identities(db, user_map: dict[str, int]):
    """为有实名信息的用户创建 UserIdentity。"""
    count = 0
    for u in TEST_USERS:
        if u["identity"] is None:
            continue
        ident = UserIdentity(
            user_id=user_map[u["openid"]],
            user_type=u["identity"]["user_type"],
            real_name=u["identity"]["real_name"],
            id_card_number=u["identity"]["id_card_number"],
            status="verified",
            verified_at=NOW.replace(microsecond=0).isoformat(),
        )
        db.add(ident)
        count += 1
    await db.commit()
    print(f"  ✓ 实名认证 ({count} 条)")


async def seed_user_points(db, user_map: dict[str, int]):
    """每个用户 1000 初始积分 + 一条 new_user_bonus 流水。"""
    for openid, uid in user_map.items():
        # 积分余额
        db.add(UserPoints(user_id=uid, balance=1000))
        # 初始赠送流水（source 字段留空避免唯一约束冲突）
        db.add(
            PointsHistory(
                user_id=uid,
                action_type="new_user_bonus",
                amount=1000,
                balance_after=1000,
                description="新用户注册赠送",
            )
        )
    await db.commit()
    print(f"  ✓ 积分余额 + 流水 ({len(user_map)} 组)")


# ═══════════════════════════════════════════════════════════════
# 第 3 层：可交易资源
# ═══════════════════════════════════════════════════════════════
async def seed_inventory(db):
    """为每个 Certification 建一条 Inventory。"""
    result = await db.execute(select(Certification).where(Certification.is_active.is_(True)))
    certs = result.scalars().all()
    for c in certs:
        db.add(
            Inventory(
                inventory_type="certification",
                ref_code=c.code,
                total_quota=100,
                available_quota=100,
                locked_quota=0,
                sold_quota=0,
            )
        )
    await db.commit()
    print(f"  ✓ 库存配额 ({len(certs)} 条)")


async def seed_courses_and_enrollments(db, user_map: dict[str, int]):
    """3 门课程 + 2 条选课记录。"""
    courses_data = [
        {
            "title": "H3C 路由交换实战",
            "category": "H3C",
            "description": "H3C 网络工程师认证备考课程，涵盖路由交换核心知识",
            "price": 380000,
            "batches": [
                {"name": "第 1 期", "start": "2026-03-01", "end": "2026-06-01"},
                {"name": "第 2 期", "start": "2026-07-01", "end": "2026-10-01"},
            ],
            "teacher_name": "赵老师",
            "teacher_contact": "teacher_zhao@example.com",
        },
        {
            "title": "深信服安全工程师精讲",
            "category": "Sangfor",
            "description": "深信服网络安全工程师认证精讲课程",
            "price": 59800,
            "batches": [
                {"name": "春季班", "start": "2026-03-15", "end": "2026-05-15"},
            ],
            "teacher_name": "钱老师",
        },
        {
            "title": "NISP 一级冲刺班",
            "category": "NISP",
            "description": "NISP 一级认证考前冲刺，考点全覆盖",
            "price": 69800,
            "batches": [
                {"name": "5 月冲刺", "start": "2026-05-01", "end": "2026-05-30"},
                {"name": "6 月冲刺", "start": "2026-06-01", "end": "2026-06-30"},
            ],
            "teacher_name": "孙老师",
        },
    ]
    course_ids: list[int] = []
    for cd in courses_data:
        course = Course(
            title=cd["title"],
            category=cd["category"],
            description=cd["description"],
            price=cd["price"],
            batches=cd.get("batches"),
            teacher_name=cd.get("teacher_name"),
            teacher_contact=cd.get("teacher_contact"),
        )
        db.add(course)
        await db.flush()
        course_ids.append(course.id)

    # 选课：user_001 → course_1, user_002 → course_2
    enrollments = [
        (user_map["test_openid_user_001"], course_ids[0], "第 2 期"),
        (user_map["test_openid_user_002"], course_ids[1], "春季班"),
    ]
    for uid, cid, batch in enrollments:
        db.add(CourseEnrollment(user_id=uid, course_id=cid, batch_selected=batch, status="enrolled"))

    await db.commit()
    print(f"  ✓ 课程 ({len(courses_data)} 门)")
    print(f"  ✓ 选课 ({len(enrollments)} 条)")


async def seed_coupons_and_user_coupons(db, user_map: dict[str, int]):
    """3 张优惠券 + 2 条用户券（1 未使用 + 1 已使用）。"""
    coupons_data = [
        {"code": "TEST-COUPON-10", "type": "fixed", "value": 10000, "min_order_amount": 100000,
         "valid_from": NOW - timedelta(days=30), "valid_to": NOW + timedelta(days=60)},
        {"code": "TEST-COUPON-20", "type": "fixed", "value": 20000, "min_order_amount": 200000,
         "valid_from": NOW - timedelta(days=30), "valid_to": NOW + timedelta(days=60)},
        {"code": "TEST-COUPON-EXPIRED", "type": "fixed", "value": 5000, "min_order_amount": 50000,
         "valid_from": NOW - timedelta(days=90), "valid_to": NOW - timedelta(days=1)},
    ]
    coupon_ids: list[int] = []
    for cd in coupons_data:
        c = Coupon(**cd)
        db.add(c)
        await db.flush()
        coupon_ids.append(c.id)

    # user_001 → COUPON-10 (未使用), user_002 → COUPON-20 (已使用)
    db.add(UserCoupon(user_id=user_map["test_openid_user_001"], coupon_id=coupon_ids[0], status="unused"))
    db.add(UserCoupon(
        user_id=user_map["test_openid_user_002"],
        coupon_id=coupon_ids[1],
        status="used",
        used_at=NOW - timedelta(days=3),
    ))
    await db.commit()
    print(f"  ✓ 优惠券 ({len(coupons_data)} 张)")
    print(f"  ✓ 用户优惠券 (2 条)")


async def seed_banners(db):
    """3 个 Banner：生效中 / 未开始 / 已过期。"""
    banners = [
        Banner(
            image_url="https://example.com/banner/active.jpg",
            jump_link="/pages/active/index",
            sort=1,
            start_time=NOW - timedelta(days=7),
            end_time=NOW + timedelta(days=7),
            is_active=True,
        ),
        Banner(
            image_url="https://example.com/banner/upcoming.jpg",
            jump_link="/pages/upcoming/index",
            sort=2,
            start_time=NOW + timedelta(days=7),
            end_time=NOW + timedelta(days=30),
            is_active=True,
        ),
        Banner(
            image_url="https://example.com/banner/expired.jpg",
            jump_link="/pages/expired/index",
            sort=3,
            start_time=NOW - timedelta(days=30),
            end_time=NOW - timedelta(days=1),
            is_active=False,
        ),
    ]
    db.add_all(banners)
    await db.commit()
    print(f"  ✓ Banner ({len(banners)} 个)")


async def seed_zones(db):
    """4 个 Zone，不同 zone_type。"""
    zones = [
        Zone(zone_type="certification", title="认证考试专区", description="H3C / 深信服 / NISP 认证", sort_order=1),
        Zone(zone_type="course", title="精品课程", description="名师授课，系统学习", sort_order=2),
        Zone(zone_type="competition", title="竞赛报名", description="全国职业技能大赛", sort_order=3),
        Zone(zone_type="activity", title="限时活动", description="优惠券发放，积分兑换", sort_order=4),
    ]
    db.add_all(zones)
    await db.commit()
    print(f"  ✓ Zone ({len(zones)} 个)")


# ═══════════════════════════════════════════════════════════════
# 第 4 层：业务活动数据
# ═══════════════════════════════════════════════════════════════
async def seed_orders(db, user_map: dict[str, int]):
    """5 条订单，覆盖所有状态。"""
    # 获取第一条 inventory 的 id
    inv_result = await db.execute(select(Inventory).limit(1))
    inv = inv_result.scalars().first()
    inv_id = inv.id if inv else None

    # 获取第一条 cert
    cert_result = await db.execute(select(Certification).limit(1))
    cert = cert_result.scalars().first()
    cert_type = cert.name if cert else "h3c_ne"

    base = {
        "user_id": user_map["test_openid_user_001"],
        "inventory_id": inv_id,
        "cert_type": cert_type,
        "candidate_name": "张三",
        "candidate_phone": "13800000001",
        "candidate_idcard": "110101199001011234",
        "price": 380000,
    }

    orders = [
        # 订单 1: pending — 接近过期
        {
            **base,
            "status": "pending",
            "out_trade_no": "TEST-OTN-PENDING-001",
            "expires_at": NOW + timedelta(minutes=5),
        },
        # 订单 2: paid
        {
            **base,
            "status": "paid",
            "out_trade_no": "TEST-OTN-PAID-002",
            "transaction_id": "TEST-TXN-PAID-002",
            "paid_at": NOW - timedelta(hours=2),
            "expires_at": NOW + timedelta(days=1),
        },
        # 订单 3: completed
        {
            **base,
            "user_id": user_map["test_openid_user_002"],
            "candidate_name": "李四",
            "candidate_phone": "13800000002",
            "candidate_idcard": "110101199102022345",
            "price": 480000,
            "status": "completed",
            "out_trade_no": "TEST-OTN-COMPLETED-003",
            "transaction_id": "TEST-TXN-COMPLETED-003",
            "paid_at": NOW - timedelta(days=7),
            "expires_at": NOW + timedelta(days=1),
        },
        # 订单 4: refunded
        {
            **base,
            "status": "refunded",
            "out_trade_no": "TEST-OTN-REFUNDED-004",
            "transaction_id": "TEST-TXN-REFUNDED-004",
            "paid_at": NOW - timedelta(days=14),
            "expires_at": NOW + timedelta(days=1),
        },
        # 订单 5: closed — 过期自动关闭
        {
            **base,
            "user_id": user_map["test_openid_user_003"],
            "candidate_name": "王五",
            "candidate_phone": "13800000003",
            "candidate_idcard": "110101199203033456",
            "price": 380000,
            "status": "closed",
            "out_trade_no": "TEST-OTN-CLOSED-005",
            "expires_at": NOW - timedelta(days=1),
            "closed_at": NOW - timedelta(days=1),
            "close_reason": "支付超时自动关闭",
        },
    ]
    for o in orders:
        db.add(Order(**o))
    await db.commit()
    print(f"  ✓ 订单 ({len(orders)} 条)")


async def seed_extra_points_history(db, user_map: dict[str, int]):
    """追加 purchase_bonus 和 redeem 两条积分流水。"""
    uid = user_map["test_openid_user_002"]
    # purchase_bonus: 下单赠送
    db.add(
        PointsHistory(
            user_id=uid,
            action_type="purchase_bonus",
            amount=380,
            balance_after=1380,
            description="购买认证赠送积分",
            source_type="order",
            source_id="TEST-OTN-COMPLETED-003",
        )
    )
    # redeem: 兑换优惠券
    db.add(
        PointsHistory(
            user_id=uid,
            action_type="redeem",
            amount=-100,
            balance_after=1280,
            description="兑换优惠券",
            source_type="coupon",
            source_id="TEST-COUPON-20",
        )
    )
    await db.commit()
    print(f"  ✓ 积分流水追加 (2 条)")


async def seed_quick_questions(db):
    """5 条快速提问。"""
    questions = [
        QuickQuestion(question_text="H3C 认证考试报名流程是什么？", category="certification", sort_order=1),
        QuickQuestion(question_text="课程支持回放吗？", category="course", sort_order=2),
        QuickQuestion(question_text="如何获取发票？", category="order", sort_order=3),
        QuickQuestion(question_text="积分如何使用？", category="points", sort_order=4),
        QuickQuestion(question_text="已废弃的问题", category="other", sort_order=5, is_active=False),
    ]
    db.add_all(questions)
    await db.commit()
    print(f"  ✓ 快速提问 ({len(questions)} 条)")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════
async def main():
    force = "--force" in sys.argv

    async with async_session_factory() as db:
        # ── 幂等检查 ──
        if not force:
            result = await db.execute(
                select(User).where(User.openid.in_(TEST_OPENIDS))
            )
            existing = result.scalars().first()
            if existing:
                print("测试数据已存在，跳过。（使用 --force 强制重建）")
                return

        # ── 清理 ──
        if force:
            await clean_test_data(db)

        print("初始化测试数据...")

        # 第 2 层
        user_map = await seed_users(db)
        await seed_user_identities(db, user_map)
        await seed_user_points(db, user_map)

        # 第 3 层
        await seed_inventory(db)
        await seed_courses_and_enrollments(db, user_map)
        await seed_coupons_and_user_coupons(db, user_map)
        await seed_banners(db)
        await seed_zones(db)

        # 第 4 层
        await seed_orders(db, user_map)
        await seed_extra_points_history(db, user_map)
        await seed_quick_questions(db)

        print("测试数据初始化完成。")


if __name__ == "__main__":
    asyncio.run(main())
