"""人社多项目批次解析层：batch_project 优先，旧 Plan 字段兜底。"""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import RensheBatchProject
from app.domain.renshe.src.index import RensheApplication
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.services.renshe_batch_project import resolve_application_project, resolve_project
from app.services.renshe_batch_project import resolve_enrollable_project
from app.port.exceptions import BusinessException


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"renshe_bp_{uuid4().hex[:8]}"

    async with factory() as db, db.begin():
        product = CertProduct(
            type="renshe",
            code=f"{prefix}_P1",
            name=f"{prefix} 项目一",
            chinese_name=f"{prefix} 项目一",
            is_active=True,
        )
        plan = Plan(
            product_type=f"{prefix}_P1",
            name=f"{prefix} plan",
            price_cents=50000,
            occupation_name="旧工种",
            occupation_code="old-code",
            skill_level="三级",
            capacity=10,
        )
        other_plan = Plan(
            product_type=f"{prefix}_P1",
            name=f"{prefix} other plan",
            price_cents=50000,
            occupation_name="旧工种",
            occupation_code="old-code",
            skill_level="三级",
            capacity=10,
        )
        db.add_all((product, plan, other_plan))
        await db.flush()
        user = User(openid=f"{prefix}_user")
        db.add(user)
        await db.flush()
        project = RensheBatchProject(
            plan_id=plan.id,
            product_id=product.id,
            price_cents=38000,
            occupation_name="新工种",
            occupation_code="new-code",
            skill_level="四级",
        )
        db.add(project)
        await db.flush()
        other_project = RensheBatchProject(
            plan_id=other_plan.id,
            product_id=product.id,
            price_cents=100,
            is_active=True,
        )
        db.add(other_project)
        await db.flush()
        seed = SimpleNamespace(
            factory=factory,
            prefix=prefix,
            plan_id=plan.id,
            project_id=project.id,
            product_code=product.code,
            other_plan_id=other_plan.id,
            other_project_id=other_project.id,
            user_id=user.id,
        )

    yield seed

    async with factory() as db, db.begin():
        await db.execute(delete(RensheApplication).where(
            RensheApplication.plan_id.in_((seed.plan_id, seed.other_plan_id))
        ))
        await db.execute(delete(RensheBatchProject).where(
            RensheBatchProject.plan_id.in_((seed.plan_id, seed.other_plan_id))
        ))
        await db.execute(delete(Plan).where(
            Plan.id.in_((seed.plan_id, seed.other_plan_id))
        ))
        await db.execute(delete(Plan).where(Plan.id == seed.other_plan_id))
        await db.execute(delete(CertProduct).where(
            CertProduct.code.like(f"{prefix}%")
        ))
        await db.execute(delete(User).where(User.id == seed.user_id))

    await engine.dispose()


async def test_batch_project_overrides_legacy_plan_fields(context):
    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        resolved = await resolve_project(db, plan, batch_project_id=context.project_id)
        legacy = await resolve_project(db, plan)

    assert resolved.batch_project_id == context.project_id
    assert resolved.price_cents == 38000
    assert resolved.occupation_name == "新工种"
    assert resolved.skill_level == "四级"
    assert resolved.product_code == context.product_code

    assert legacy.batch_project_id is None
    assert legacy.price_cents == plan.price_cents
    assert legacy.occupation_name == "旧工种"
    assert legacy.skill_level == "三级"


async def test_application_resolution_uses_batch_project(context):
    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        application = SimpleNamespace(batch_project_id=context.project_id)
        resolved = await resolve_application_project(db, application, plan)

    assert resolved.batch_project_id == context.project_id
    assert resolved.price_cents == 38000
    assert resolved.occupation_code == "new-code"


async def test_missing_or_cross_plan_project_is_rejected(context):
    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        with pytest.raises(BusinessException, match="不存在"):
            await resolve_project(db, plan, batch_project_id=999999999)
        with pytest.raises(BusinessException, match="不属于当前批次"):
            await resolve_project(db, plan, batch_project_id=context.other_project_id)


