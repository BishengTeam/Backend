"""种子数据初始化脚本: 价格配置 / 认证信息 / 题库分类"""
import asyncio

from sqlalchemy import select

from app.adapter.database import async_session_factory
from app.domain.certification.src.index import Certification
from app.domain.order.src.index import PriceConfig
from app.domain.community.src.index import QuizCategory
from app.domain.user.src.index import AdminUser
from app.domain.community.src.rule.quiz import normalize_category_name


async def _seed_price_configs(db):
    records = [
        PriceConfig(product_type="H3C-NE", user_type="student", price=380000),
        PriceConfig(product_type="H3C-NE", user_type="normal", price=480000),
        PriceConfig(product_type="SF-CSE", user_type="student", price=59800),
        PriceConfig(product_type="SF-CSE", user_type="normal", price=89800),
        PriceConfig(product_type="NISP-1", user_type="student", price=69800),
        PriceConfig(product_type="NISP-1", user_type="normal", price=69800),
        PriceConfig(product_type="RS-ZY", user_type="student", price=0),
        PriceConfig(product_type="RS-ZY", user_type="normal", price=0),
    ]
    db.add_all(records)
    print("  ✓ 价格配置 (8 条)")


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
    admin_id = await db.scalar(select(AdminUser.id).order_by(AdminUser.id).limit(1))
    if admin_id is None:
        raise RuntimeError(
            "题库分类需要管理员引用，请先执行 scripts/init_super_admin.py"
        )

    def category(name: str, description: str, *, parent_id: int | None, depth: int):
        normalized = normalize_category_name(name)
        return QuizCategory(
            name=normalized,
            normalized_name=normalized,
            parent_id=parent_id,
            depth=depth,
            description=description,
            status="active",
            sort_order=0,
            ever_had_question=False,
            lock_version=1,
            created_by=admin_id,
            updated_by=admin_id,
        )

    parent = category("H3C 网络工程师", "H3C 认证题库", parent_id=None, depth=1)
    parent2 = category("深信服网络安全", "深信服认证题库", parent_id=None, depth=1)
    db.add_all([parent, parent2])
    await db.flush()
    children = [
        category("网络基础", "网络基础知识", parent_id=parent.id, depth=2),
        category("路由协议", "路由协议相关", parent_id=parent.id, depth=2),
        category("安全基础", "安全基础知识", parent_id=parent2.id, depth=2),
        category("防火墙", "防火墙相关", parent_id=parent2.id, depth=2),
    ]
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
