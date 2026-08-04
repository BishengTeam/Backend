"""测试数据初始化：用户 / 订单 / 积分 / 库存 / 课程 / 优惠券 / Banner / Zone / 快速提问

用法：
    python scripts/seed_testdata.py          # 幂等：已存在则跳过
    python scripts/seed_testdata.py --force  # 先清理再重建
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# 确保 Backend 项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 注入缺失的环境变量，避免 Settings() 初始化失败。
os.environ.setdefault("JWT_SECRET", "seed-script-placeholder-do-not-use-in-prod")

from sqlalchemy import select, text

from app.adapter.database import async_session_factory
from app.domain.user.src.index import AdminUser, PointsHistory, User, UserProfile, UserRealname, UserStudent, UserEnterprise, UserPoints
from app.domain.content.src.index import Activity, Training, Zone
from app.domain.content.src.model.banner import Banner
from app.domain.certification.src.index import Certification, Course, CourseEnrollment, Job
from app.domain.order.src.index import Coupon, Inventory, Order, PriceConfig, UserCoupon
from app.domain.community.src.index import QuickQuestion, QuizQuestion

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
        "activity_registration",
        "activity_reminder",
        "course_enrollment",
        "job_application",
        '"order"',
        "ticket",
        "agreement",
        "quiz_record",
        "quiz_checkin",
        "competition_reg",
        "conversation",
        "collection",
        "share",
    ]
    if user_ids:
        for table in user_dependent_tables:
            await db.execute(text(f"DELETE FROM {table} WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text('DELETE FROM "user" WHERE id = ANY(:ids)'), {"ids": user_ids})

    # 3. 删除全局测试数据（按依赖顺序）
    await db.execute(text("DELETE FROM activity_reminder"))
    await db.execute(text("DELETE FROM activity_registration"))
    await db.execute(text("DELETE FROM inventory_record"))
    await db.execute(text("DELETE FROM inventory"))
    await db.execute(text("DELETE FROM user_coupon"))  # 可能还有残留
    await db.execute(text("DELETE FROM coupon"))
    await db.execute(text("DELETE FROM course_enrollment"))
    await db.execute(text("DELETE FROM course_asset"))
    await db.execute(text("DELETE FROM course"))
    await db.execute(text("DELETE FROM job_application"))
    await db.execute(text("DELETE FROM job"))
    await db.execute(text("DELETE FROM activity"))
    await db.execute(text("DELETE FROM training"))
    await db.execute(text("DELETE FROM banner"))
    await db.execute(text("DELETE FROM zone"))
    await db.execute(text("DELETE FROM quiz_question"))
    await db.execute(text("DELETE FROM quick_question"))
    await db.execute(text("DELETE FROM deleted_openid"))
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
    """为有实名信息的用户创建 UserRealname / UserProfile，并为学生用户创建 UserStudent。"""
    count = 0
    for u in TEST_USERS:
        uid = user_map[u["openid"]]

        # 创建 UserProfile
        db.add(UserProfile(user_id=uid))

        if u["identity"] is None:
            continue
        ident = UserRealname(
            user_id=uid,
            user_type=u["identity"]["user_type"],
            real_name=u["identity"]["real_name"],
            id_card_number=u["identity"]["id_card_number"],
            status="verified",
            verified_at=NOW.replace(microsecond=0).isoformat(),
        )
        db.add(ident)

        # 学生用户额外创建 UserStudent
        if u["identity"]["user_type"] == "student":
            db.add(UserStudent(
                user_id=uid,
                education="本科",
                school="测试大学",
                major="计算机科学",
                student_card_oss="oss://student_card_test.jpg",
                status="verified",
                verified_at=NOW.replace(microsecond=0).isoformat(),
            ))

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
            "batches": {
                "11111111-1111-4111-8111-111111111111": {
                    "class_date": "2026-03-01",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "location": "线上",
                },
                "22222222-2222-4222-8222-222222222222": {
                    "class_date": "2026-07-01",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "location": "线上",
                },
            },
            "teacher_name": "赵老师",
            "teacher_contact": "teacher_zhao@example.com",
        },
        {
            "title": "深信服安全工程师精讲",
            "category": "Sangfor",
            "description": "深信服网络安全工程师认证精讲课程",
            "price": 59800,
            "batches": {
                "33333333-3333-4333-8333-333333333333": {
                    "class_date": "2026-03-15",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "location": "线上",
                },
            },
            "teacher_name": "钱老师",
        },
        {
            "title": "NISP 一级冲刺班",
            "category": "NISP",
            "description": "NISP 一级认证考前冲刺，考点全覆盖",
            "price": 69800,
            "batches": {
                "44444444-4444-4444-8444-444444444444": {
                    "class_date": "2026-05-01",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "location": "线上",
                },
                "55555555-5555-4555-8555-555555555555": {
                    "class_date": "2026-06-01",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "location": "线上",
                },
            },
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
        (user_map["test_openid_user_001"], course_ids[0], None),
        (user_map["test_openid_user_002"], course_ids[1], None),
    ]
    for uid, cid, batch in enrollments:
        db.add(
            CourseEnrollment(
                user_id=uid,
                course_id=cid,
                batch_selected=batch,
                status="enrolled",
                learning_access=True,
                access_granted_at=NOW,
            )
        )

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
            image_url="https://placehold.co/800x400/1677FF/FFFFFF?text=Active+Banner",
            jump_link="/pages/active/index",
            sort=1,
            start_time=NOW - timedelta(days=7),
            end_time=NOW + timedelta(days=7),
            is_active=True,
        ),
        Banner(
            image_url="https://placehold.co/800x400/722ED1/FFFFFF?text=Upcoming+Banner",
            jump_link="/pages/upcoming/index",
            sort=2,
            start_time=NOW + timedelta(days=7),
            end_time=NOW + timedelta(days=30),
            is_active=True,
        ),
        Banner(
            image_url="https://placehold.co/800x400/999999/FFFFFF?text=Expired+Banner",
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
        Zone(zone_type="cert", title="认证考试专区", description="H3C / 深信服 / NISP 认证", sort_order=1),
        Zone(zone_type="study", title="精品课程", description="名师授课，系统学习", sort_order=2),
        Zone(zone_type="competition", title="竞赛报名", description="全国职业技能大赛", sort_order=3),
        Zone(zone_type="activity", title="限时活动", description="优惠券发放，积分兑换", sort_order=4),
        Zone(zone_type="employment", title="就业专区", description="最新岗位推荐", sort_order=5),
        Zone(zone_type="training", title="培训专区", description="技能培训课程", sort_order=6),
    ]
    db.add_all(zones)
    await db.commit()
    print(f"  ✓ Zone ({len(zones)} 个)")


async def seed_activities(db, user_map: dict[str, int]):
    """3 个活动 + 2 条报名记录。"""
    activities = [
        Activity(title="H3C 认证线下交流会", description="H3C 官方认证讲师现场答疑",
                 location="北京海淀区", start_time=NOW + timedelta(days=7),
                 end_time=NOW + timedelta(days=7, hours=3), max_participants=50),
        Activity(title="深信服安全技术沙龙", description="深信服安全专家分享最新安全趋势",
                 location="上海浦东新区", start_time=NOW + timedelta(days=14),
                 end_time=NOW + timedelta(days=14, hours=4), max_participants=80),
        Activity(title="已结束的网络工程讲座", description="往期活动",
                 location="深圳南山区", start_time=NOW - timedelta(days=30),
                 end_time=NOW - timedelta(days=30, hours=2), max_participants=30, is_active=False),
    ]
    db.add_all(activities)
    await db.flush()

    from app.domain.content.src.index import ActivityRegistration
    db.add(ActivityRegistration(activity_id=activities[0].id, user_id=user_map["test_openid_user_001"], name="张三", phone="13800000001"))
    db.add(ActivityRegistration(activity_id=activities[1].id, user_id=user_map["test_openid_user_002"], name="李四", phone="13800000002"))
    await db.commit()
    print(f"  ✓ 活动 ({len(activities)} 个 + 2 条报名)")


async def seed_trainings(db):
    """2 个培训。"""
    trainings = [
        Training(title="H3C 路由交换实训", description="H3C NE 认证实训课程",
                 location="北京", start_time=NOW + timedelta(days=10),
                 end_time=NOW + timedelta(days=15), max_participants=30,
                 cert_type="H3C-NE", price=280000),
        Training(title="NISP 一级考前集训", description="NISP 一级认证考前集中培训",
                 location="线上", start_time=NOW + timedelta(days=20),
                 end_time=NOW + timedelta(days=22), max_participants=100,
                 cert_type="NISP-1", price=59800),
    ]
    db.add_all(trainings)
    await db.commit()
    print(f"  ✓ 培训 ({len(trainings)} 个)")


async def seed_jobs(db):
    """3 个岗位。"""
    jobs = [
        Job(title="网络工程师", company="新华三集团", location="北京",
            salary_range="15K-25K", description="负责网络设备配置与维护",
            requirements="H3C NE 及以上认证", contact_info="hr@h3c.com"),
        Job(title="网络安全工程师", company="深信服科技", location="深圳",
            salary_range="20K-35K", description="负责安全产品部署与运维",
            requirements="深信服认证优先", contact_info="jobs@sangfor.com"),
        Job(title="已过期的实习岗位", company="某小型企业", location="武汉",
            salary_range="3K-5K", description="网络运维实习",
            requirements="在校学生", contact_info="hr@example.com", is_active=False),
    ]
    db.add_all(jobs)
    await db.commit()
    print(f"  ✓ 岗位 ({len(jobs)} 个)")


async def seed_quiz_questions(db):
    """为每个题库子分类写入题目。"""
    from app.domain.community.src.index import QuizCategory

    result = await db.execute(select(QuizCategory).where(QuizCategory.parent_id.isnot(None)))
    sub_cats = result.scalars().all()
    if not sub_cats:
        print("  ⚠ 无子分类，跳过题目创建")
        return

    # 按子分类分别出题
    question_bank = {
        "网络基础": [
            ("single_choice", "OSPF 协议的默认管理距离是多少？",
             {"A": "90", "B": "110", "C": "120", "D": "1"}, "B", "OSPF 默认管理距离为 110"),
            ("multiple_choice", "以下哪些是私有 IP 地址？",
             {"A": "10.0.0.1", "B": "172.16.0.1", "C": "192.168.0.1", "D": "8.8.8.8"}, "A,B,C", "10.x、172.16-31.x、192.168.x 是私有地址"),
            ("judge", "TCP 是面向连接的协议。",
             {"A": "正确", "B": "错误"}, "A", "TCP 通过三次握手建立连接"),
        ],
        "路由协议": [
            ("single_choice", "BGP 使用的端口号是？",
             {"A": "53", "B": "179", "C": "520", "D": "80"}, "B", "BGP 使用 TCP 179 端口"),
            ("multiple_choice", "以下哪些属于链路状态路由协议？",
             {"A": "OSPF", "B": "RIP", "C": "IS-IS", "D": "EIGRP"}, "A,C", "OSPF 和 IS-IS 是链路状态协议"),
            ("judge", "RIP 协议的收敛速度比 OSPF 快。",
             {"A": "正确", "B": "错误"}, "B", "OSPF 收敛速度快于 RIP"),
        ],
        "安全基础": [
            ("single_choice", "以下哪种攻击属于 DDoS 攻击？",
             {"A": "SQL 注入", "B": "SYN Flood", "C": "XSS 跨站", "D": "CSRF"}, "B", "SYN Flood 是典型的 DDoS 攻击方式"),
            ("multiple_choice", "以下哪些属于对称加密算法？",
             {"A": "AES", "B": "RSA", "C": "DES", "D": "ECC"}, "A,C", "AES 和 DES 为对称加密，RSA/ECC 为非对称"),
            ("judge", "防火墙可以完全阻止所有网络攻击。",
             {"A": "正确", "B": "错误"}, "B", "防火墙是重要防线但无法阻止所有攻击"),
        ],
        "防火墙": [
            ("single_choice", "iptables 中，DROP 和 REJECT 的区别是？",
             {"A": "无区别", "B": "DROP 静默丢弃 REJECT 返回拒绝信息", "C": "REJECT 静默丢弃 DROP 返回信息", "D": "DROP 仅限 TCP"}, "B", "DROP 无响应，REJECT 返回 ICMP 拒绝"),
            ("multiple_choice", "深信服下一代防火墙支持哪些功能？",
             {"A": "IPS 入侵防御", "B": "WAF Web 防护", "C": "VPN 接入", "D": "数据备份"}, "A,B,C", "数据备份不属于防火墙核心功能"),
            ("judge", "状态检测防火墙比包过滤防火墙更安全。",
             {"A": "正确", "B": "错误"}, "A", "状态检测能跟踪连接状态，安全性更高"),
        ],
    }

    total = 0
    for cat in sub_cats:
        cat_name = cat.name
        if cat_name not in question_bank:
            continue
        for qtype, text, opts, answer, expl in question_bank[cat_name]:
            db.add(QuizQuestion(
                category_id=cat.id,
                question_type=qtype,
                question_text=text,
                options=opts,
                correct_answer=answer,
                explanation=expl,
            ))
            total += 1
    await db.commit()
    print(f"  ✓ 题库题目 ({total} 道)")


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
        await seed_activities(db, user_map)
        await seed_trainings(db)
        await seed_jobs(db)

        # 第 4 层
        await seed_orders(db, user_map)
        await seed_quiz_questions(db)
        await seed_extra_points_history(db, user_map)
        await seed_quick_questions(db)

        print("测试数据初始化完成。")


if __name__ == "__main__":
    asyncio.run(main())
