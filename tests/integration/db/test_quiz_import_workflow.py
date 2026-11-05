"""Exercise confirmation races and publication checkpoints on PostgreSQL."""
import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, func

from tests.integration.db.test_admin_quiz_v2 import quiz_v2_env
from app.domain.community.src.model.quiz import QuizImportJob, QuizImportError, QuizPublicationOperation, QuizModule, QuizKnowledgePoint, QuizQuestion
from app.domain.user.src.index import AdminUser
from app.schemas.admin_quiz_contract import AdminQuizLibraryCreate, AdminQuizLibraryQuery, AdminQuizImportConfirmCategoriesRequest, AdminQuizPublicationRequest, AdminQuizQuestionQuery, AdminQuizOwnershipMoveRequest
from app.port.config import settings
from app.services.quiz_publication import QuizPublicationService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def workflow(quiz_v2_env, monkeypatch, tmp_path):
    env = quiz_v2_env
    @asynccontextmanager
    async def ctx():
        async with env.factory() as db: yield db
    monkeypatch.setattr("app.services.quiz_publication.get_db_ctx", ctx)
    (tmp_path / "private").mkdir()
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "QUIZ_IMPORT_STORAGE_TYPE", "local")
    async with env.factory() as db:
        (await db.get(AdminUser, env.admin_id)).must_change_password = False
        await db.commit()
    env.library = await env.service.create_library(AdminQuizLibraryCreate(name=env.prefix + "导入", access_mode="free"), admin_id=env.admin_id)
    try:
        yield env
    finally:
        async with env.factory() as db:
            ids = select(QuizImportJob.id).where(QuizImportJob.admin_id == env.admin_id)
            await db.execute(delete(QuizImportError).where(QuizImportError.job_id.in_(ids)))
            await db.execute(delete(QuizImportJob).where(QuizImportJob.admin_id == env.admin_id))
            await db.execute(delete(QuizPublicationOperation).where(QuizPublicationOperation.admin_id == env.admin_id))
            await db.commit()


def item(path, text="示例题"):
    return {"category_path": path, "question_text": text, "question_type": "single_choice", "options": {"A": "甲", "B": "乙", "C": "丙"}, "correct_answer": "A"}


async def upload(env, questions):
    job = await env.stats_service.create_import_job(source_type="json", content=json.dumps({"questions": questions}, ensure_ascii=False).encode(), admin_id=env.admin_id, filename="test.json", library_id=env.library.id)
    await env.stats_service.process_import_job(job.id)
    return await env.stats_service.get_import_job(job.id)


async def confirm(env, job):
    await env.stats_service.confirm_import_categories(job.id, AdminQuizImportConfirmCategoriesRequest(lock_version=job.lock_version, impact_version=job.impact_version), admin_id=env.admin_id)
    await env.stats_service.process_import_job(job.id)
    return await env.stats_service.get_import_job(job.id)


@pytest.mark.parametrize("path,expected", [([], ("基础题目", "综合练习")), (["网络基础"], ("网络基础", "综合练习")), (["", "TCP"], ("基础题目", "TCP")), (["网络基础", "TCP"], ("网络基础", "TCP"))])
async def test_optional_paths_wait_for_one_explicit_confirmation(workflow, path, expected):
    env = workflow
    job = await upload(env, [item(path)])
    assert job.status == "awaiting_category_confirmation"
    async with env.factory() as db:
        assert not await db.scalar(select(func.count()).select_from(QuizQuestion).where(QuizQuestion.library_id == env.library.id))
    job = await confirm(env, job)
    assert job.status == "succeeded", job.error_message
    assert job.created_count == 1
    assert not await env.stats_service.process_import_job(job.id)
    tree = await env.service.get_content_tree(env.library.id)
    assert (tree.modules[0].name, tree.modules[0].knowledge_points[0].name) == expected
    assert tree.modules[0].system_kind == "none"
    page = await env.service.list_questions(AdminQuizQuestionQuery(library_id=env.library.id))
    assert page.total == 1 and page.items[0].status == "draft"


