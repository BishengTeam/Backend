"""NISP async export service with job queue, OSS storage, and signed URLs."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispExportJob, NispRegistration
from app.integrations.nisp_storage import NispObjectStorage
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.nisp import NispExportJobResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NISP_LEVEL1_HEADERS = [
    "序号", "姓名", "拼音", "专业", "学校名称/单位名称",
    "身份证号(18位)", "手机号码", "邮箱", "报考省份", "培训种类",
    "身份证双面", "寸照",
]

NISP_LEVEL2_HEADERS = [
    "序号", "培训机构", "姓名", "拼音", "学校名称",
    "身份证号(18位)", "考试类型", "性别", "年龄", "最高学历",
    "专业", "移动电话", "邮箱", "省份", "地址", "邮编",
    "身份证双面照片", "寸照", "学籍报告", "NISP二级考试报名申请表",
]

HEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
HEADER_FONT = Font(name="微软雅黑", size=10, bold=True)
BODY_FONT = Font(name="微软雅黑", size=10)
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

EXPORT_TTL_HOURS = 72
WORKER_POLL_SECONDS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NispExportService:

    def __init__(self, storage: NispObjectStorage | None = None) -> None:
        self.storage = storage or NispObjectStorage()

    async def create_job(
        self,
        *,
        admin_id: int,
        batch_id: int,
        level: str,
        include_statuses: list[str] | None = None,
    ) -> NispExportJobResponse:
        include_statuses = include_statuses or ["approved"]
        async with get_db_ctx() as db:
            count = await db.scalar(
                select(func.count()).select_from(NispRegistration).where(
                    NispRegistration.batch_id == batch_id,
                    NispRegistration.level == level,
                    NispRegistration.status.in_(include_statuses),
                )
            ) or 0
            if count == 0:
                raise BusinessException("没有符合条件的报名记录")

            job = NispExportJob(
                batch_id=batch_id,
                level=level,
                requested_by_admin_id=admin_id,
                include_statuses=include_statuses,
                status="queued",
                registration_count=count,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return NispExportJobResponse.model_validate(job)

    async def list_jobs(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[NispExportJobResponse], int]:
        async with get_db_ctx() as db:
            total = await db.scalar(
                select(func.count()).select_from(NispExportJob)
            ) or 0
            rows = (
                await db.execute(
                    select(NispExportJob)
                    .order_by(NispExportJob.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [NispExportJobResponse.model_validate(r) for r in rows], total

    async def get_job(self, job_id: int) -> NispExportJobResponse:
        async with get_db_ctx() as db:
            job = await db.get(NispExportJob, job_id)
            if job is None:
                raise NotFoundException("NISP 导出任务")
            return NispExportJobResponse.model_validate(job)

    async def signed_url(self, job_id: int) -> str:
        async with get_db_ctx() as db:
            job = await db.get(NispExportJob, job_id)
            if job is None:
                raise NotFoundException("NISP 导出任务")
            if job.status != "succeeded" or not job.storage_key:
                raise BusinessException("导出任务尚未完成")
            if job.expires_at and job.expires_at < _now():
                raise BusinessException("导出文件已过期，请重新导出")
            return await self.storage.signed_get_url(job.storage_key)

    async def process_next_job(self) -> bool:
        """Pick up and process one queued job. Returns True if a job was processed."""
        async with get_db_ctx() as db:
            job = (
                await db.execute(
                    select(NispExportJob)
                    .where(NispExportJob.status == "queued")
                    .order_by(NispExportJob.id.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return False

            job.status = "running"
            job.started_at = _now()
            job.heartbeat_at = _now()
            await db.commit()

            try:
                await self._run_job(job.id)
            except Exception as exc:
                async with get_db_ctx() as db2:
                    failed = await db2.get(NispExportJob, job.id)
                    if failed is not None:
                        failed.status = "failed"
                        failed.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                        failed.finished_at = _now()
                        await db2.commit()
            return True

    async def _run_job(self, job_id: int) -> None:
        async with get_db_ctx() as db:
            job = await db.get(NispExportJob, job_id)
            if job is None:
                return
            registrations = (
                await db.execute(
                    select(NispRegistration)
                    .where(
                        NispRegistration.batch_id == job.batch_id,
                        NispRegistration.level == job.level,
                        NispRegistration.status.in_(job.include_statuses),
                    )
                    .order_by(NispRegistration.id.asc())
                )
            ).scalars().all()

            # Build Excel
            excel_bytes = self._build_excel(list(registrations), level=job.level)

            # Upload to storage
            storage_key = f"nisp/exports/{job.id}/{uuid.uuid4().hex}.xlsx"
            await self.storage.save_export(
                storage_key=storage_key,
                data=excel_bytes,
            )

            job.status = "succeeded"
            job.finished_at = _now()
            job.storage_key = storage_key
            job.artifact_sha256 = hashlib.sha256(excel_bytes).hexdigest()
            job.artifact_bytes = len(excel_bytes)
            job.expires_at = _now() + timedelta(hours=EXPORT_TTL_HOURS)
            await db.commit()

    async def expire_artifacts(self) -> int:
        """Mark expired succeeded jobs as failed and delete their artifacts."""
        now = _now()
        async with get_db_ctx() as db:
            jobs = (
                await db.execute(
                    select(NispExportJob)
                    .where(
                        NispExportJob.status == "succeeded",
                        NispExportJob.expires_at < now,
                    )
                )
            ).scalars().all()
            for job in jobs:
                if job.storage_key:
                    try:
                        await self.storage.delete_object(job.storage_key)
                    except Exception:
                        pass
                job.status = "failed"
                job.last_error = "expired"
                job.storage_key = None
            await db.commit()
            return len(jobs)

    def _build_excel(
        self,
        registrations: list[NispRegistration],
        *,
        level: str,
    ) -> bytes:
        import io

        wb = Workbook()
        if level == "1":
            ws = wb.active
            ws.title = "NISP一级报名表"
            self._write_level1(ws, registrations)
        else:
            ws = wb.active
            ws.title = "NISP二级报名表"
            self._write_level2(ws, registrations)

        headers = NISP_LEVEL1_HEADERS if level == "1" else NISP_LEVEL2_HEADERS
        for col_idx in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 16

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    def _write_level1(self, ws, registrations: list[NispRegistration]) -> None:
        ws.append(NISP_LEVEL1_HEADERS)
        self._style_header(ws, len(NISP_LEVEL1_HEADERS))
        for idx, reg in enumerate(registrations, 1):
            s = reg.candidate_snapshot
            row = [
                idx, s.get("name", ""), s.get("pinyin", ""), s.get("major", ""),
                s.get("school", ""), s.get("id_card", reg.candidate_idcard),
                s.get("phone", ""), s.get("email", ""), s.get("province", ""),
                s.get("training_type", "NISP一级"),
                "已上传" if self._has_material(reg, "id_card_both_sides") else "未上传",
                "已上传" if self._has_material(reg, "portrait_photo") else "未上传",
            ]
            ws.append(row)
            self._style_row(ws, ws.max_row, len(NISP_LEVEL1_HEADERS))

    def _write_level2(self, ws, registrations: list[NispRegistration]) -> None:
        ws.append(NISP_LEVEL2_HEADERS)
        self._style_header(ws, len(NISP_LEVEL2_HEADERS))
        for idx, reg in enumerate(registrations, 1):
            s = reg.candidate_snapshot
            row = [
                idx, s.get("institution", ""), s.get("name", ""), s.get("pinyin", ""),
                s.get("school", ""), s.get("id_card", reg.candidate_idcard),
                s.get("training_type", "NISP二级"), s.get("gender", ""),
                s.get("age", ""), s.get("education", ""), s.get("major", ""),
                s.get("phone", ""), s.get("email", ""), s.get("province", ""),
                s.get("address", ""), s.get("zip_code", ""),
                "已上传" if self._has_material(reg, "id_card_both_sides") else "未上传",
                "已上传" if self._has_material(reg, "portrait_photo") else "未上传",
                "已上传" if self._has_material(reg, "xuexin_report") else "未上传",
                "已上传" if self._has_material(reg, "application_form") else "未上传",
            ]
            ws.append(row)
            self._style_row(ws, ws.max_row, len(NISP_LEVEL2_HEADERS))

    @staticmethod
    def _has_material(reg: NispRegistration, material_type: str) -> bool:
        keys = reg.material_keys or {}
        return bool(keys.get(material_type))

    @staticmethod
    def _style_header(ws, col_count: int) -> None:
        for col in range(1, col_count + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN
        ws.row_dimensions[1].height = 28

    @staticmethod
    def _style_row(ws, row: int, col_count: int) -> None:
        for col in range(1, col_count + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN


async def nisp_export_worker_loop(service: NispExportService | None = None) -> None:
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    active_service = service or NispExportService()
    while True:
        try:
            processed = await active_service.process_next_job()
            if not processed:
                await active_service.expire_artifacts()
        except Exception:
            logger.warning("NISP export worker iteration failed", exc_info=True)
        await asyncio.sleep(WORKER_POLL_SECONDS)
