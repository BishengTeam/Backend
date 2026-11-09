"""全后台统一回收站核心。

- 软删除：deleted_at + restore_until（30 自然日），到期由 cleanup_loop 的
  purge_expired_recycle_bin() 自动永久删除。
- 永久删除策略：无引用物理删；被学生数据引用的转「墓碑」（清空展示字段保留行，
  维持外键引用不断链）。归档条目只陈列、不倒计时、永不清除。
- 未纳入本模块的删除操作：prices/coupons/cert_products/certifications/points_mall
  （is_active 下架类）、plan/renshe 项目/quiz_category（草稿守卫删除且带部分唯一索引）、
  user（is_active 禁用走回收站用户页签，注销用户不进回收站）。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, exists, func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Competition,
    CompetitionReg,
    CompetitionTrack,
    Course,
    CourseChapter,
    Job,
    JobApplication,
    UserChapterProgress,
)
from app.domain.classroom.src.index import Classroom, ClassroomQuestion, ClassroomVideo
from app.domain.community.src.index import (
    QuizActivationCode,
    QuizCollection,
    QuizCourseLibraryBinding,
    QuizExamQuestion,
    QuizKnowledgePoint,
    QuizLibrary,
    QuizModule,
    QuizPracticeSessionQuestion,
    QuizQuestion,
    QuizQuestionRevision,
    QuizQuestionRevisionStats,
    QuizQuestionStats,
    QuizWrongItem,
)
from app.domain.content.src.index import (
    Activity,
    ActivityRegistration,
    ActivityReminder,
    AgreementTemplate,
    Banner,
    Popup,
    SharePoster,
    Training,
    Zone,
)
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ForbiddenException, NotFoundException
from app.domain.user.src.index import User

RESTORE_WINDOW = timedelta(days=30)

TOMBSTONE_TEXT = "（已永久删除）"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def soft_delete_row(obj, *, now: datetime | None = None) -> None:
    stamp = now or utcnow()
    obj.deleted_at = stamp
    obj.restore_until = stamp + RESTORE_WINDOW


def restore_row(obj) -> None:
    obj.deleted_at = None
    obj.restore_until = None


@dataclass
class TrashModuleSpec:
    """单个回收站模块的注册信息。"""

    key: str
    label: str
    model: Any
    # 展示标题列；None 表示用 "#id" 兜底（如 banner 只有图片地址）
    title_attr: str | None
    restore_permission: str
    # 到期清理策略：physical=直接物理删；tombstone=有引用时清字段保行
    purge_policy: str = "physical"
    # tombstone 策略下清空的字段 -> 写入值
    tombstone_fields: dict[str, Any] = field(default_factory=dict)
    # 模块内按归属过滤（teacher 只能看自己的课堂内容）
    teacher_scoped: bool = False


def _tombstone(title_key: str) -> dict[str, Any]:
    return {title_key: TOMBSTONE_TEXT, "description": None, "cover_url": None}


TRASH_MODULES: dict[str, TrashModuleSpec] = {
    spec.key: spec
    for spec in [
        TrashModuleSpec("banner", "首页轮播图", Banner, None, "homepage:write"),
        TrashModuleSpec("popup", "首页弹窗海报", Popup, None, "homepage:write"),
        TrashModuleSpec("zone", "首页专区", Zone, "title", "homepage:write"),
        TrashModuleSpec("share_poster", "分享海报", SharePoster, None, "homepage:write"),
        TrashModuleSpec("training", "培训", Training, "title", "training:write",
                        "tombstone", _tombstone("title")),
        TrashModuleSpec("activity", "活动", Activity, "title", "activity:write",
                        "tombstone", _tombstone("title")),
        TrashModuleSpec("job", "招聘职位", Job, "title", "job:write",
                        "tombstone", {**_tombstone("title"), "company": None,
                                      "requirements": None, "contact_info": None}),
        TrashModuleSpec("competition", "赛事", Competition, "name", "competition:write",
                        "tombstone", {"name": TOMBSTONE_TEXT, "description": None,
                                      "cover_url": None, "poster_url": None}),
        TrashModuleSpec("classroom_video", "课堂视频", ClassroomVideo, "title",
                        "classroom:manage", teacher_scoped=True),
        TrashModuleSpec("classroom_question", "课堂练习题", ClassroomQuestion, "stem",
                        "classroom:manage", teacher_scoped=True),
        TrashModuleSpec("course", "课程", Course, "title", "course:write",
                        "tombstone", {"title": TOMBSTONE_TEXT, "description": None}),
        TrashModuleSpec("course_chapter", "课程章节", CourseChapter, "title", "course:write",
                        "tombstone", {"title": TOMBSTONE_TEXT}),
        TrashModuleSpec("quiz_question", "题目", QuizQuestion, "question_text",
                        "quiz:write"),
        TrashModuleSpec("quiz_module", "题库模块", QuizModule, "name", "quiz:write"),
        TrashModuleSpec("quiz_knowledge_point", "知识点", QuizKnowledgePoint, "name",
                        "quiz:write"),
        TrashModuleSpec("quiz_library", "题库", QuizLibrary, "name", "quiz_library_manage"),
    ]
}

# 归档条目（不倒计时）：陈列在回收站「已归档」页签；仅删除条目有 30 天倒计时。
# restorable=False 的模块（批次/协议模板）归档是终态，仅陈列、恢复回原模块处理。
ARCHIVED_MODULES: dict[str, dict[str, Any]] = {
    "course": {"model": Course, "title": "title", "label": "课程",
               "restorable": True, "super_admin_only": True},
    "quiz_library": {"model": QuizLibrary, "title": "name", "label": "题库",
                     "restorable": True},
    "plan": {"model": Plan, "title": "name", "label": "认证批次", "restorable": False},
    "agreement_template": {"model": AgreementTemplate, "title": "title",
                           "label": "协议模板", "restorable": False},
}

# 用户页签特殊口径（决策 10/11）：禁用用户进回收站（恢复=启用，复用 toggle 的
# 黑名单清理），已注销用户（deactivated_at 非空）不进回收站且后台不可见。
USER_MODULE_KEY = "user"


def _apply_tombstone(obj, spec: TrashModuleSpec) -> None:
    for key, value in spec.tombstone_fields.items():
        if hasattr(obj, key):
            setattr(obj, key, value)


async def _has_reference(db, model: Any, column: Any, value: int) -> bool:
    return bool(
        await db.scalar(select(exists().where(column == value)).select_from(model))
    )


# ---------------------------------------------------------------------------
# 引用检查（决定物理删还是墓碑）
# ---------------------------------------------------------------------------

async def _activity_has_refs(db, activity_id: int) -> bool:
    return await _has_reference(db, ActivityRegistration, ActivityRegistration.activity_id, activity_id) \
        or await _has_reference(db, ActivityReminder, ActivityReminder.activity_id, activity_id)


async def _job_has_refs(db, job_id: int) -> bool:
    return await _has_reference(db, JobApplication, JobApplication.job_id, job_id)


async def _competition_has_refs(db, competition_id: int) -> bool:
    track_ids = (
        await db.execute(
            select(CompetitionTrack.id).where(CompetitionTrack.competition_id == competition_id)
        )
    ).scalars().all()
    if await _has_reference(db, CompetitionReg, CompetitionReg.competition_id, competition_id):
        return True
    if track_ids and await db.scalar(
        select(exists().where(CompetitionReg.track_id.in_(track_ids)))
    ):
        return True
    return False


async def _course_has_refs(db, course_id: int) -> bool:
    return await _has_reference(db, CourseChapter, CourseChapter.course_id, course_id)


async def _course_chapter_has_refs(db, chapter_id: int) -> bool:
    return await _has_reference(db, UserChapterProgress, UserChapterProgress.chapter_id, chapter_id)


async def _quiz_question_has_refs(db, question_id: int) -> bool:
    checks = [
        (QuizPracticeSessionQuestion, QuizPracticeSessionQuestion.question_id),
        (QuizWrongItem, QuizWrongItem.question_id),
        (QuizCollection, QuizCollection.question_id),
        (QuizExamQuestion, QuizExamQuestion.question_id),
        (QuizQuestionRevision, QuizQuestionRevision.question_id),
    ]
    return any(await _has_reference(db, m, c, question_id) for m, c in checks)


async def _quiz_point_has_refs(db, point_id: int) -> bool:
    return await _has_reference(db, QuizQuestion, QuizQuestion.knowledge_point_id, point_id)


async def _quiz_module_has_refs(db, module_id: int) -> bool:
    return await _has_reference(db, QuizKnowledgePoint, QuizKnowledgePoint.module_id, module_id)


async def _quiz_library_has_refs(db, library_id: int) -> bool:
    checks = [
        (QuizQuestion, QuizQuestion.library_id),
        (QuizModule, QuizModule.library_id),
        (QuizCourseLibraryBinding, QuizCourseLibraryBinding.library_id),
        (QuizActivationCode, QuizActivationCode.library_id),
    ]
    return any(await _has_reference(db, m, c, library_id) for m, c in checks)


async def _purge_quiz_question(db, question: QuizQuestion) -> str:
    """到期题目：无引用物理删（含修订/统计）；有引用转墓碑。返回动作。"""
    referenced = await _quiz_question_has_refs(db, int(question.id))
    # 修订与统计在两种路径下都清掉（墓碑不留历史内容，物理删为 RESTRICT 外键让路）
    await db.execute(
        delete(QuizQuestionRevisionStats).where(
            QuizQuestionRevisionStats.question_id == question.id
        )
    )
    await db.execute(
        delete(QuizQuestionRevision).where(QuizQuestionRevision.question_id == question.id)
    )
    question.current_revision_id = None
    question.pending_revision_id = None
    if referenced:
        question.question_text = TOMBSTONE_TEXT
        question.normalized_question_text = TOMBSTONE_TEXT
        question.options = None
        question.option_image_urls = None
        question.correct_answer = None
        question.explanation = None
        question.image_urls = []
        question.restore_until = None
        return "tombstone"
    await db.delete(question)
    return "physical"


async def _purge_generic(db, spec: TrashModuleSpec, obj) -> str:
    if spec.purge_policy != "tombstone":
        await db.delete(obj)
        return "physical"
    check = {
        "activity": _activity_has_refs,
        "job": _job_has_refs,
        "competition": _competition_has_refs,
        "course": _course_has_refs,
        "course_chapter": _course_chapter_has_refs,
        "quiz_module": lambda d, i: _quiz_module_has_refs(d, i),
        "quiz_knowledge_point": lambda d, i: _quiz_point_has_refs(d, i),
        "quiz_library": lambda d, i: _quiz_library_has_refs(d, i),
    }.get(spec.key)
    if check is None:
        await db.delete(obj)
        return "physical"
    if await check(db, int(obj.id)):
        _apply_tombstone(obj, spec)
        obj.restore_until = None
        return "tombstone"
    await db.delete(obj)
    return "physical"


class RecycleBinService:
    """统一回收站：列表 / 恢复 / 立即彻底删除 / 到期自动清理。"""

    # -- 查询 ---------------------------------------------------------------

    async def summary(self, visible_keys: list[str]) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            out = []
            for key in visible_keys:
                if key == USER_MODULE_KEY:
                    disabled_count = (
                        await db.scalar(
                            select(func.count())
                            .select_from(User)
                            .where(
                                User.is_active == False,  # noqa: E712
                                User.deactivated_at.is_(None),
                            )
                        )
                        or 0
                    )
                    out.append({"module": key, "label": "用户", "deleted_count": disabled_count})
                    continue
                spec = TRASH_MODULES[key]
                deleted_count = (
                    await db.scalar(
                        select(func.count())
                        .select_from(spec.model)
                        .where(spec.model.deleted_at.is_not_null())
                    )
                    or 0
                )
                out.append({"module": key, "label": spec.label, "deleted_count": deleted_count})
            return out

    async def list_items(
        self,
        spec: TrashModuleSpec,
        *,
        keyword: str | None,
        page: int,
        page_size: int,
        teacher_admin_id: int | None = None,
    ) -> dict[str, Any]:
        async with get_db_ctx() as db:
            if spec.key == USER_MODULE_KEY:
                return await self._list_disabled_users(
                    db, keyword=keyword, page=page, page_size=page_size
                )
            base = select(spec.model).where(spec.model.deleted_at.is_not_null())
            if keyword and spec.title_attr:
                base = base.where(getattr(spec.model, spec.title_attr).ilike(f"%{keyword}%"))
            if spec.teacher_scoped and teacher_admin_id is not None:
                # 课堂内容按归属隔离：老师只能看到自己课堂的条目
                base = base.where(
                    spec.model.classroom_id.in_(
                        select(Classroom.id).where(
                            Classroom.teacher_admin_id == teacher_admin_id
                        )
                    )
                )
            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(spec.model.deleted_at.desc(), spec.model.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            now = utcnow()
            items = [self._to_item(spec, row, now) for row in rows]
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def _list_disabled_users(
        self, db, *, keyword: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        base = select(User).where(
            User.is_active == False,  # noqa: E712
            User.deactivated_at.is_(None),
        )
        if keyword:
            pattern = f"%{keyword}%"
            base = base.where(or_(User.phone.ilike(pattern), User.openid.ilike(pattern)))
        total = (
            await db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        rows = (
            await db.execute(
                base.order_by(User.updated_at.desc(), User.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        items = [
            {
                "module": USER_MODULE_KEY,
                "id": int(u.id),
                "title": u.phone or f"用户 #{u.id}",
                "status": "deleted",
                "deleted_at": u.updated_at,
                "restore_until": None,
                "expired": False,
            }
            for u in rows
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def _to_item(self, spec: TrashModuleSpec, row, now: datetime) -> dict[str, Any]:
        title = (
            str(getattr(row, spec.title_attr))
            if spec.title_attr
            else f"{spec.label} #{row.id}"
        )
        return {
            "module": spec.key,
            "id": int(row.id),
            "title": title,
            "status": "deleted",
            "deleted_at": row.deleted_at,
            "restore_until": row.restore_until,
            "expired": bool(row.restore_until and row.restore_until <= now),
        }

    # -- 恢复 ---------------------------------------------------------------

    async def restore_items(self, spec: TrashModuleSpec, ids: list[int], *, actor_id: int | None = None) -> int:
        # quiz 四类对象必须走 V2 服务的 undo_delete（有状态映射：曾发布→停用、纯草稿→草稿），
        # 通用恢复只清时间戳，status 仍是 deleted 会导致"恢复无效"。
        if spec.key in {"quiz_question", "quiz_module", "quiz_knowledge_point", "quiz_library"}:
            return await self._restore_quiz_items(spec.key, ids, actor_id=actor_id)
        if spec.key == USER_MODULE_KEY:
            # 恢复用户 = 重新启用（toggle 内已同事务清注销黑名单）
            from app.services.admin_user import AdminUserService

            restored = 0
            for user_id in ids:
                try:
                    await AdminUserService().toggle_user_status(
                        user_id, True, actor_id=actor_id
                    )
                    restored += 1
                except NotFoundException:
                    continue
            return restored
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(spec.model).where(
                        spec.model.id.in_(ids), spec.model.deleted_at.is_not_null()
                    )
                )
            ).scalars().all()
            now = utcnow()
            restored = 0
            for row in rows:
                if row.restore_until and row.restore_until <= now:
                    raise BusinessException(
                        f"「{self._title(spec, row)}」已超过 {RESTORE_WINDOW.days} 天，无法恢复"
                    )
                restore_row(row)
                restored += 1
            await db.commit()
            return restored

    async def _restore_quiz_items(self, key: str, ids: list[int], *, actor_id: int | None) -> int:
        from app.schemas.admin_quiz_contract import (
            AdminQuizLibraryStatusUpdate,
            AdminQuizVersionRequest,
        )
        from app.services.admin_quiz_v2 import AdminQuizV2Service

        admin_id = actor_id or 0
        service = AdminQuizV2Service()
        now = utcnow()
        # 先取当前 lock_version 并做过期校验（undo 内部还会二次校验版本）
        lock_versions: dict[int, int] = {}
        async with get_db_ctx() as db:
            model = TRASH_MODULES[key].model
            rows = (
                await db.execute(
                    select(model).where(model.id.in_(ids), model.deleted_at.is_not_null())
                )
            ).scalars().all()
            for row in rows:
                if row.restore_until and row.restore_until <= now:
                    raise BusinessException(
                        f"「{self._title(TRASH_MODULES[key], row)}」已超过 {RESTORE_WINDOW.days} 天，无法恢复"
                    )
                lock_versions[int(row.id)] = int(row.lock_version)
        restored = 0
        for item_id in ids:
            lock_version = lock_versions.get(item_id)
            if lock_version is None:
                continue
            try:
                if key == "quiz_question":
                    await service.transition_question(
                        item_id, AdminQuizVersionRequest(lock_version=lock_version),
                        "undo_delete", admin_id=admin_id,
                    )
                elif key == "quiz_module":
                    await service.undo_delete_module(
                        item_id, AdminQuizVersionRequest(lock_version=lock_version),
                        admin_id=admin_id,
                    )
                elif key == "quiz_knowledge_point":
                    await service.undo_delete_knowledge_point(
                        item_id, AdminQuizVersionRequest(lock_version=lock_version),
                        admin_id=admin_id,
                    )
                else:
                    await service.transition_library(
                        item_id,
                        AdminQuizLibraryStatusUpdate(action="undo_delete", lock_version=lock_version),
                        admin_id=admin_id,
                    )
                restored += 1
            except NotFoundException:
                continue
        return restored

    def _title(self, spec: TrashModuleSpec, row) -> str:
        return (
            str(getattr(row, spec.title_attr))
            if spec.title_attr
            else f"{spec.label} #{row.id}"
        )

    # -- 立即彻底删除（super_admin） ----------------------------------------

    async def purge_items(self, spec: TrashModuleSpec, ids: list[int]) -> dict[str, int]:
        if spec.key == USER_MODULE_KEY:
            raise BusinessException("用户账号不做物理删除，过期账号由系统清理任务按其规则处理")
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(spec.model).where(
                        spec.model.id.in_(ids), spec.model.deleted_at.is_not_null()
                    )
                )
            ).scalars().all()
            result = {"physical": 0, "tombstone": 0}
            for row in rows:
                if spec.key == "quiz_question":
                    action = await _purge_quiz_question(db, row)
                else:
                    action = await _purge_generic(db, spec, row)
                result[action] += 1
            await db.commit()
            return result

    # -- 归档陈列（不倒计时） -------------------------------------------------

    async def archived_summary(self) -> dict[str, int]:
        async with get_db_ctx() as db:
            out: dict[str, int] = {}
            for key, meta in ARCHIVED_MODULES.items():
                out[key] = (
                    await db.scalar(
                        select(func.count())
                        .select_from(meta["model"])
                        .where(meta["model"].status == "archived")
                    )
                    or 0
                )
            return out

    async def list_archived(
        self, module_key: str, *, keyword: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        meta = ARCHIVED_MODULES.get(module_key)
        if meta is None:
            raise NotFoundException("归档模块")
        model, title = meta["model"], meta["title"]
        async with get_db_ctx() as db:
            base = select(model).where(model.status == "archived")
            if keyword:
                base = base.where(getattr(model, title).ilike(f"%{keyword}%"))
            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(model.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            items = [
                {
                    "module": module_key,
                    "id": int(r.id),
                    "title": str(getattr(r, title)),
                    "status": "archived",
                    "deleted_at": None,
                    "restore_until": None,
                    "expired": False,
                    "restorable": bool(meta["restorable"]),
                }
                for r in rows
            ]
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def restore_archived(self, module_key: str, ids: list[int], *, admin_id: int, is_super_admin: bool) -> int:
        meta = ARCHIVED_MODULES.get(module_key)
        if meta is None or not meta["restorable"]:
            raise BusinessException("该模块的归档条目为终态，请在原模块处理")
        if meta.get("super_admin_only") and not is_super_admin:
            raise ForbiddenException("恢复已归档课程仅超级管理员可操作")
        restored = 0
        if module_key == "course":
            from app.services.admin_course import AdminCourseService

            for course_id in ids:
                try:
                    await AdminCourseService().change_lifecycle(
                        course_id, "restore", admin_id=admin_id
                    )
                    restored += 1
                except NotFoundException:
                    continue
            return restored
        if module_key == "quiz_library":
            from app.schemas.admin_quiz_contract import AdminQuizLibraryStatusUpdate
            from app.services.admin_quiz_v2 import AdminQuizV2Service

            async with get_db_ctx() as db:
                lock_versions = {
                    int(row.id): int(row.lock_version)
                    for row in (
                        await db.execute(
                            select(QuizLibrary.id, QuizLibrary.lock_version).where(
                                QuizLibrary.id.in_(ids),
                                QuizLibrary.status == "archived",
                            )
                        )
                    ).all()
                }
            service = AdminQuizV2Service()
            for library_id in ids:
                lock_version = lock_versions.get(library_id)
                if lock_version is None:
                    continue
                try:
                    await service.transition_library(
                        library_id,
                        AdminQuizLibraryStatusUpdate(
                            action="restore", lock_version=lock_version
                        ),
                        admin_id=admin_id,
                    )
                    restored += 1
                except (BusinessException, NotFoundException):
                    continue
            return restored
        raise BusinessException("该模块的归档条目不支持恢复")

    async def delete_archived(self, module_key: str, ids: list[int], *, admin_id: int) -> int:
        """归档条目 → 移入回收站（删除态，启动 30 天倒计时）。

        仅题库支持：原「题库管理页归档视图」的移入回收站入口随视图一起收编到这里。
        """
        if module_key != "quiz_library":
            raise BusinessException("该模块不支持从归档移入回收站")
        from app.schemas.admin_quiz_contract import AdminQuizLibraryStatusUpdate
        from app.services.admin_quiz_v2 import AdminQuizV2Service

        deleted = 0
        async with get_db_ctx() as db:
            lock_versions = {
                int(row.id): int(row.lock_version)
                for row in (
                    await db.execute(
                        select(QuizLibrary.id, QuizLibrary.lock_version).where(
                            QuizLibrary.id.in_(ids),
                            QuizLibrary.status == "archived",
                        )
                    )
                ).all()
            }
        service = AdminQuizV2Service()
        for library_id in ids:
            lock_version = lock_versions.get(library_id)
            if lock_version is None:
                continue
            try:
                await service.transition_library(
                    library_id,
                    AdminQuizLibraryStatusUpdate(action="delete", lock_version=lock_version),
                    admin_id=admin_id,
                )
                deleted += 1
            except (BusinessException, NotFoundException):
                continue
        return deleted

    # -- 到期自动清理（cleanup_loop 调用） -----------------------------------

    async def purge_expired(self) -> dict[str, int]:
        now = utcnow()
        result = {"physical": 0, "tombstone": 0}
        async with get_db_ctx() as db:
            for spec in TRASH_MODULES.values():
                rows = (
                    await db.execute(
                        select(spec.model)
                        .where(
                            spec.model.deleted_at.is_not_null(),
                            spec.model.restore_until.is_not_null(),
                            spec.model.restore_until <= now,
                        )
                        .limit(500)
                    )
                ).scalars().all()
                for row in rows:
                    if spec.key == "quiz_question":
                        action = await _purge_quiz_question(db, row)
                    else:
                        action = await _purge_generic(db, spec, row)
                    result[action] += 1
            await db.commit()
        return result
