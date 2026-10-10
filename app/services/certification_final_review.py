"""Final review bound to an effective certification export version."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db_ctx
from app.domain.h3c.src.index import (
    H3cExamBatch,
    H3cExportItem,
    H3cExportJob,
    H3cFinalReview,
    H3cRegistration,
    H3cRegistrationVersion,
)
from app.domain.nisp.src.index import (
    NispExamBatch,
    NispExportItem,
    NispExportJob,
    NispFinalReview,
    NispRegistration,
    NispRegistrationVersion,
)
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.h3c_registration import (
    H3cBatchFinalReviewRequest,
    H3cBatchFinalReviewResponse,
    H3cFinalReviewRequest,
    H3cRegistrationResponse,
    CertificationFinalReviewResult,
)
from app.schemas.nisp import (
    NispBatchFinalReviewRequest,
    NispBatchFinalReviewResponse,
    NispFinalReviewRequest,
    NispFinalReviewResult,
    NispRegistrationResponse,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _lock_h3c_chain(db: AsyncSession, registration_id: int):
    refs = (
        await db.execute(
        select(H3cRegistration.batch_id, H3cRegistration.plan_id, H3cRegistration.order_id)
        .where(H3cRegistration.id == registration_id)
        )
    ).first()
    if refs is None:
        raise NotFoundException("H3C 报名")
    plan = await db.scalar(select(Plan).where(Plan.id == refs.plan_id).with_for_update())
    batch = await db.scalar(
        select(H3cExamBatch).where(H3cExamBatch.id == refs.batch_id).with_for_update()
    )
    order = await db.scalar(select(Order).where(Order.id == refs.order_id).with_for_update())
    registration = await db.scalar(
        select(H3cRegistration)
        .where(H3cRegistration.id == registration_id)
        .with_for_update()
    )
    if plan is None or batch is None or order is None or registration is None:
        raise NotFoundException("H3C 报名")
    return registration, order


async def _lock_nisp_chain(db: AsyncSession, registration_id: int):
    refs = (
        await db.execute(
        select(NispRegistration.batch_id, NispRegistration.plan_id, NispRegistration.order_id)
        .where(NispRegistration.id == registration_id)
        )
    ).first()
    if refs is None:
        raise NotFoundException("NISP 报名")
    plan = await db.scalar(select(Plan).where(Plan.id == refs.plan_id).with_for_update())
    batch = await db.scalar(
        select(NispExamBatch).where(NispExamBatch.id == refs.batch_id).with_for_update()
    )
    order = await db.scalar(select(Order).where(Order.id == refs.order_id).with_for_update())
    registration = await db.scalar(
        select(NispRegistration)
        .where(NispRegistration.id == registration_id)
        .with_for_update()
    )
    if plan is None or batch is None or order is None or registration is None:
        raise NotFoundException("NISP 报名")
    return registration, order


class H3cFinalReviewService:
    async def review(
        self, *, admin_id: int, registration_id: int, data: H3cFinalReviewRequest
    ) -> H3cRegistrationResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                registration, _order = await _lock_h3c_chain(db, registration_id)
                version = await db.scalar(
                    select(H3cRegistrationVersion)
                    .where(
                        H3cRegistrationVersion.registration_id == registration.id,
                        H3cRegistrationVersion.is_current.is_(True),
                    )
                    .with_for_update()
                )
                if version is None:
                    raise ConflictException("H3C 报名缺少当前信息版本")
                item = await db.scalar(
                    select(H3cExportItem)
                    .where(H3cExportItem.id == data.export_item_id)
                    .with_for_update()
                )
                if item is None or item.registration_id != registration.id:
                    raise NotFoundException("H3C 导出版本")
                job = await db.scalar(
                    select(H3cExportJob).where(H3cExportJob.id == item.job_id).with_for_update()
                )
                existing = await db.scalar(
                    select(H3cFinalReview)
                    .where(H3cFinalReview.registration_id == registration.id)
                    .with_for_update()
                )
                self._validate(registration, version, item, job)
                if existing is not None:
                    if existing.decision != data.decision:
                        raise ConflictException("H3C 报名已有终审结果，不能重复终审")
                else:
                    db.add(H3cFinalReview(
                        registration_id=registration.id,
                        export_job_id=job.id,
                        export_item_id=item.id,
                        registration_version_id=version.id,
                        material_version_ids=list(item.material_versions or {}),
                        decision=data.decision,
                        reason_code=data.reason_code,
                        reason_detail=data.reason_detail,
                        reviewer_admin_id=admin_id,
                        reviewed_at=_now(),
                    ))
                if data.decision == "approved":
                    registration.status = "final_approved"
            from app.services.h3c_registration import H3cRegistrationService
            return await H3cRegistrationService()._admin_registration(registration_id)

    @staticmethod
    def _validate(
        registration: H3cRegistration,
        version: H3cRegistrationVersion,
        item: H3cExportItem,
        job: H3cExportJob | None,
    ) -> None:
        if registration.status != "approved":
            raise ConflictException("仅初审通过报名可以终审")
        if job is None or job.status != "succeeded":
            raise ConflictException("终审必须基于成功导出的版本")
        if not item.is_valid:
            raise ConflictException("导出版本已失效，请重新导出")
        if item.registration_version_id != version.id:
            raise ConflictException("导出版本与当前报名版本不一致")

    async def review_batch(
        self, *, admin_id: int, data: H3cBatchFinalReviewRequest
    ) -> H3cBatchFinalReviewResponse:
        results = []
        for registration_id in data.registration_ids:
            try:
                item_id = await self._effective_item_id(
                    registration_id=registration_id, export_job_id=data.export_job_id
                )
                await self.review(
                    admin_id=admin_id,
                    registration_id=registration_id,
                    data=H3cFinalReviewRequest(export_item_id=item_id),
                )
                results.append(CertificationFinalReviewResult(
                    registration_id=registration_id, success=True
                ))
            except Exception as exc:
                results.append(CertificationFinalReviewResult(
                    registration_id=registration_id,
                    success=False,
                    reason=exc.message if hasattr(exc, "message") else type(exc).__name__,
                ))
        return H3cBatchFinalReviewResponse(items=results)

    @staticmethod
    async def _effective_item_id(*, registration_id: int, export_job_id: int) -> int:
        async with get_db_ctx() as db:
            item = await db.scalar(
                select(H3cExportItem.id)
                .join(H3cExportJob, H3cExportJob.id == H3cExportItem.job_id)
                .where(
                    H3cExportItem.registration_id == registration_id,
                    H3cExportItem.job_id == export_job_id,
                    H3cExportItem.is_valid.is_(True),
                    H3cExportJob.status == "succeeded",
                )
                .limit(1)
            )
            if item is None:
                raise ConflictException("没有可用于终审的导出版本")
            return item


class NispFinalReviewService:
    async def review(
        self, *, admin_id: int, registration_id: int, data: NispFinalReviewRequest
    ) -> NispRegistrationResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                registration, _order = await _lock_nisp_chain(db, registration_id)
                version = await db.scalar(
                    select(NispRegistrationVersion)
                    .where(
                        NispRegistrationVersion.registration_id == registration.id,
                        NispRegistrationVersion.is_current.is_(True),
                    )
                    .with_for_update()
                )
                if version is None:
                    raise ConflictException("NISP 报名缺少当前信息版本")
                item = await db.scalar(
                    select(NispExportItem)
                    .where(NispExportItem.id == data.export_item_id)
                    .with_for_update()
                )
                if item is None or item.registration_id != registration.id:
                    raise NotFoundException("NISP 导出版本")
                job = await db.scalar(
                    select(NispExportJob).where(NispExportJob.id == item.job_id).with_for_update()
                )
                existing = await db.scalar(
                    select(NispFinalReview)
                    .where(NispFinalReview.registration_id == registration.id)
                    .with_for_update()
                )
                if registration.status != "approved":
                    raise ConflictException("仅初审通过报名可以终审")
                if job is None or job.status != "succeeded" or not item.is_valid:
                    raise ConflictException("导出版本不可用")
                if item.registration_version_id != version.id:
                    raise ConflictException("导出版本与当前报名版本不一致")
                if existing is None:
                    db.add(NispFinalReview(
                        registration_id=registration.id,
                        export_job_id=job.id,
                        export_item_id=item.id,
                        registration_version_id=version.id,
                        material_version_ids=list(item.material_versions or {}),
                        decision=data.decision,
                        reason_code=data.reason_code,
                        reason_detail=data.reason_detail,
                        reviewer_admin_id=admin_id,
                        reviewed_at=_now(),
                    ))
                if data.decision == "approved":
                    registration.status = "final_approved"
            from app.services.nisp_registration import NispRegistrationService
            return await NispRegistrationService().get_admin_registration(registration_id)

    async def review_batch(
        self, *, admin_id: int, data: NispBatchFinalReviewRequest
    ) -> NispBatchFinalReviewResponse:
        results = []
        for registration_id in data.registration_ids:
            try:
                async with get_db_ctx() as db:
                    item_id = await db.scalar(
                        select(NispExportItem.id)
                        .join(NispExportJob, NispExportJob.id == NispExportItem.job_id)
                        .where(
                            NispExportItem.registration_id == registration_id,
                            NispExportItem.job_id == data.export_job_id,
                            NispExportItem.is_valid.is_(True),
                            NispExportJob.status == "succeeded",
                        )
                        .limit(1)
                    )
                if item_id is None:
                    raise ConflictException("没有可用于终审的导出版本")
                await self.review(
                    admin_id=admin_id,
                    registration_id=registration_id,
                    data=NispFinalReviewRequest(export_item_id=item_id),
                )
                results.append(NispFinalReviewResult(registration_id=registration_id, success=True))
            except Exception as exc:
                results.append(NispFinalReviewResult(
                    registration_id=registration_id,
                    success=False,
                    reason=exc.message if hasattr(exc, "message") else type(exc).__name__,
                ))
        return NispBatchFinalReviewResponse(items=results)
