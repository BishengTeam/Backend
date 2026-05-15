"""种子数据初始化脚本: 价格配置 / 认证信息 / 题库分类"""
import asyncio

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.certification import Certification
from app.models.price_config import PriceConfig
from app.models.quiz import QuizCategory


async def seed_price_configs():
    records = [
        PriceConfig(cert_type="H3C", user_type="student", price=380000),
        PriceConfig(cert_type="H3C", user_type="enterprise", price=480000),
        PriceConfig(cert_type="Sangfor", user_type="student", price=59800),
        PriceConfig(cert_type="Sangfor", user_type="enterprise", price=89800),
        PriceConfig(cert_type="NISP", user_type="student", price=69800),
        PriceConfig(cert_type="NISP", user_type="enterprise", price=69800),
    ]
    async with async_session_factory() as db:
        async with db.begin():
            db.add_all(records)


async def seed_certifications():
    records = [
        Certification(
            name="H3C 网络工程师",
            code="H3C-NE",
            vendor="H3C",
            price_enterprise=480000,
            price_student=380000,
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="深信服网络安全工程师",
            code="SF-CSE",
            vendor="深信服",
            price_enterprise=89800,
            price_student=59800,
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="NISP 一级",
            code="NISP-1",
            vendor="NISP",
            price_enterprise=69800,
            price_student=69800,
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="人社职业技能等级认定",
            code="RS-ZY",
            vendor="人社",
            price_enterprise=200000,
            price_student=150000,
            requires_xuexin=True,
            pay_first=False,
        ),
    ]
    async with async_session_factory() as db:
        async with db.begin():
            db.add_all(records)


async def seed_quiz_categories():
    parent = QuizCategory(name="H3C 网络工程师", description="H3C 认证题库")
    parent2 = QuizCategory(name="深信服网络安全", description="深信服认证题库")
    children = [
        QuizCategory(name="网络基础", parent_id=None, description="网络基础知识"),  # parent set below
        QuizCategory(name="路由协议", parent_id=None, description="路由协议相关"),
        QuizCategory(name="安全基础", parent_id=None, description="安全基础知识"),
        QuizCategory(name="防火墙", parent_id=None, description="防火墙相关"),
    ]
    async with async_session_factory() as db:
        async with db.begin():
            db.add_all([parent, parent2])
            await db.flush()
            children[0].parent_id = parent.id
            children[1].parent_id = parent.id
            children[2].parent_id = parent2.id
            children[3].parent_id = parent2.id
            db.add_all(children)


async def main():
    async with async_session_factory() as db:
        existing = (await db.execute(select(PriceConfig).limit(1))).first()
        if existing:
            print("种子数据已存在，跳过。")
            return

    print("初始化种子数据...")
    await seed_certifications()
    print("  ✓ 认证信息 (4 条)")
    await seed_price_configs()
    print("  ✓ 价格配置 (6 条)")
    await seed_quiz_categories()
    print("  ✓ 题库分类 (4 条)")
    print("种子数据初始化完成。")


if __name__ == "__main__":
    asyncio.run(main())
