from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


RENSHE_PRODUCT_CODE = "RS-ZY"
RENSHE_ORDER_EXPIRE_MINUTES = 60
MAX_EXPORT_VOLUME_BYTES = 10 * 1024 * 1024 * 1024
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

EDUCATION_LEVELS = {
    "secondary_vocational",
    "associate",
    "bachelor",
    "master",
    "doctorate",
}

APPLICATION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_payment"},
    "pending_payment": {"draft", "pending_initial_review", "closed"},
    "pending_initial_review": {"initial_rejected", "pending_external_review", "closed"},
    "initial_rejected": {"pending_initial_review", "closed"},
    "pending_external_review": {"external_rejected", "external_approved", "closed"},
    "external_rejected": {"pending_initial_review", "closed"},
    "external_approved": {"closed"},
    "closed": set(),
}

PROFILE_LOCKING_APPLICATION_STATUSES = {
    "pending_payment",
    "pending_initial_review",
    "pending_external_review",
}

EXPORTABLE_APPLICATION_STATUSES = {
    "pending_external_review",
    "external_approved",
}


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    extensions: frozenset[str]
    content_types: frozenset[str]
    max_bytes: int
    magic: tuple[bytes, ...]


JPEG_SPEC = MaterialSpec(
    extensions=frozenset({".jpg", ".jpeg"}),
    content_types=frozenset({"image/jpeg", "image/pjpeg"}),
    max_bytes=10 * 1024 * 1024,
    magic=(b"\xff\xd8\xff",),
)
PDF_SPEC = MaterialSpec(
    extensions=frozenset({".pdf"}),
    content_types=frozenset({"application/pdf"}),
    max_bytes=20 * 1024 * 1024,
    magic=(b"%PDF-",),
)

MATERIAL_SPECS: dict[str, MaterialSpec] = {
    "id_card_front": JPEG_SPEC,
    "id_card_back": JPEG_SPEC,
    "portrait": JPEG_SPEC,
    "student_card": JPEG_SPEC,
    "xuexin_registration": PDF_SPEC,
    "education_proof": JPEG_SPEC,
}


def assert_application_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in APPLICATION_TRANSITIONS.get(current, set()):
        raise ValueError(f"人社报名状态不允许从 {current} 变更为 {target}")


def add_business_days(start: datetime, days: int) -> datetime:
    """Add China-local weekdays while preserving the caller's timezone.

    Statutory holiday configuration is intentionally out of scope for the first
    release. Naive inputs are interpreted and returned as naive Asia/Shanghai
    wall-clock values.
    """
    if days < 0:
        raise ValueError("days must be non-negative")
    was_naive = start.tzinfo is None
    original_tz = start.tzinfo
    result = (
        start.replace(tzinfo=BUSINESS_TIMEZONE)
        if was_naive
        else start.astimezone(BUSINESS_TIMEZONE)
    )
    added = 0
    while added < days:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    if was_naive:
        return result.replace(tzinfo=None)
    return result.astimezone(original_tz)


def validate_material(
    *,
    kind: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    header: bytes,
) -> str:
    """Validate metadata and magic bytes, returning the normalized extension."""
    spec = MATERIAL_SPECS.get(kind)
    if spec is None:
        raise ValueError("不支持的材料类型")
    extension = Path(filename).suffix.lower()
    if extension not in spec.extensions:
        raise ValueError("材料文件扩展名不符合要求")
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type not in spec.content_types:
        raise ValueError("材料 MIME 类型不符合要求")
    if size_bytes <= 0:
        raise ValueError("材料文件不能为空")
    if size_bytes > spec.max_bytes:
        raise ValueError("材料文件超过大小限制")
    if not any(header.startswith(prefix) for prefix in spec.magic):
        raise ValueError("材料文件头不符合声明类型")
    return extension
