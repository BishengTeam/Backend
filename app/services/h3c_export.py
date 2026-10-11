"""Asynchronous official H3C workbook and image-attachment exports."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import shutil
import tempfile
import uuid
import zipfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from copy import copy
from pathlib import Path
import re
from collections import defaultdict
from typing import Sequence

from sqlalchemy import func, or_, select, update as sa_update

from app.adapter.database import get_db_ctx
from app.domain.h3c.src.index import (
    H3cExamBatch,
    H3cExportItem,
    H3cExportJob,
    H3cMaterial,
    H3cRegistration,
    H3cRegistrationVersion,
)
from app.domain.user.src.model.admin_user import AdminUser
from app.domain.plan.src.index import Plan
from app.integrations.h3c_storage import H3cObjectStorage
from app.port.config import settings
from app.port.exceptions import BusinessException, ConflictException, NotFoundException, ThirdPartyException
from app.schemas.common import PaginatedData
from app.schemas.h3c_registration import (
    H3cExportCreate,
    H3cExportItemResponse,
    H3cExportJobResponse,
    H3cExportPrecheckPerson,
    H3cExportPrecheckRequest,
    H3cExportPrecheckResponse,
    H3cSignedUrlResponse,
)


logger = logging.getLogger(__name__)
EXPORT_RETENTION_DAYS = 30
H3C_TEMPLATE_SHA256 = {
    "coupon": "12bea5a00028785a870220ef258261d642fab8ac1c1267c4ab44307bd1471d83",
    "full": "90238331f141e23996231ed6d2a357060b748df94cd5742353d195676a5aca80",
    "student": "c8ea41c819b0c4adfae3236f91ab5e58e072a63414a8125285ce637ccded6201",
}
MATERIAL_SLUGS = {
    "coupon_proof": "coupon-proof",
    "student_proof": "student-proof",
}
REQUIRED_MATERIAL_TYPES = {
    "coupon": "coupon_proof",
    "student": "student_proof",
    "full": None,
}
TYPE_FILE_NAMES = {
    "coupon": "考券报名.xlsx",
    "student": "学生报名.xlsx",
    "full": "全额报名.xlsx",
}
H3C_EXPORT_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
H3C_DATE_COLUMNS = {
    "coupon": {14, 23, 24},
    "full": {12, 20, 21},
    "student": {14, 21, 22},
}
H3C_DATETIME_COLUMNS = {
    "coupon": {18},
    "full": {16},
    "student": {17},
}


@dataclass(frozen=True, slots=True)
class _ExportRow:
    registration: H3cRegistration
    materials: tuple[H3cMaterial, ...]
    item: H3cExportItem | None = None


class H3cExportService:
    def __init__(self, storage: H3cObjectStorage | None = None) -> None:
        self.storage = storage or H3cObjectStorage()

    @staticmethod
    def _precheck_stmt(data: H3cExportPrecheckRequest):
        stmt = select(H3cRegistration).where(H3cRegistration.batch_id == data.batch_id)
        if data.registration_ids is not None:
            stmt = stmt.where(H3cRegistration.id.in_(data.registration_ids))
        else:
            if data.registration_type:
                stmt = stmt.where(
                    H3cRegistration.registration_type == data.registration_type
                )
            if data.status:
                stmt = stmt.where(H3cRegistration.status == data.status)
            if data.keyword:
                pattern = f"%{data.keyword.strip()}%"
                stmt = stmt.where(
                    or_(
                        H3cRegistration.registration_no.ilike(pattern),
                        H3cRegistration.candidate_snapshot["candidate_name"].astext.ilike(
                            pattern
                        ),
                    )
                )
        return stmt.order_by(H3cRegistration.id)

    @staticmethod
    async def _current_versions(
        db, registration_ids: Sequence[int]
    ) -> dict[int, H3cRegistrationVersion]:
        if not registration_ids:
            return {}
        rows = (
            await db.execute(
                select(H3cRegistrationVersion)
                .where(
                    H3cRegistrationVersion.registration_id.in_(registration_ids),
                    H3cRegistrationVersion.is_current.is_(True),
                )
            )
        ).scalars().all()
        return {row.registration_id: row for row in rows}

    @staticmethod
    async def _current_materials(
        db, registration_ids: Sequence[int]
    ) -> dict[int, dict[str, H3cMaterial]]:
        if not registration_ids:
            return {}
        rows = (
            await db.execute(
                select(H3cMaterial)
                .where(
                    H3cMaterial.registration_id.in_(registration_ids),
                    H3cMaterial.is_current.is_(True),
                )
                .order_by(H3cMaterial.registration_id, H3cMaterial.id)
            )
        ).scalars().all()
        result: dict[int, dict[str, H3cMaterial]] = defaultdict(dict)
        for row in rows:
            result[row.registration_id][row.material_type] = row
        return dict(result)

    @staticmethod
    def _material_version(material: H3cMaterial) -> dict:
        return {
            "material_id": material.id,
            "version_no": material.version_no,
            "sha256": material.sha256,
        }

    @staticmethod
    def _export_block_reason(
        *,
        registration: H3cRegistration,
        version: H3cRegistrationVersion | None,
        materials: dict[str, H3cMaterial],
        allowed_statuses: Sequence[str],
    ) -> str | None:
        if registration.status not in allowed_statuses:
            return "当前状态不可导出"
        if version is None:
            return "缺少当前报名信息版本"
        required_type = REQUIRED_MATERIAL_TYPES[registration.registration_type]
        if required_type and required_type not in materials:
            return "缺少必需证明材料"
        return None

    @staticmethod
    def _review_readiness(
        *,
        registration_status: str,
        item: H3cExportItem,
        job_status: str,
        current_version_id: int | None,
    ) -> tuple[bool, str | None]:
        if registration_status == "final_approved":
            return False, "已终审通过"
        if registration_status != "approved":
            return False, "仅初审通过人员可终审"
        if job_status != "succeeded":
            return False, "导出任务未成功"
        if not item.is_valid:
            return False, item.invalidated_reason or "导出版本已失效"
        if current_version_id is None or item.registration_version_id != current_version_id:
            return False, "导出版本与当前报名版本不一致"
        return True, None

    @staticmethod
    async def _job_item_stats(
        db, job_ids: Sequence[int]
    ) -> dict[int, dict]:
        if not job_ids:
            return {}
        rows = (
            await db.execute(
                select(H3cExportItem, H3cRegistration.registration_type)
                .join(
                    H3cRegistration,
                    H3cRegistration.id == H3cExportItem.registration_id,
                )
                .where(H3cExportItem.job_id.in_(job_ids))
                .order_by(H3cExportItem.job_id, H3cExportItem.id)
            )
        ).all()
        result: dict[int, dict] = {
            job_id: {
                "valid_item_count": 0,
                "counts_by_type": {"coupon": 0, "student": 0, "full": 0},
                "registration_ids": [],
            }
            for job_id in job_ids
        }
        for item, registration_type in rows:
            stats = result[item.job_id]
            stats["registration_ids"].append(item.registration_id)
            stats["counts_by_type"][registration_type] += 1
            if item.is_valid:
                stats["valid_item_count"] += 1
        return result

    async def precheck(self, data: H3cExportPrecheckRequest) -> H3cExportPrecheckResponse:
        async with get_db_ctx() as db:
            batch = await db.get(H3cExamBatch, data.batch_id)
            if batch is None:
                raise NotFoundException("H3C 考试批次")
            registrations = (await db.execute(self._precheck_stmt(data))).scalars().all()
            versions = await self._current_versions(db, [row.id for row in registrations])
            materials = await self._current_materials(db, [row.id for row in registrations])
            people: list[H3cExportPrecheckPerson] = []
            exportable_ids: list[int] = []
            counts_by_type = {"coupon": 0, "student": 0, "full": 0}
            for registration in registrations:
                reason = self._export_block_reason(
                    registration=registration,
                    version=versions.get(registration.id),
                    materials=materials.get(registration.id, {}),
                    allowed_statuses=("approved", "final_approved"),
                )
                person = H3cExportPrecheckPerson(
                    registration_id=registration.id,
                    registration_no=registration.registration_no,
                    name=str((registration.candidate_snapshot or {}).get("candidate_name") or ""),
                    registration_type=registration.registration_type,
                    status=registration.status,
                    exportable=reason is None,
                    reason=reason,
                )
                people.append(person)
                if reason is None:
                    exportable_ids.append(registration.id)
                    counts_by_type[registration.registration_type] += 1
            return H3cExportPrecheckResponse(
                selected_count=len(people),
                exportable_count=len(exportable_ids),
                blocked_count=len(people) - len(exportable_ids),
                counts_by_type=counts_by_type,
                exportable_registration_ids=exportable_ids,
                people=people,
            )

    async def create_job(
        self,
        *,
        admin_id: int,
        data: H3cExportCreate,
    ) -> H3cExportJobResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                batch = await db.scalar(
                    select(H3cExamBatch)
                    .where(H3cExamBatch.id == data.batch_id)
                    .with_for_update()
                )
                if batch is None:
                    raise NotFoundException("H3C 考试批次")
                if data.registration_ids is not None:
                    registrations = (
                        await db.execute(
                            select(H3cRegistration)
                            .where(
                                H3cRegistration.batch_id == batch.id,
                                H3cRegistration.id.in_(data.registration_ids),
                            )
                            .order_by(H3cRegistration.id)
                            .with_for_update()
                        )
                    ).scalars().all()
                else:
                    stmt = (
                        select(H3cRegistration)
                        .where(
                            H3cRegistration.batch_id == batch.id,
                            H3cRegistration.status.in_(data.include_statuses),
                        )
                        .order_by(H3cRegistration.id)
                        .with_for_update()
                    )
                    if data.registration_type not in (None, "mixed"):
                        stmt = stmt.where(
                            H3cRegistration.registration_type == data.registration_type
                        )
                    registrations = (await db.execute(stmt)).scalars().all()
                versions = await self._current_versions(db, [row.id for row in registrations])
                materials = await self._current_materials(db, [row.id for row in registrations])
                blocked: list[str] = []
                exportable_registrations: list[H3cRegistration] = []
                for registration in registrations:
                    reason = self._export_block_reason(
                        registration=registration,
                        version=versions.get(registration.id),
                        materials=materials.get(registration.id, {}),
                        allowed_statuses=tuple(data.include_statuses),
                    )
                    if reason is None:
                        exportable_registrations.append(registration)
                    else:
                        blocked.append(
                            f"{registration.registration_no} {reason}"
                        )
                if data.registration_ids is not None:
                    missing_ids = set(data.registration_ids) - {
                        row.id for row in registrations
                    }
                    if missing_ids:
                        blocked.append(f"以下报名不属于该批次：{','.join(map(str, sorted(missing_ids)))}")
                    if blocked:
                        raise ConflictException(
                            "所选人员暂不可导出：" + "；".join(blocked[:20])
                        )
                registrations = exportable_registrations
                if not registrations:
                    raise ConflictException("H3C 导出清单中没有可导出报名")
                job = H3cExportJob(
                    batch_id=batch.id,
                    registration_type=data.registration_type,
                    artifact_type=data.artifact_type,
                    requested_by_admin_id=admin_id,
                    include_statuses=list(data.include_statuses),
                    status="queued",
                    registration_count=len(registrations),
                )
                db.add(job)
                await db.flush()
                for registration in registrations:
                    version = versions[registration.id]
                    if version is None:
                        raise ConflictException("H3C 报名缺少当前信息版本")
                    material_versions = dict(version.material_versions or {})
                    if not material_versions:
                        material_versions = {
                            material_type: self._material_version(material)
                            for material_type, material in materials.get(
                                registration.id, {}
                            ).items()
                        }
                    db.add(
                        H3cExportItem(
                            job_id=job.id,
                            registration_id=registration.id,
                            registration_version_id=version.id,
                            candidate_snapshot=version.candidate_snapshot,
                            material_versions=material_versions,
                            is_valid=True,
                        )
                    )
            return await self.get_job(job.id)

    async def list_jobs(
        self,
        *,
        batch_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cExportJobResponse]:
        async with get_db_ctx() as db:
            stmt = select(H3cExportJob, AdminUser.display_name).outerjoin(
                AdminUser, AdminUser.id == H3cExportJob.requested_by_admin_id
            )
            if batch_id is not None:
                stmt = stmt.where(H3cExportJob.batch_id == batch_id)
            if status:
                stmt = stmt.where(H3cExportJob.status == status)
            total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = (
                await db.execute(
                    stmt.order_by(H3cExportJob.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            stats = await self._job_item_stats(db, [row[0].id for row in rows])
            return PaginatedData(
                items=[
                    self._response(
                        row[0],
                        requested_by_admin_name=row[1],
                        stats=stats.get(row[0].id),
                    )
                    for row in rows
                ],
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def get_job(self, job_id: int) -> H3cExportJobResponse:
        async with get_db_ctx() as db:
            job = await db.get(H3cExportJob, job_id)
            if job is None:
                raise NotFoundException("H3C 导出任务")
            admin_name = await db.scalar(
                select(AdminUser.display_name).where(
                    AdminUser.id == job.requested_by_admin_id
                )
            )
            stats = await self._job_item_stats(db, [job.id])
            return self._response(
                job,
                requested_by_admin_name=admin_name,
                stats=stats.get(job.id),
            )

    async def list_items(
        self,
        *,
        job_id: int,
        registration_type: str | None = None,
        current_status: str | None = None,
        is_valid: bool | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cExportItemResponse]:
        async with get_db_ctx() as db:
            job = await db.get(H3cExportJob, job_id)
            if job is None:
                raise NotFoundException("H3C 导出任务")
            base = (
                select(H3cExportItem, H3cRegistration)
                .join(
                    H3cRegistration,
                    H3cRegistration.id == H3cExportItem.registration_id,
                )
                .where(H3cExportItem.job_id == job.id)
            )
            if registration_type:
                base = base.where(H3cRegistration.registration_type == registration_type)
            if current_status:
                base = base.where(H3cRegistration.status == current_status)
            if is_valid is not None:
                base = base.where(H3cExportItem.is_valid.is_(is_valid))
            if keyword:
                pattern = f"%{keyword.strip()}%"
                base = base.where(
                    or_(
                        H3cRegistration.registration_no.ilike(pattern),
                        H3cRegistration.candidate_snapshot["candidate_name"].astext.ilike(
                            pattern
                        ),
                    )
                )
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (
                await db.execute(
                    base.order_by(H3cExportItem.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            current_versions = await self._current_versions(
                db, [item.registration_id for item, _registration in rows]
            )
            items: list[H3cExportItemResponse] = []
            for item, registration in rows:
                version = current_versions.get(registration.id)
                review_ready, block_reason = self._review_readiness(
                    registration_status=registration.status,
                    item=item,
                    job_status=job.status,
                    current_version_id=version.id if version else None,
                )
                items.append(
                    H3cExportItemResponse(
                        id=item.id,
                        job_id=item.job_id,
                        registration_id=registration.id,
                        registration_no=registration.registration_no,
                        name=str((registration.candidate_snapshot or {}).get("candidate_name") or ""),
                        registration_type=registration.registration_type,
                        current_status=registration.status,
                        version_no=version.version_no if version else None,
                        is_valid=item.is_valid,
                        invalidated_reason=item.invalidated_reason,
                        review_ready=review_ready,
                        block_reason=block_reason,
                        created_at=item.created_at,
                    )
                )
            return PaginatedData(
                items=items,
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def signed_url(
        self,
        *,
        admin_id: int,
            job_id: int,
    ) -> H3cSignedUrlResponse:
        async with get_db_ctx() as db:
            job = await db.get(H3cExportJob, job_id)
            if job is None:
                raise NotFoundException("H3C 导出任务")
            if job.status != "succeeded" or not job.storage_key:
                raise ConflictException("H3C 导出产物尚未生成或已过期")
            if job.expires_at is not None and job.expires_at <= datetime.now(timezone.utc):
                raise ConflictException("H3C 导出产物已过期")
            url = await self.storage.signed_get_url(job.storage_key)
            return H3cSignedUrlResponse(
                url=url,
                expires_in=settings.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS,
            )

    async def process_next_job(self) -> bool:
        async with get_db_ctx() as db:
            await db.execute(
                sa_update(H3cExportJob)
                .where(
                    H3cExportJob.status == "running",
                    H3cExportJob.lease_expires_at < datetime.now(timezone.utc),
                )
                .values(
                    status="queued",
                    last_error="worker lease expired; job recovered",
                )
            )
            await db.commit()
            job = await db.scalar(
                select(H3cExportJob)
                .where(H3cExportJob.status == "queued")
                .order_by(H3cExportJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return False
            job_id = job.id
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.heartbeat_at = job.started_at
            job.lease_expires_at = job.started_at + timedelta(minutes=10)
            await db.commit()
        try:
            await self._run_job(job_id)
            return True
        except Exception as exc:
            logger.error(
                "H3C export job failed: job_id=%s exception_type=%s",
                job_id,
                type(exc).__name__,
            )
            await self._mark_failed(job_id, exc)
            return True

    async def _run_job(self, job_id: int) -> None:
        async with get_db_ctx() as db:
            job = await db.get(H3cExportJob, job_id)
            if job is None:
                raise NotFoundException("H3C 导出任务")
            batch = await db.get(H3cExamBatch, job.batch_id)
            if batch is None:
                raise NotFoundException("H3C 考试批次")
            plan = await db.get(Plan, batch.plan_id)
            export_rows = (
                await db.execute(
                    select(H3cRegistration, H3cExportItem)
                    .join(
                        H3cExportItem,
                        H3cExportItem.registration_id == H3cRegistration.id,
                    )
                    .join(
                        H3cRegistrationVersion,
                        H3cRegistrationVersion.id
                        == H3cExportItem.registration_version_id,
                    )
                    .where(
                        H3cExportItem.job_id == job.id,
                        H3cExportItem.is_valid.is_(True),
                        H3cRegistrationVersion.is_current.is_(True),
                    )
                    .order_by(H3cRegistration.id)
                )
            ).all()
            if not export_rows:
                raise ConflictException("H3C 导出清单中没有有效报名版本")
            all_material_ids = [
                int(value["material_id"])
                for _registration, item in export_rows
                for value in (item.material_versions or {}).values()
                if isinstance(value, dict) and value.get("material_id")
            ]
            material_rows: list[H3cMaterial] = []
            if all_material_ids:
                material_rows = list(
                    (
                        await db.execute(
                            select(H3cMaterial)
                            .where(H3cMaterial.id.in_(all_material_ids))
                            .order_by(H3cMaterial.registration_id, H3cMaterial.id)
                        )
                    ).scalars().all()
                )
            # Legacy jobs created before material snapshots were frozen have an
            # empty mapping. Keep them downloadable by falling back to materials
            # that were current when the older worker implementation ran.
            legacy_registration_ids = [
                registration.id
                for registration, item in export_rows
                if not (item.material_versions or {})
                and REQUIRED_MATERIAL_TYPES[registration.registration_type]
            ]
            if legacy_registration_ids:
                legacy_rows = (
                    await db.execute(
                        select(H3cMaterial)
                        .where(
                            H3cMaterial.registration_id.in_(legacy_registration_ids),
                            H3cMaterial.is_current.is_(True),
                        )
                        .order_by(H3cMaterial.registration_id, H3cMaterial.id)
                    )
                ).scalars().all()
                existing_ids = {row.id for row in material_rows}
                material_rows.extend(row for row in legacy_rows if row.id not in existing_ids)
            materials_by_registration: dict[int, dict[str, H3cMaterial]] = defaultdict(dict)
            for material in material_rows:
                materials_by_registration[material.registration_id][material.material_type] = material
            rows = [
                _ExportRow(
                    registration=registration,
                    materials=tuple(materials_by_registration.get(registration.id, {}).values()),
                    item=item,
                )
                for registration, item in export_rows
            ]
            storage_key = f"h3c/exports/{batch.id}/{job.id}/{uuid.uuid4().hex}"
            if job.artifact_type == "embedded_xlsx":
                extension = "xlsx"
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif job.artifact_type == "full_package":
                extension = "zip"
                content_type = "application/zip"
            else:
                extension = "zip"
                content_type = "application/zip"
            storage_key += f".{extension}"

        with tempfile.TemporaryDirectory(prefix="wemini-h3c-export-") as temp:
            directory = Path(temp)
            if job.artifact_type == "embedded_xlsx":
                artifact = await self._build_workbook(
                    batch=batch,
                    registration_type=job.registration_type,
                    rows=rows,
                    destination=directory,
                )
            elif job.artifact_type == "full_package":
                artifact = await self._build_full_package(
                    batch=batch,
                    batch_name=plan.name if plan is not None else None,
                    job_id=job.id,
                    rows=rows,
                    destination=directory,
                )
            else:
                artifact = await self._build_zip(
                    rows=rows,
                    destination=directory,
                )
            await self.storage.upload_file(storage_key, artifact, content_type)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            size = artifact.stat().st_size

        async with get_db_ctx() as db:
            current = await db.scalar(
                select(H3cExportJob).where(H3cExportJob.id == job_id).with_for_update()
            )
            if current is None or current.status != "running":
                raise ConflictException("H3C 导出任务状态已变化")
            now = datetime.now(timezone.utc)
            current.status = "succeeded"
            current.finished_at = now
            current.storage_key = storage_key
            current.artifact_sha256 = digest
            current.artifact_bytes = size
            current.expires_at = now + timedelta(days=EXPORT_RETENTION_DAYS)
            current.last_error = None
            await db.commit()

    async def _build_workbook(
        self,
        *,
        batch: H3cExamBatch,
        registration_type: str,
        rows: list[_ExportRow],
        destination: Path,
    ) -> Path:
        try:
            from openpyxl import load_workbook
            from openpyxl.drawing.image import Image as XlsxImage
            from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
            from openpyxl.utils import get_column_letter
            from PIL import Image as PillowImage
        except ImportError as exc:
            raise ThirdPartyException("H3C Excel 导出依赖未安装") from exc

        template = Path(__file__).resolve().parents[1] / "templates" / "h3c" / f"{registration_type}.xlsx"
        if not template.is_file() or hashlib.sha256(template.read_bytes()).hexdigest() != H3C_TEMPLATE_SHA256[registration_type]:
            raise ThirdPartyException("H3C 官方模板缺失或已损坏")
        output = destination / f"h3c-{registration_type}-{uuid.uuid4().hex}.xlsx"
        workbook = load_workbook(template)
        worksheet = workbook["模板"]
        sample_row_styles = [copy(cell._style) for cell in worksheet[3]]
        if worksheet.max_row >= 3:
            worksheet.delete_rows(3, worksheet.max_row - 2)
        # openpyxl's delete_rows removes cell values, but does not remove
        # drawings anchored to those rows. The official templates ship with
        # sample material images, so clear them before adding real images.
        worksheet._images = []

        material_dir = destination / "materials"
        material_dir.mkdir(parents=True, exist_ok=True)
        date_columns = H3C_DATE_COLUMNS[registration_type]
        datetime_columns = H3C_DATETIME_COLUMNS[registration_type]
        content_widths: dict[int, float] = {}
        for index, row in enumerate(rows):
            excel_row = index + 3
            frozen_item = getattr(row, "item", None)
            snapshot = (
                frozen_item.candidate_snapshot
                if frozen_item is not None and frozen_item.candidate_snapshot is not None
                else row.registration.candidate_snapshot
            )
            values = self._workbook_row(
                batch=batch,
                registration=row.registration,
                registration_type=registration_type,
                snapshot=snapshot,
            )
            for column, value in enumerate(values, 1):
                if column in date_columns:
                    value = self._excel_date(value)
                elif column in datetime_columns:
                    value = self._excel_datetime(value)
                worksheet.cell(row=excel_row, column=column, value=value)
                cell = worksheet.cell(row=excel_row, column=column)
                if column <= len(sample_row_styles):
                    cell._style = copy(sample_row_styles[column - 1])
                if column in date_columns:
                    cell.number_format = "yyyy/m/d"
                elif column in datetime_columns:
                    cell.number_format = "yyyy/m/d h:mm"
                content_widths[column] = max(
                    content_widths.get(column, 0),
                    self._excel_value_width(cell.value) + 2,
                )
            worksheet.row_dimensions[excel_row].height = 80
            if registration_type in {"coupon", "student"}:
                material = next(
                    (
                        item
                        for item in row.materials
                        if item.material_type
                        == ("coupon_proof" if registration_type == "coupon" else "student_proof")
                    ),
                    None,
                )
                if material is not None:
                    material_path = material_dir / f"{row.registration.id}-{material.id}.jpg"
                    await self.storage.download_file(material.storage_key, material_path)
                    with PillowImage.open(material_path) as image:
                        width, height = image.size
                    picture = XlsxImage(str(material_path))
                    material_column_width = worksheet.column_dimensions["L"].width or 8.43
                    material_cell_width_px = self._excel_column_width_pixels(
                        material_column_width
                    )
                    worksheet.row_dimensions[excel_row].height = self._material_row_height(
                        image_width=width,
                        image_height=height,
                        cell_width_px=material_cell_width_px,
                    )
                    picture.anchor = TwoCellAnchor(
                        _from=AnchorMarker(col=11, row=excel_row - 1),
                        to=AnchorMarker(col=12, row=excel_row),
                    )
                    worksheet.add_image(picture)
        for column, content_width in content_widths.items():
            letter = get_column_letter(column)
            template_width = worksheet.column_dimensions[letter].width or 8.43
            worksheet.column_dimensions[letter].width = min(
                255,
                max(template_width, content_width),
            )
        workbook.save(output)
        return output

    @staticmethod
    def _excel_datetime(value: object | None) -> object | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, datetime.min.time())
        elif isinstance(value, str):
            text = value.strip()
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
                for date_format in (
                    "%Y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                ):
                    try:
                        parsed = datetime.strptime(text, date_format)
                        break
                    except ValueError:
                        continue
            if parsed is None:
                return value
        else:
            return value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(H3C_EXPORT_TZ).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _excel_date(value: object | None) -> object | None:
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(H3C_EXPORT_TZ)
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            text = value.strip()
            try:
                return date.fromisoformat(text[:10].replace("/", "-"))
            except ValueError:
                return value
        return value

    @staticmethod
    def _excel_value_width(value: object | None) -> float:
        if value is None:
            return 0
        if isinstance(value, datetime):
            return 16
        if isinstance(value, date):
            return 10
        return max(
            (
                sum(
                    2
                    if unicodedata.east_asian_width(character) in {"W", "F"}
                    else 1
                    for character in line
                )
                for line in str(value).splitlines()
            ),
            default=0,
        )

    @staticmethod
    def _excel_column_width_pixels(width: float) -> int:
        # ECMA-376 conversion with Calibri 11's 7-pixel maximum digit width.
        return int(((256 * width + 128 // 7) / 256) * 7)

    @staticmethod
    def _material_row_height(
        *,
        image_width: int,
        image_height: int,
        cell_width_px: int,
    ) -> float:
        if image_width <= 0 or image_height <= 0 or cell_width_px <= 0:
            return 80
        required_px = cell_width_px * image_height / image_width
        required_points = required_px * 72 / 96
        return min(409.5, max(80, required_points))

    @staticmethod
    def _workbook_row(
        *,
        batch: H3cExamBatch,
        registration: H3cRegistration,
        registration_type: str,
        snapshot: dict | None = None,
    ) -> list[object | None]:
        snapshot = snapshot if snapshot is not None else registration.candidate_snapshot
        common = [
            snapshot.get("candidate_name"),
            snapshot.get("gender"),
            snapshot.get("candidate_idcard"),
            snapshot.get("school"),
            snapshot.get("address"),
            snapshot.get("phone"),
            snapshot.get("email"),
            batch.country,
            batch.language,
            snapshot.get("education"),
        ]
        if registration_type == "coupon":
            return [
                snapshot.get("coupon_code"),
                *common,
                None,  # image placeholder
                None,
                snapshot.get("birth_date"),
                batch.identity_tag,
                snapshot.get("first_name_en"),
                snapshot.get("last_name_en"),
                snapshot.get("exam_datetime"),
                batch.exam_code,
                None,
                batch.training_org,
                batch.training_address,
                batch.training_start,
                batch.training_end,
            ]
        if registration_type == "full":
            return [
                batch.exam_code,
                *common,
                snapshot.get("birth_date"),
                batch.identity_tag,
                snapshot.get("first_name_en"),
                snapshot.get("last_name_en"),
                snapshot.get("exam_datetime"),
                None,
                batch.training_org,
                batch.training_teacher,
                batch.training_start,
                batch.training_end,
            ]
        return [
            batch.exam_code,
            *common,
            None,  # image placeholder
            snapshot.get("verify_code"),
            snapshot.get("birth_date"),
            snapshot.get("first_name_en"),
            snapshot.get("last_name_en"),
            snapshot.get("exam_datetime"),
            None,
            batch.training_org,
            batch.training_teacher,
            batch.training_start,
            batch.training_end,
        ]

    async def _build_zip(
        self,
        *,
        rows: list[_ExportRow],
        destination: Path,
    ) -> Path:
        output = destination / f"h3c-images-{uuid.uuid4().hex}.zip"
        material_dir = destination / "zip-materials"
        material_dir.mkdir(parents=True)
        used_names: set[str] = set()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for row in rows:
                for material in row.materials:
                    slug = MATERIAL_SLUGS[material.material_type]
                    name = f"{row.registration.registration_no}-{slug}.jpg"
                    candidate = name
                    serial = 2
                    while candidate in used_names:
                        candidate = f"{name[:-4]}-{serial}.jpg"
                        serial += 1
                    used_names.add(candidate)
                    material_path = material_dir / candidate
                    await self.storage.download_file(material.storage_key, material_path)
                    archive.write(material_path, arcname=candidate)
        return output

    @staticmethod
    def _safe_zip_component(value: object, *, fallback: str) -> str:
        normalized = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", str(value or "")).strip().strip(".")
        normalized = re.sub(r"\s+", " ", normalized)
        return (normalized[:120] if normalized else fallback)

    async def _build_full_package(
        self,
        *,
        batch: H3cExamBatch,
        batch_name: str | None = None,
        job_id: int,
        rows: list[_ExportRow],
        destination: Path,
    ) -> Path:
        try:
            from openpyxl import Workbook, load_workbook
        except ImportError as exc:
            raise ThirdPartyException("H3C 完整资料包依赖未安装") from exc

        workdir = destination / f"full-package-{job_id}"
        workbook_dir = workdir / "workbooks"
        attachment_staging = workdir / "attachments"
        workbook_dir.mkdir(parents=True)
        attachment_staging.mkdir()

        grouped: dict[str, list[_ExportRow]] = defaultdict(list)
        for row in rows:
            grouped[row.registration.registration_type].append(row)

        workbook_paths: dict[str, Path] = {}
        for registration_type, type_rows in grouped.items():
            path = await self._build_workbook(
                batch=batch,
                registration_type=registration_type,
                rows=type_rows,
                destination=workbook_dir,
            )
            renamed = workbook_dir / TYPE_FILE_NAMES[registration_type]
            if renamed.exists():
                raise ConflictException("H3C 完整资料包内文件名冲突")
            path.rename(renamed)
            workbook_paths[registration_type] = renamed
            worksheet = load_workbook(renamed, read_only=True)["模板"]
            generated_rows = max(0, worksheet.max_row - 2)
            if generated_rows != len(type_rows):
                raise ConflictException("H3C 报名表人数与导出清单不一致")

        records: list[dict[str, object]] = []
        attachment_files: list[tuple[Path, str]] = []
        exported_at = datetime.now(H3C_EXPORT_TZ).replace(microsecond=0, tzinfo=None)
        display_batch_name = (
            batch_name
            or getattr(batch, "name", None)
            or f"H3C批次-{batch.id}"
        )
        for registration_type in ("coupon", "student", "full"):
            for row in grouped.get(registration_type, []):
                registration = row.registration
                snapshot = (
                    row.item.candidate_snapshot
                    if row.item is not None and row.item.candidate_snapshot is not None
                    else registration.candidate_snapshot
                ) or {}
                registration_no = self._safe_zip_component(
                    registration.registration_no, fallback=f"registration-{registration.id}"
                )
                name = self._safe_zip_component(
                    snapshot.get("candidate_name"), fallback=f"candidate-{registration.id}"
                )
                person_dir = self._safe_zip_component(
                    f"{registration_no}_{name}", fallback=f"person-{registration.id}"
                )
                required_type = REQUIRED_MATERIAL_TYPES[registration_type]
                material = next(
                    (item for item in row.materials if item.material_type == required_type),
                    None,
                ) if required_type else None
                attachment_dir: str | None = None
                attachment_count = 0
                if material is not None:
                    extension = self._safe_zip_component(
                        Path(material.original_filename or "").suffix, fallback=".jpg"
                    )
                    if not extension.startswith("."):
                        extension = f".{extension}"
                    file_name = f"{'考券证明' if registration_type == 'coupon' else '学生证明'}{extension}"
                    local_path = attachment_staging / f"{material.id}-{uuid.uuid4().hex}"
                    await self.storage.download_file(material.storage_key, local_path)
                    arcname = f"附件/{person_dir}/{file_name}"
                    attachment_files.append((local_path, arcname))
                    attachment_dir = f"附件/{person_dir}"
                    attachment_count = 1
                records.append(
                    {
                        "registration_no": registration.registration_no,
                        "name": snapshot.get("candidate_name", ""),
                        "registration_type": registration_type,
                        "status": registration.status,
                        "version_no": row.item.registration_version_id if row.item else None,
                        "table_path": f"报名表/{TYPE_FILE_NAMES[registration_type]}",
                        "attachment_dir": attachment_dir or "-",
                        "attachment_count": attachment_count,
                        "result": "成功",
                        "failure_reason": "-",
                    }
                )

        manifest_path = workbook_dir / "导出清单.xlsx"
        manifest_workbook = Workbook()
        manifest_sheet = manifest_workbook.active
        manifest_sheet.title = "导出清单"
        headers = [
            "序号", "姓名", "报名编号", "报名类型", "导出时状态", "报名版本ID",
            "报名表位置", "附件目录", "附件数量", "导出结果", "失败原因",
            "批次名称", "任务编号", "导出时间",
        ]
        manifest_sheet.append(headers)
        for column in range(1, len(headers) + 1):
            manifest_sheet.column_dimensions[
                manifest_sheet.cell(row=1, column=column).coordinate[:1]
            ].width = 18
        status_labels = {
            "coupon": "考券报名", "student": "学生报名", "full": "全额报名",
        }
        for index, record in enumerate(records, 1):
            manifest_sheet.append([
                index,
                record["name"],
                record["registration_no"],
                status_labels[str(record["registration_type"])],
                "终审通过" if record["status"] == "final_approved" else "初审通过",
                record["version_no"],
                record["table_path"],
                record["attachment_dir"],
                record["attachment_count"],
                record["result"],
                record["failure_reason"],
                display_batch_name,
                job_id,
                exported_at,
            ])
        if manifest_sheet.max_row - 1 != len(rows):
            raise ConflictException("H3C 导出清单人数与任务人数不一致")
        manifest_workbook.save(manifest_path)

        output = destination / f"h3c-full-package-{job_id}-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            archive.write(manifest_path, arcname="导出清单.xlsx")
            for registration_type, path in workbook_paths.items():
                archive.write(path, arcname=f"报名表/{TYPE_FILE_NAMES[registration_type]}")
            for path, arcname in attachment_files:
                archive.write(path, arcname=arcname)

        expected_rows = sum(len(items) for items in grouped.values())
        if expected_rows != len(rows) or len(records) != len(rows):
            raise ConflictException("H3C 完整资料包人员数量不一致")
        expected_attachment_people = sum(
            1
            for row in rows
            if REQUIRED_MATERIAL_TYPES[row.registration.registration_type]
        )
        if len(attachment_files) != expected_attachment_people:
            raise ConflictException("H3C 完整资料包附件人数不一致")
        return output

    async def expire_artifacts(self) -> int:
        now = datetime.now(timezone.utc)
        expired_count = 0
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(H3cExportJob)
                    .where(
                        H3cExportJob.status == "succeeded",
                        H3cExportJob.expires_at.is_not(None),
                        H3cExportJob.expires_at <= now,
                        H3cExportJob.storage_key.is_not(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for job in rows:
                if job.storage_key:
                    await self.storage.delete(job.storage_key)
                job.storage_key = None
                job.artifact_sha256 = None
                job.artifact_bytes = None
                job.expires_at = None
                expired_count += 1
            await db.commit()
        return expired_count

    async def _mark_failed(self, job_id: int, exc: Exception) -> None:
        async with get_db_ctx() as db:
            job = await db.scalar(
                select(H3cExportJob).where(H3cExportJob.id == job_id).with_for_update()
            )
            if job is None:
                return
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            await db.commit()

    @staticmethod
    def _response(
        job: H3cExportJob,
        *,
        requested_by_admin_name: str | None = None,
        stats: dict | None = None,
    ) -> H3cExportJobResponse:
        response = H3cExportJobResponse.model_validate(job)
        response.requested_by_admin_name = requested_by_admin_name
        if stats is not None:
            response.valid_item_count = int(stats.get("valid_item_count", 0))
            response.counts_by_type = dict(stats.get("counts_by_type", {}))
            response.registration_ids = list(stats.get("registration_ids", []))
        return response


async def h3c_export_worker_loop(service: H3cExportService | None = None) -> None:
    active_service = service or H3cExportService()
    while True:
        try:
            processed = await active_service.process_next_job()
            if not processed:
                await active_service.expire_artifacts()
        except Exception as exc:
            logger.warning(
                "H3C export worker iteration failed: exception_type=%s",
                type(exc).__name__,
            )
        await asyncio.sleep(5)
