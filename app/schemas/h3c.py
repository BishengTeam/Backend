"""Compatibility exports for the frozen H3C registration schemas."""

from app.schemas.h3c_registration import (
    H3cMaterialUploadResponse,
    H3cOrderCreate,
    H3cProfileDefaults,
)

__all__ = [
    "H3cMaterialUploadResponse",
    "H3cOrderCreate",
    "H3cProfileDefaults",
]
