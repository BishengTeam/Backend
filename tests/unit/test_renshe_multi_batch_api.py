from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.plan import PlanCreate
from app.services.plan import PlanService
from app.services.renshe_project import RensheProjectService
from tests.unit.test_renshe_projects import isolated as _isolated, project_data


@pytest.fixture
def isolated(_isolated):
    return _isolated


@pytest.mark.asyncio
async def test_multi_project_batch_returns_valid_project_rows(isolated):
    service = RensheProjectService()
    first = await service.save(project_data("项目一"), admin_id=1)
    second = await service.save(project_data("项目二"), admin_id=1)
    now = datetime.now(timezone.utc)

    batch = await PlanService().create_renshe_multi_batch(
        PlanCreate(
            name="多项目批次",
            apply_start=now,
            apply_end=now + timedelta(days=10),
            exam_date=now + timedelta(days=20),
            capacity=50,
            projects=[
                {"product_id": first.id, "price_cents": 10000, "sort_order": 0},
                {"product_id": second.id, "price_cents": 20000, "sort_order": 1},
            ],
        ),
        admin_id=1,
    )

    assert [item.product_id for item in batch.projects] == [first.id, second.id]
    assert all(item.updated_at is not None for item in batch.projects)


@pytest.mark.asyncio
async def test_admin_list_keeps_batch_when_all_projects_are_disabled(isolated):
    project = await RensheProjectService().save(
        project_data("唯一项目"), admin_id=1
    )
    now = datetime.now(timezone.utc)
    batch = await PlanService().create_renshe_multi_batch(
        PlanCreate(
            name="停用项目批次",
            apply_start=now,
            apply_end=now + timedelta(days=10),
            capacity=50,
            projects=[
                {"product_id": project.id, "price_cents": 10000, "sort_order": 0}
            ],
        ),
        admin_id=1,
    )

    await PlanService().update_multi_project(
        batch.id, batch.projects[0].id, is_active=False, admin_id=1
    )

    rows = await PlanService().list_renshe_plans()
    assert batch.id in {item.id for item in rows}
