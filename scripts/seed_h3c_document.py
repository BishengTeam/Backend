"""Seed the initial H3C xuexin verification PDF into OSS and the database."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401  # Register every ORM table referenced by FKs.
from app.adapter.database import async_session_factory
from app.domain.content.src.index import DocumentResource
from app.integrations.document_storage import DocumentObjectStorage


DOCUMENT_KEY = "h3c.xuexin_verification_guide"
DEFAULT_PDF = Path(__file__).resolve().parents[1] / "docs/h3c/如何查询学籍在线验证码.pdf"


async def seed(pdf_path: Path) -> str:
    data = pdf_path.read_bytes()
    storage = DocumentObjectStorage()
    storage_key, size_bytes, sha256 = await storage.save(
        document_key=DOCUMENT_KEY,
        filename=pdf_path.name,
        content_type="application/pdf",
        data=data,
    )
    async with async_session_factory() as db:
        document = await db.scalar(
            select(DocumentResource).where(
                DocumentResource.document_key == DOCUMENT_KEY
            )
        )
        if document is None:
            db.add(
                DocumentResource(
                    document_key=DOCUMENT_KEY,
                    scene="h3c_student_xuexin_guide",
                    title="如何查询学籍在线验证码",
                    entry_text="查看《如何查询学籍在线验证码》PDF",
                    entry_mode="required",
                    description="学信网在线验证码获取教程",
                    storage_key=storage_key,
                    original_filename=pdf_path.name,
                    content_type="application/pdf",
                    size_bytes=size_bytes,
                    sha256=sha256,
                    version_no=1,
                    is_active=True,
                )
            )
            await db.commit()
            return "created"
        unchanged = (
            document.sha256 == sha256
            and document.storage_key == storage_key
            and document.title == "如何查询学籍在线验证码"
            and document.scene == "h3c_student_xuexin_guide"
            and document.is_active
        )
        if unchanged:
            return "unchanged"
        document.title = "如何查询学籍在线验证码"
        document.scene = "h3c_student_xuexin_guide"
        document.entry_text = document.entry_text or "查看《如何查询学籍在线验证码》PDF"
        document.entry_mode = "required"
        document.description = document.description or "学信网在线验证码获取教程"
        document.storage_key = storage_key
        document.original_filename = pdf_path.name
        document.size_bytes = size_bytes
        document.sha256 = sha256
        document.version_no += 1
        document.is_active = True
        await db.commit()
        return "updated"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    args = parser.parse_args()
    if not args.pdf.is_file():
        raise SystemExit(f"PDF does not exist: {args.pdf}")
    result = asyncio.run(seed(args.pdf))
    print(f"{DOCUMENT_KEY}: {result}")


if __name__ == "__main__":
    main()
