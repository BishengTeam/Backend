"""管理端 — 认证配套附件（考场须知、指引等）。"""
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import require_any_permission
from app.schemas.common import APIResponse, success
from app.domain.exam.src.index import CertAttachment, ExamSchedule
from app.domain.plan.src.index import Plan
from app.port.exceptions import BusinessException, NotFoundException
from app.policy.vendor_scope import manageable_vendors, require_vendor_permission
from app.services.upload import ALLOWED_EXTENSIONS, UploadService

router = APIRouter(prefix="/cert-attachments", tags=["管理后台-认证附件"])

guard = require_any_permission(
    "h3c:batch_manage", "nisp:batch_manage", "user:write", "content:write"
)
VENDOR_PATTERN = "^(h3c|nisp|renshe|sangfor)$"
CERT_ATTACHMENT_PREFIX = "/api/media/cert-attachments/"
_ATTACHMENT_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

logger = logging.getLogger(__name__)


class AttachmentCreate(BaseModel):
    vendor: str = Field(..., pattern=VENDOR_PATTERN)
    plan_id: int | None = Field(None, ge=1)
    schedule_id: int | None = Field(None, ge=1)
    title: str = Field(..., min_length=1, max_length=128)
    file_url: str = Field(..., min_length=1, max_length=512)
    file_size: int | None = Field(None, ge=0)
    sort_order: int = Field(0, ge=0)

    @field_validator("file_url")
    @classmethod
    def _validate_file_url(cls, value: str) -> str:
        if not value.startswith(CERT_ATTACHMENT_PREFIX):
            raise ValueError("附件必须来自本站附件上传接口")
        name = value.removeprefix(CERT_ATTACHMENT_PREFIX)
        if "/" in name or ".." in name or not _ATTACHMENT_FILENAME.fullmatch(name):
            raise ValueError("附件地址格式不合法")
        extension = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError("附件类型不在允许范围内")
        return value


@router.post("/upload", response_model=APIResponse[dict], summary="上传附件文件")
async def upload_attachment(
    vendor: str = Query(..., pattern=VENDOR_PATTERN),
    file: UploadFile = File(...),
    _admin=Depends(guard),
):
    require_vendor_permission(_admin, vendor)
    result = await UploadService.save_file(file, sub_dir="cert-attachments")
    return success(data=result)


@router.get("", response_model=APIResponse[list], summary="附件列表")
async def list_attachments(
    vendor: Annotated[str | None, Query(pattern=VENDOR_PATTERN)] = None,
    plan_id: Annotated[int | None, Query(ge=1)] = None,
    schedule_id: Annotated[int | None, Query(ge=1)] = None,
    _admin=Depends(guard),
    db: AsyncSession = Depends(get_db),
):
    if vendor:
        require_vendor_permission(_admin, vendor)
    stmt = select(CertAttachment)
    if vendor:
        stmt = stmt.where(CertAttachment.vendor == vendor)
    else:
        stmt = stmt.where(CertAttachment.vendor.in_(manageable_vendors(_admin)))
    if schedule_id:
        stmt = stmt.where(CertAttachment.schedule_id == schedule_id)
    if plan_id:
        stmt = stmt.where(CertAttachment.plan_id == plan_id)
    rows = (await db.execute(
        stmt.order_by(CertAttachment.sort_order, CertAttachment.id)
    )).scalars().all()
    return success(data=[{
        "id": item.id,
        "vendor": item.vendor,
        "plan_id": item.plan_id,
        "schedule_id": item.schedule_id,
        "title": item.title,
        "file_url": item.file_url,
        "file_size": item.file_size,
        "sort_order": item.sort_order,
        "is_active": item.is_active,
    } for item in rows])


@router.post("", response_model=APIResponse[dict], summary="新增附件")
async def create_attachment(
    data: AttachmentCreate,
    _admin=Depends(guard),
    db: AsyncSession = Depends(get_db),
):
    require_vendor_permission(_admin, data.vendor)
    storage_key = data.file_url.removeprefix("/api/media/")
    if not UploadService.file_exists(storage_key):
        raise BusinessException("附件文件不存在，请重新上传")
    file_size = UploadService.get_file_path(storage_key).stat().st_size
    if data.plan_id is not None and data.schedule_id is not None:
        raise BusinessException("附件只能挂在批次或考场计划之一")
    if data.plan_id is not None:
        from app.services.cert_attachment import plan_belongs_to_vendor

        plan = await db.get(Plan, data.plan_id)
        if plan is None:
            raise NotFoundException("批次")
        if not await plan_belongs_to_vendor(db, plan, data.vendor):
            raise BusinessException("附件的厂商与批次不一致")
    if data.schedule_id is not None:
        schedule = await db.get(ExamSchedule, data.schedule_id)
        if schedule is None:
            raise NotFoundException("考场计划")
        if schedule.vendor != data.vendor:
            raise BusinessException("附件的厂商与考场计划不一致")
    item = CertAttachment(
        vendor=data.vendor,
        plan_id=data.plan_id,
        schedule_id=data.schedule_id,
        title=data.title,
        file_url=data.file_url,
        file_size=file_size,
        sort_order=data.sort_order,
    )
    db.add(item)
    await db.commit()
    return success(data={"id": item.id}, message="附件已添加")


@router.delete("/{attachment_id}", response_model=APIResponse[dict], summary="删除附件")
async def delete_attachment(
    attachment_id: int,
    _admin=Depends(guard),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(CertAttachment, attachment_id)
    if item is None:
        raise NotFoundException("附件")
    if item.schedule_id is not None:
        schedule = await db.get(ExamSchedule, item.schedule_id)
        require_vendor_permission(_admin, schedule.vendor if schedule else item.vendor)
    else:
        require_vendor_permission(_admin, item.vendor)
    file_url = item.file_url
    shared_reference = await db.scalar(
        select(CertAttachment.id).where(
            CertAttachment.file_url == file_url,
            CertAttachment.id != item.id,
        ).limit(1)
    )
    await db.delete(item)
    await db.commit()
    # 多附件可能引用同一文件；仅在无其他引用时清理物理文件。
    # 删除失败不阻断请求，孤儿文件由存储清理任务兜底。
    if shared_reference is None:
        try:
            UploadService.delete_file_by_url(file_url)
        except OSError:
            logger.warning(
                "cert attachment file delete failed: url=%s", file_url, exc_info=True
            )
    return success(message="附件已删除")
