"""赛事绑定练习题库的集成回归（真实 PostgreSQL，CI 执行）。

覆盖：
1. 管理端更新竞赛绑定题库 → 列表回显 id/名称，非法目标被拒；
2. 用户端详情仅透出「已发布 + v2_enabled」的绑定题库，解绑后入口消失。
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"compquiz_{uuid4().hex[:12]}"

    def _ctx_factory(_mod):
        @asynccontextmanager
        async def wrapper():
            async with factory() as session:
                yield session

        return wrapper

    import app.services.admin_competition as admin_module
    import app.services.competition as user_module

    monkeypatch.setattr(admin_module, "get_db_ctx", _ctx_factory(admin_module))
    monkeypatch.setattr(user_module, "get_db_ctx", _ctx_factory(user_module))
    yield SimpleNamespace(factory=factory, prefix=prefix)

    from app.domain.certification.src.index import Competition, CompetitionTrack
    from app.domain.community.src.index import QuizLibrary
    from app.domain.community.src.model.quiz import QuizQuestion

    library_ids = select(QuizLibrary.id).where(
        QuizLibrary.name.like(f"{prefix}%")
    )
    competition_ids = select(Competition.id).where(
        Competition.name.like(f"{prefix}%")
    )
    async with factory() as db:
        await db.execute(
            delete(QuizQuestion).where(QuizQuestion.library_id.in_(library_ids))
        )
        await db.execute(
            delete(CompetitionTrack).where(
                CompetitionTrack.competition_id.in_(competition_ids)
            )
        )
        await db.execute(delete(Competition).where(Competition.id.in_(competition_ids)))
        await db.execute(delete(QuizLibrary).where(QuizLibrary.id.in_(library_ids)))
        await db.commit()
    await engine.dispose()


async def test_bind_and_surface_quiz_library(context) -> None:
    from app.domain.certification.src.index import Competition
    from app.domain.community.src.index import QuizLibrary
    from app.port.exceptions import BusinessException
    from app.schemas.admin_competition import (
        AdminCompetitionCreate,
        AdminCompetitionUpdate,
    )
    from app.services.admin_competition import AdminCompetitionService
    from app.services.competition import CompetitionService

    now = datetime.now(timezone.utc)
    async with context.factory() as db:
        library = QuizLibrary(
            name=f"{context.prefix}_lib",
            library_code=f"{context.prefix}_L",
            access_mode="free",
            vendor_tag="none",
            status="published",
            v2_enabled=True,
        )
        db.add(library)
        await db.flush()
        library_id = library.id
        competition = Competition(
            name=f"{context.prefix}_comp",
            is_active=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=2),
        )
        db.add(competition)
        await db.commit()

    service = AdminCompetitionService()

    async with context.factory() as db:
        row = (
            await db.execute(
                select(Competition.id).where(
                    Competition.name == f"{context.prefix}_comp"
                )
            )
        ).scalar_one()
        created_id = int(row)

    # 绑定 + 列表回显名称
    updated = await service.update(
        created_id, AdminCompetitionUpdate(quiz_library_id=library_id)
    )
    assert updated.quiz_library_id == library_id
    assert updated.quiz_library_name == f"{context.prefix}_lib"

    listing = await service.list_competitions(f"{context.prefix}_comp", 1, 20)
    assert listing.items[0].quiz_library_id == library_id
    assert listing.items[0].quiz_library_name == f"{context.prefix}_lib"

    # 用户端详情透出
    detail = await CompetitionService().get_event_detail(created_id)
    assert detail.quiz_library is not None
    assert detail.quiz_library.id == library_id

    # 绑定不存在的题库被拒
    with pytest.raises(BusinessException, match="题库不存在或不可绑定"):
        await service.update(
            created_id, AdminCompetitionUpdate(quiz_library_id=library_id + 10_000_000)
        )

    # 解绑 → 详情不再透出
    await service.update(created_id, AdminCompetitionUpdate(quiz_library_id=None))
    detail = await CompetitionService().get_event_detail(created_id)
    assert detail.quiz_library is None

    # 创建时携带绑定
    created = await service.create(
        AdminCompetitionCreate(
            name=f"{context.prefix}_comp2",
            is_active=True,
            quiz_library_id=library_id,
        )
    )
    assert created.quiz_library_id == library_id
    assert created.quiz_library_name == f"{context.prefix}_lib"