async def test_renamed_defaults_reuse_and_changed_plan_requires_reconfirmation(workflow):
    env = workflow
    first, second = await asyncio.gather(upload(env, [item([])]), upload(env, [item([], "第二题")]))
    first = await confirm(env, first)
    assert first.status == "succeeded"
    second = await confirm(env, second)
    assert second.status == "awaiting_category_confirmation"
    second = await confirm(env, second)
    assert second.status == "succeeded"
    async with env.factory() as db:
        module = await db.scalar(select(QuizModule).where(QuizModule.library_id == env.library.id))
        point = await db.scalar(select(QuizKnowledgePoint).where(QuizKnowledgePoint.library_id == env.library.id))
        module.name = module.normalized_name = "日常练习"
        point.name = point.normalized_name = "综合题"
        module.lock_version += 1
        point.lock_version += 1
        await db.commit()
    third = await confirm(env, await upload(env, [item([], "第三题")]))
    assert third.status == "succeeded"
    tree = await env.service.get_content_tree(env.library.id)
    assert len(tree.modules) == len(tree.modules[0].knowledge_points) == 1
    assert tree.modules[0].name == "日常练习"


async def test_publication_exact_revisions_stages_and_idempotency(workflow):
    env = workflow
    await confirm(env, await upload(env, [item([], "选定题"), item([], "保留草稿")]))
    svc = QuizPublicationService()
    check = await svc.preflight(env.library.id)
    data = AdminQuizPublicationRequest(request_key=uuid4().hex, lock_version=check["lock_version"], preflight_version=check["preflight_version"], items=check["candidates"][:1], publish_library=True, open_entry=True)
    job = await svc.create(env.library.id, data, admin_id=env.admin_id)
    assert (await svc.create(env.library.id, data, admin_id=env.admin_id))["id"] == job["id"]
    for _ in range(5): await svc.process_one()
    result = await svc.get(job["id"])
    assert result["status"] == "succeeded", result
    assert result["progress"]["questions"] == 1 and result["progress"]["entry"]
    check = await svc.preflight(env.library.id)
    assert check["counts"]["published"] == 1 and check["counts"]["draft"] == 1
    assert check["entry_open"]


async def test_import_and_partial_publication_reconcile_library_totals(workflow):
    env = workflow
    job = await confirm(env, await upload(env, [item([], f"导入对账 {index}") for index in range(10)]))
    assert job.total_rows == job.created_count == 10
    service = QuizPublicationService()
    check = await service.preflight(env.library.id)
    operation = await service.create(env.library.id, AdminQuizPublicationRequest(
        request_key=uuid4().hex, lock_version=check["lock_version"],
        preflight_version=check["preflight_version"], items=check["candidates"][:8],
        publish_library=True, open_entry=True,
    ), admin_id=env.admin_id)
    for _ in range(5):
        await service.process_one()
    assert (await service.get(operation["id"]))["status"] == "succeeded"
    (row,) = await env.service.list_libraries(
        AdminQuizLibraryQuery(keyword=env.prefix), include_practice_counts=True,
    )
    assert row.question_count == 10
    assert row.practice_counts.practicable == 8
    assert row.practice_counts.unpublished == 2


async def test_bulk_move_retains_revisions_and_blocks_cross_library(workflow):
    env = workflow
    await confirm(env, await upload(env, [item([]), item(["网络", "TCP"], "另一题")]))
    rows = (await env.service.list_questions(AdminQuizQuestionQuery(library_id=env.library.id))).items
    tree = await env.service.get_content_tree(env.library.id)
    target = next(m.knowledge_points[0] for m in tree.modules if m.name == "网络")
    source = next(q for q in rows if q.import_default_ownership)
    await QuizPublicationService().move(AdminQuizOwnershipMoveRequest(library_id=env.library.id, knowledge_point_id=target.id, items=[{"question_id": source.id, "lock_version": source.lock_version}]), admin_id=env.admin_id)
    changed = (await env.service.list_questions(AdminQuizQuestionQuery(question_id=source.id))).items[0]
    assert changed.pending_revision_id == source.pending_revision_id
    assert changed.correct_answer == source.correct_answer
    assert not changed.import_default_ownership and changed.knowledge_point_id == target.id
    from app.port.exceptions import BusinessException
    other = await env.service.create_library(AdminQuizLibraryCreate(name=env.prefix + "其他题库"), admin_id=env.admin_id)
    with pytest.raises(BusinessException):
        await QuizPublicationService().move(AdminQuizOwnershipMoveRequest(library_id=other.id, knowledge_point_id=target.id, items=[{"question_id": changed.id, "lock_version": changed.lock_version}]), admin_id=env.admin_id)