async def test_existing_resolution_allows_inactive_but_enrollment_rejects(context):
    async with context.factory() as db, db.begin():
        project = await db.get(RensheBatchProject, context.project_id)
        project.is_active = False

    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        existing = await resolve_project(db, plan, batch_project_id=context.project_id)
        assert existing.is_active is False
        with pytest.raises(BusinessException, match="已停用"):
            await resolve_enrollable_project(db, plan, context.project_id)


async def test_null_batch_project_active_duplicates_are_blocked(context):
    async with context.factory() as db, db.begin():
        db.add(RensheApplication(
            plan_id=context.plan_id,
            user_id=context.user_id,
            status="draft",
        ))

    async with context.factory() as db:
        db.add(RensheApplication(
            plan_id=context.plan_id,
            user_id=context.user_id,
            status="pending_payment",
        ))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_closed_null_application_does_not_block_new_one(context):
    async with context.factory() as db, db.begin():
        db.add(RensheApplication(
            plan_id=context.plan_id,
            user_id=context.user_id,
            status="closed",
        ))
    async with context.factory() as db, db.begin():
        db.add(RensheApplication(
            plan_id=context.plan_id,
            user_id=context.user_id,
            status="draft",
        ))

    async with context.factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(RensheApplication).where(
                RensheApplication.plan_id == context.plan_id
            )
        )
        assert count == 2


async def test_multi_project_batch_requires_explicit_project(context):
    async with context.factory() as db, db.begin():
        extra_product = CertProduct(
            type="renshe",
            code=f"{context.prefix}_P2",
            name=f"{context.prefix} 项目二",
            chinese_name=f"{context.prefix} 项目二",
            is_active=True,
        )
        db.add(extra_product)
        await db.flush()
        db.add(RensheBatchProject(
            plan_id=context.plan_id,
            product_id=extra_product.id,
            price_cents=100,
        ))

    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        with pytest.raises(BusinessException, match="请选择报考项目"):
            await resolve_enrollable_project(db, plan, None)
        resolved = await resolve_enrollable_project(
            db, plan, context.project_id
        )
        assert resolved.batch_project_id == context.project_id


async def test_enrollable_project_rejects_non_renshe_product(context):
    async with context.factory() as db, db.begin():
        bad_product = CertProduct(
            type="h3c",
            code=f"{context.prefix}_H3C",
            name=f"{context.prefix} h3c",
            chinese_name=f"{context.prefix} h3c",
            is_active=True,
        )
        db.add(bad_product)
        await db.flush()
        bad_project = RensheBatchProject(
            plan_id=context.other_plan_id,
            product_id=bad_product.id,
            price_cents=100,
        )
        db.add(bad_project)
        await db.flush()
        bad_project_id = bad_project.id

    async with context.factory() as db:
        plan = await db.get(Plan, context.other_plan_id)
        with pytest.raises(BusinessException, match="认证产品无效"):
            await resolve_enrollable_project(db, plan, bad_project_id)


async def test_all_projects_disabled_does_not_fall_back_to_legacy_plan(context):
    async with context.factory() as db, db.begin():
        project = await db.get(RensheBatchProject, context.project_id)
        project.is_active = False

    async with context.factory() as db:
        plan = await db.get(Plan, context.plan_id)
        with pytest.raises(BusinessException, match="暂无可用报考项目"):
            await resolve_enrollable_project(db, plan, None)


async def test_unconfigured_multi_project_batch_does_not_fall_back(context):
    async with context.factory() as db, db.begin():
        plan = Plan(
            product_type=None,
            name=f"{context.prefix} unconfigured",
            price_cents=0,
            capacity=0,
        )
        db.add(plan)
        await db.flush()
        plan_id = plan.id

    async with context.factory() as db:
        plan = await db.get(Plan, plan_id)
        with pytest.raises(BusinessException, match="尚未配置报考项目"):
            await resolve_enrollable_project(db, plan, None)

    async with context.factory() as db, db.begin():
        await db.execute(delete(Plan).where(Plan.id == plan_id))
