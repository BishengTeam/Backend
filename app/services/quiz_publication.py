"""Read-only preflight, explicit ownership repair and resumable publication.

Each worker tick commits at most one 100-question atomic chunk or one library
stage. The stage cursor and its business changes share the same transaction.
"""
from __future__ import annotations

import hashlib
import json
import logging
from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.domain.community.src.model.quiz import (
    QuizPublicationOperation, QuizQuestion, QuizQuestionRevision, QuizLibrary,
    QuizModule, QuizKnowledgePoint, QuizMigrationIssue, QuizImportJob,
)
from app.domain.user.src.index import AdminUser
from app.policy.permissions import ROLE_PERMISSIONS
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ForbiddenException, ValidationException
from app.schemas.admin_quiz_contract import (
    AdminQuizPublicationRequest, AdminQuizBatchRequest, AdminQuizLibraryStatusUpdate,
    AdminQuizLibraryUpdate, AdminQuizOwnershipMoveRequest,
)
from app.services.admin_quiz_v2 import AdminQuizV2Service
from app.services.quiz_question_pool import practice_pool


class QuizPublicationService:
    @staticmethod
    def require_active_library(library):
        if library is None:
            raise NotFoundException("题库")
        if library.status in {"archived", "deleted"}:
            raise BusinessException("题库已归档或已移入回收站，不能执行发布")

    @staticmethod
    async def permission(db, admin_id, permission):
        admin = await db.get(AdminUser, admin_id)
        allowed = ROLE_PERMISSIONS.get(admin.role, []) if admin else []
        if not admin or not admin.is_active or admin.must_change_password or ("*" not in allowed and permission not in allowed):
            raise ForbiddenException(f"缺少权限或账号不可用：{permission}")

    @staticmethod
    def response(job):
        return {"id": job.id, "library_id": job.library_id, "status": job.status,
                "progress": job.progress, "error_message": job.error_message,
                "created_at": job.created_at, "updated_at": job.updated_at}

    async def preflight(self, library_id, *, db=None):
        async with AdminQuizV2Service._database(db) as session:
            library = await session.get(QuizLibrary, library_id)
            if not library:
                raise NotFoundException("题库")
            rows = list((await session.execute(select(QuizQuestion, QuizKnowledgePoint, QuizModule)
                .outerjoin(QuizKnowledgePoint, QuizKnowledgePoint.id == QuizQuestion.knowledge_point_id)
                .join(QuizModule, QuizModule.id == QuizQuestion.module_id)
                .where(QuizQuestion.library_id == library_id, QuizQuestion.status != "deleted")
                .order_by(QuizQuestion.id))).all())
            counts = {key: 0 for key in ("published", "draft", "abnormal", "default_ownership", "unavailable_ownership", "system_ownership")}
            counts["published"] = int(await session.scalar(
                practice_pool(select(func.count())).where(QuizQuestion.library_id == library_id)
            ) or 0)
            repairable = []
            revisions = {r.id: r for r in (await session.execute(select(QuizQuestionRevision).where(
                QuizQuestionRevision.id.in_([q.pending_revision_id for q, _, _ in rows if q.pending_revision_id])))).scalars()}
            candidates = []
            for q, point, module in rows:
                # quiz023：模块直挂即可用；知识点存在时须可用且归属一致。
                usable = bool(module and module.library_id == library_id
                    and module.status == "active" and module.system_kind == "none"
                    and (q.knowledge_point_id is None or (
                        point and point.library_id == library_id
                        and point.status == "active" and point.system_kind == "none")))
                if q.pending_revision_id: counts["draft"] += 1
                if q.import_default_ownership: counts["default_ownership"] += 1
                if (point and point.system_kind != "none") or (module and module.system_kind != "none"):
                    counts["system_ownership"] += 1
                if not usable: counts["unavailable_ownership"] += 1
                revision = revisions.get(q.pending_revision_id)
                if revision:
                    try:
                        AdminQuizV2Service._normalize_question(question_type=revision.question_type, question_text=revision.question_text,
                            options=revision.options, correct_answer=revision.correct_answer, explanation=revision.explanation,
                            image_urls=revision.image_urls, option_image_urls=revision.option_image_urls, require_publishable=True)
                    except (BusinessException, ValidationException):
                        counts["abnormal"] += 1
                    else:
                        if usable and q.status in {"draft", "published"}:
                            candidates.append({"question_id": q.id, "revision_id": revision.id, "lock_version": q.lock_version})
            if counts["unavailable_ownership"]:
                repairable.append(f"有 {counts['unavailable_ownership']} 道题目的归属不可用，请筛选后批量调整归属")
            blockers = await AdminQuizV2Service()._publication_blockers(session, library)
            waiting = int(await session.scalar(select(func.count()).select_from(QuizImportJob).where(
                QuizImportJob.library_id == library_id, QuizImportJob.status == "awaiting_category_confirmation")) or 0)
            fingerprint = [library.lock_version, [(q.id, q.lock_version, q.pending_revision_id,
                point.lock_version if point else None, module.lock_version if module else None) for q, point, module in rows]]
            return {"library_id": library_id, "lock_version": library.lock_version, "status": library.status,
                "access_mode": library.access_mode, "entry_open": library.v2_enabled,
                "counts": counts, "blockers": blockers, "repairable": repairable,
                "warnings": ([f"{counts['default_ownership']} 道题目使用系统补齐的正式归属，可进一步细分，不影响发布"] if counts["default_ownership"] else []),
                "waiting_import_count": waiting, "candidates": candidates,
                "question_previews": {str(q.id): revisions[q.pending_revision_id].question_text for q, _, _ in rows if q.pending_revision_id in revisions},
                "preflight_version": hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()}

    async def move(self, data: AdminQuizOwnershipMoveRequest, *, admin_id):
        svc = AdminQuizV2Service()
        async with get_db_ctx() as db:
            # Library first: serialize moves/imports and protect the effective pool.
            library = await svc._locked(db, QuizLibrary, data.library_id)
            if not library: raise NotFoundException("题库")
            # quiz023：目标可为知识点或模块（直挂），二选一。
            if data.knowledge_point_id is not None:
                target = await svc._locked(db, QuizKnowledgePoint, data.knowledge_point_id)
                if not target: raise NotFoundException("知识点")
                await svc._locked(db, QuizModule, target.module_id)
                _, _, point = await svc._active_point(db, data.knowledge_point_id)
                if point.library_id != library.id: raise BusinessException("只能调整到同一题库内的知识点")
                target_point_id: int | None = point.id
                target_module_id = int(point.module_id)
            else:
                module = await svc._locked(db, QuizModule, data.module_id)
                if not module: raise NotFoundException("模块")
                _, module = await svc._active_module(db, data.module_id)
                if module.library_id != library.id: raise BusinessException("只能调整到同一题库内的模块")
                target_point_id = None
                target_module_id = int(module.id)
            questions = []
            for item in sorted(data.items, key=lambda x: x.question_id):
                q = await svc._locked(db, QuizQuestion, item.question_id)
                if not q or q.library_id != library.id or q.status == "deleted":
                    raise BusinessException("题目不存在或不属于当前题库")
                svc._check_version(q, item.lock_version)
                questions.append(q)
            for q in questions:
                before = svc._question_fields(q)
                q.knowledge_point_id = target_point_id
                q.module_id = target_module_id
                q.import_default_ownership = False
                q.lock_version += 1
                q.updated_by = admin_id
                svc._audit(db, admin_id=admin_id, action="question.move", object_type="question", object_id=q.id,
                    before=before, after=svc._question_fields(q), permission="quiz_content_edit")
            await db.flush()
            await svc._reconcile_library_migration_issues(db, library, now=svc._now())
            await db.commit()
            return {"updated_count": len(questions)}

    async def create(self, library_id, data: AdminQuizPublicationRequest, *, admin_id):
        async with get_db_ctx() as db:
            library = await AdminQuizV2Service._locked(db, QuizLibrary, library_id)
            self.require_active_library(library)
            old = await db.scalar(select(QuizPublicationOperation).where(QuizPublicationOperation.request_key == data.request_key))
            payload = data.model_dump(mode="json")
            if old:
                if old.admin_id != admin_id or old.library_id != library_id or old.payload != payload:
                    raise ConflictException("该请求标识已被其他发布操作使用")
                return self.response(old)
            AdminQuizV2Service._check_version(library, data.lock_version)
            preflight = await self.preflight(library_id, db=db)
            if preflight["preflight_version"] != data.preflight_version:
                raise ConflictException("题库内容已变化，请重新检查并确认")
            candidates = {(i["question_id"], i["revision_id"], i["lock_version"]) for i in preflight["candidates"]}
            if any((i.question_id, i.revision_id, i.lock_version) not in candidates for i in data.items):
                raise BusinessException("选定题目未通过内容或归属检查，请返回题目处理")
            for permission in self.required_permissions(data):
                await self.permission(db, admin_id, permission)
            job = QuizPublicationOperation(library_id=library_id, admin_id=admin_id, request_key=data.request_key,
                payload=payload, status="queued", progress={"questions": 0, "library": False, "entry": False,
                    "library_version": library.lock_version, "phase": "questions"})
            db.add(job)
            await db.commit()
            return self.response(job)

    @staticmethod
    def required_permissions(data):
        return (["quiz_content_publish"] if data.items else []) + (["quiz_library_manage"] if data.publish_library or data.open_entry else [])

    async def get(self, job_id):
        async with get_db_ctx() as db:
            job = await db.get(QuizPublicationOperation, job_id)
            if not job: raise NotFoundException("发布任务")
            return self.response(job)

    async def list(self, library_id):
        async with get_db_ctx() as db:
            jobs = (await db.execute(select(QuizPublicationOperation).where(
                QuizPublicationOperation.library_id == library_id).order_by(QuizPublicationOperation.id.desc()).limit(20))).scalars()
            return [self.response(job) for job in jobs]

    async def resume(self, job_id, *, admin_id, preflight_version):
        async with get_db_ctx() as db:
            job = await AdminQuizV2Service._locked(db, QuizPublicationOperation, job_id)
            if not job: raise NotFoundException("发布任务")
            data = AdminQuizPublicationRequest.model_validate(job.payload)
            for permission in self.required_permissions(data): await self.permission(db, admin_id, permission)
            if job.status == "failed":
                library = await AdminQuizV2Service._locked(db, QuizLibrary, job.library_id)
                self.require_active_library(library)
                check = await self.preflight(job.library_id, db=db)
                if preflight_version != check["preflight_version"]:
                    raise ConflictException("题库已变化，请重新检查后确认继续")
                candidates = {(i["question_id"], i["revision_id"], i["lock_version"]) for i in check["candidates"]}
                if any((i.question_id, i.revision_id, i.lock_version) not in candidates for i in data.items[job.progress["questions"]:]):
                    raise ConflictException("尚未发布的修订已变化，请重新选择；已完成的发布保留")
                # Only the explicitly rechecked configuration version advances.
                # The original exact revision manifest never changes.
                job.progress = {**job.progress, "library_version": library.lock_version}
                job.admin_id = admin_id
                job.status = "queued"
                job.error_message = None
                await db.commit()
                await db.refresh(job)
            return self.response(job)

    async def process_one(self):
        svc = AdminQuizV2Service()
        async with get_db_ctx() as db:
            job = await db.scalar(select(QuizPublicationOperation).where(QuizPublicationOperation.status == "queued")
                .order_by(QuizPublicationOperation.id).with_for_update(skip_locked=True).limit(1))
            if not job: return False
            try:
                async with db.begin_nested():
                    data = AdminQuizPublicationRequest.model_validate(job.payload)
                    progress = dict(job.progress)
                    library = await svc._locked(db, QuizLibrary, job.library_id)
                    self.require_active_library(library)
                    svc._check_version(library, progress["library_version"])
                    cursor = progress["questions"]
                    if cursor < len(data.items):
                        await self.permission(db, job.admin_id, "quiz_content_publish")
                        result = await svc.batch_transition_questions(AdminQuizBatchRequest(items=data.items[cursor:cursor+100]),
                            "publish", admin_id=job.admin_id, _db=db)
                        if not result.succeeded:
                            raise BusinessException("；".join(f"题目{e.question_id}：{e.message}" for e in result.errors))
                        progress["questions"] += result.updated_count
                        progress["phase"] = "questions"
                    elif data.publish_library and not progress["library"]:
                        await self.permission(db, job.admin_id, "quiz_library_manage")
                        action = "restore" if library.status == "suspended" else "publish"
                        if library.status != "published":
                            await svc.transition_library(library.id, AdminQuizLibraryStatusUpdate(action=action, lock_version=library.lock_version), admin_id=job.admin_id, _db=db)
                        elif await svc._publication_blockers(db, library):
                            raise BusinessException("；".join(await svc._publication_blockers(db, library)))
                        progress["library"] = True
                        progress["phase"] = "library"
                    elif data.open_entry and not progress["entry"]:
                        await self.permission(db, job.admin_id, "quiz_library_manage")
                        await svc.update_library(library.id, AdminQuizLibraryUpdate(lock_version=library.lock_version, v2_enabled=True), admin_id=job.admin_id, _db=db)
                        progress["entry"] = True
                        progress["phase"] = "entry"
                    else:
                        job.status = "succeeded"
                        progress["phase"] = "completed"
                    progress["library_version"] = library.lock_version
                    if (progress["questions"] == len(data.items)
                        and (not data.publish_library or progress["library"])
                        and (not data.open_entry or progress["entry"])):
                        job.status = "succeeded"
                        progress["phase"] = "completed"
                    job.progress = progress
            except (BusinessException, ConflictException, NotFoundException, ForbiddenException, ValidationException) as exc:
                job.status = "failed"
                job.error_message = exc.message
            except Exception:
                logging.getLogger(__name__).exception("Quiz publication stage failed")
                job.status = "failed"
                job.error_message = "发布执行失败，请联系管理员排查后重试"
            await db.commit()
            return True
