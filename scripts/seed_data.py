"""种子数据初始化脚本: 价格配置 / 认证信息 / 题库分类"""
import asyncio

from sqlalchemy import select

from app.adapter.database import async_session_factory
from app.domain.certification.src.index import Certification
from app.domain.order.src.index import PriceConfig
from app.domain.community.src.index import QuizCategory


async def _seed_price_configs(db):
    records = [
        PriceConfig(cert_type="H3C", user_type="student", price=380000),
        PriceConfig(cert_type="H3C", user_type="enterprise", price=480000),
        PriceConfig(cert_type="Sangfor", user_type="student", price=59800),
        PriceConfig(cert_type="Sangfor", user_type="enterprise", price=89800),
        PriceConfig(cert_type="NISP", user_type="student", price=69800),
        PriceConfig(cert_type="NISP", user_type="enterprise", price=69800),
    ]
    db.add_all(records)
    print("  ✓ 价格配置 (6 条)")


async def _seed_certifications(db):
    records = [
        Certification(
            name="h3c_ne",
            chinese_name="H3C 网络工程师",
            code="H3C-NE",
            vendor="H3C",
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="sangfor_cse",
            chinese_name="深信服网络安全工程师",
            code="SF-CSE",
            vendor="深信服",
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="nisp_1",
            chinese_name="NISP 一级",
            code="NISP-1",
            vendor="NISP",
            requires_xuexin=False,
            pay_first=True,
        ),
        Certification(
            name="rs_zy",
            chinese_name="人社职业技能等级认定",
            code="RS-ZY",
            vendor="人社",
            requires_xuexin=True,
            pay_first=False,
        ),
    ]
    db.add_all(records)
    print("  ✓ 认证信息 (4 条)")


async def _seed_quiz_categories(db):
    parent = QuizCategory(name="H3C 网络工程师", description="H3C 认证题库")
    parent2 = QuizCategory(name="深信服网络安全", description="深信服认证题库")
    children = [
        QuizCategory(name="网络基础", description="网络基础知识"),
        QuizCategory(name="路由协议", description="路由协议相关"),
        QuizCategory(name="安全基础", description="安全基础知识"),
        QuizCategory(name="防火墙", description="防火墙相关"),
    ]
    db.add_all([parent, parent2])
    await db.flush()
    children[0].parent_id = parent.id
    children[1].parent_id = parent.id
    children[2].parent_id = parent2.id
    children[3].parent_id = parent2.id
    db.add_all(children)
    print("  ✓ 题库分类 (4 个父类 + 4 个子类)")


async def main():
    results = []
    async with async_session_factory() as db:
        async with db.begin():
            has_price = (await db.execute(select(PriceConfig).limit(1))).first()
            if not has_price:
                await _seed_price_configs(db)
                results.append("价格配置")
            else:
                print("价格配置已存在，跳过。")

            has_cert = (await db.execute(select(Certification).limit(1))).first()
            if not has_cert:
                await _seed_certifications(db)
                results.append("认证信息")
            else:
                print("认证信息已存在，跳过。")

            has_quiz = (await db.execute(select(QuizCategory).limit(1))).first()
            if not has_quiz:
                await _seed_quiz_categories(db)
                results.append("题库分类")
            else:
                print("题库分类已存在，跳过。")

    if results:
        print(f"种子数据初始化完成: {', '.join(results)}")
    else:
        print("种子数据已存在，跳过。")


if __name__ == "__main__":
    asyncio.run(main())