async def test_publication_retry_preserves_completed_stages_and_rechecks_permissions(workflow):
    env = workflow
    await confirm(env, await upload(env, [item([])]))
    svc = QuizPublicationService()
    check = await svc.preflight(env.library.id)
    data = AdminQuizPublicationRequest(request_key=uuid4().hex, lock_version=check["lock_version"], preflight_version=check["preflight_version"], items=check["candidates"], publish_library=True, open_entry=True)
    job = await svc.create(env.library.id, data, admin_id=env.admin_id)
    await asyncio.gather(svc.process_one(), svc.process_one())
    # Depending on lock timing the second worker may execute the next phase.
    while not (await svc.get(job["id"]))["progress"]["library"]: await svc.process_one()
    async with env.factory() as db:
        (await db.get(AdminUser, env.admin_id)).is_active = False
        await db.commit()
    await svc.process_one()
    failed = await svc.get(job["id"])
    assert failed["status"] == "failed" and failed["progress"]["questions"] == 1
    assert failed["progress"]["library"] and not failed["progress"]["entry"]
    async with env.factory() as db:
        (await db.get(AdminUser, env.admin_id)).is_active = True
        await db.commit()
    await svc.resume(job["id"], admin_id=env.admin_id, preflight_version=(await svc.preflight(env.library.id))["preflight_version"])
    await svc.process_one()
    await svc.process_one()
    finished = await svc.get(job["id"])
    assert finished["status"] == "succeeded" and finished["progress"]["questions"] == 1


async def test_disabled_explicit_target_accepts_drafts_but_default_does_not(workflow):
    env = workflow
    await confirm(env, await upload(env, [item([])]))
    async with env.factory() as db:
        module = await db.scalar(select(QuizModule).where(QuizModule.library_id == env.library.id))
        module.status = "disabled"
        module.disabled_at = env.service._now()
        module.lock_version += 1
        await db.commit()
    blocked = await upload(env, [item([], "空归属")])
    assert blocked.status == "validation_failed"
    explicit = await confirm(env, await upload(env, [item(["基础题目", "综合练习"], "指定停用路径")]))
    assert explicit.status == "succeeded"
    check = await QuizPublicationService().preflight(env.library.id)
    assert check["counts"]["unavailable_ownership"] == 2 and not check["candidates"]
    assert (await env.service.list_questions(AdminQuizQuestionQuery(library_id=env.library.id, unavailable_ownership=True))).total == 2
    assert (await env.service.list_questions(AdminQuizQuestionQuery(library_id=env.library.id, import_default_ownership=True))).total == 1


async def test_question_edit_after_confirmation_cannot_publish_newer_revision(workflow):
    from app.schemas.admin_quiz_contract import AdminQuizQuestionUpdate
    env = workflow
    await confirm(env, await upload(env, [item([])]))
    svc = QuizPublicationService()
    check = await svc.preflight(env.library.id)
    target = check["candidates"][0]
    job = await svc.create(env.library.id, AdminQuizPublicationRequest(request_key=uuid4().hex, lock_version=check["lock_version"], preflight_version=check["preflight_version"], items=[target]), admin_id=env.admin_id)
    await env.service.update_question(target["question_id"], AdminQuizQuestionUpdate(lock_version=target["lock_version"], explanation="其他管理员修改"), admin_id=env.admin_id)
    await svc.process_one()
    result = await svc.get(job["id"])
    assert result["status"] == "failed" and result["progress"]["questions"] == 0
    assert (await env.service.list_questions(AdminQuizQuestionQuery(library_id=env.library.id))).items[0].status == "draft"


async def test_content_errors_leave_no_default_structure_or_questions(workflow):
    env = workflow
    bad = item([], "错误题")
    bad["correct_answer"] = "Z"
    result = await upload(env, [item([]), bad])
    assert result.status == "validation_failed"
    async with env.factory() as db:
        for model in (QuizModule, QuizKnowledgePoint, QuizQuestion):
            assert await db.scalar(select(func.count()).select_from(model).where(model.library_id == env.library.id)) == 0


async def test_active_named_nodes_adopt_default_flag_only_after_confirmation(workflow):
    env = workflow
    await confirm(env, await upload(env, [item(["基础题目", "综合练习"])]))
    tree = await env.service.get_content_tree(env.library.id)
    assert not tree.modules[0].is_import_default
    job = await upload(env, [item([], "空分类题")])
    assert job.category_impact["new_category_count"] == 0
    assert not (await env.service.get_content_tree(env.library.id)).modules[0].is_import_default
    await confirm(env, job)
    tree = await env.service.get_content_tree(env.library.id)
    assert tree.modules[0].is_import_default and tree.modules[0].knowledge_points[0].is_import_default
