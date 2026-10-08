"""Seed the NISP operational PDF documents into OSS and document management."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401  # Register every ORM table referenced by FKs.
from app.adapter.database import async_session_factory
from app.domain.content.src.index import DocumentResource
from app.integrations.document_storage import DocumentObjectStorage


DOCUMENTS = [
    {
        "document_key": "nisp.education_report_guide",
        "scene": "nisp_education_report_guide",
        "title": "《学历证书电子注册备案表》查询步骤",
        "entry_text": "查看《学历证书电子注册备案表》查询步骤PDF",
        "entry_mode": "required",
        "description": "NISP二级学籍验证报告查询教程",
        "filename": "《学历证书电子注册备案表》查询步骤.pdf",
    },
    {
        "document_key": "nisp.level2_application_form",
        "scene": "nisp_level2_application_form",
        "title": "NISP二级考试报名申请表",
        "entry_text": "下载《NISP二级考试报名申请表》PDF",
        "entry_mode": "required",
        "description": "NISP二级认证报名申请表模板",
        "filename": "NISP二级考试报名申请表.pdf",
    },
]


async def seed(docs_dir: Path) -> list[str]:
    storage = DocumentObjectStorage()
    results: list[str] = []
    async with async_session_factory() as db:
        for template in DOCUMENTS:
            pdf_path = docs_dir / template["filename"]
            if not pdf_path.is_file():
                raise SystemExit(f"PDF does not exist: {pdf_path}")
            data = pdf_path.read_bytes()
            storage_key, size_bytes, sha256 = await storage.save(
                document_key=template["document_key"],
                filename=template["filename"],
                content_type="application/pdf",
                data=data,
            )
            document = await db.scalar(
                select(DocumentResource).where(
                    DocumentResource.document_key == template["document_key"]
                )
            )
            if document is None:
                db.add(
                    DocumentResource(
                        document_key=template["document_key"],
                        scene=template["scene"],
                        title=template["title"],
                        entry_text=template["entry_text"],
                        entry_mode=template["entry_mode"],
                        description=template["description"],
                        storage_key=storage_key,
                        original_filename=template["filename"],
                        content_type="application/pdf",
                        size_bytes=size_bytes,
                        sha256=sha256,
                        version_no=1,
                        is_active=True,
                    )
                )
                results.append(f"{template['document_key']}:created")
                continue

            unchanged = (
                document.sha256 == sha256
                and document.storage_key == storage_key
                and document.scene == template["scene"]
                and document.title == template["title"]
                and document.is_active
            )
            if unchanged:
                results.append(f"{template['document_key']}:unchanged")
                continue

            document.scene = template["scene"]
            document.title = template["title"]
            document.entry_text = document.entry_text or template["entry_text"]
            document.entry_mode = template["entry_mode"]
            document.description = document.description or template["description"]
            document.storage_key = storage_key
            document.original_filename = template["filename"]
            document.size_bytes = size_bytes
            document.sha256 = sha256
            document.version_no += 1
            document.is_active = True
            results.append(f"{template['document_key']}:updated")
        await db.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs" / "nisp",
    )
    args = parser.parse_args()
    for line in asyncio.run(seed(args.docs_dir)):
        print(line)


if __name__ == "__main__":
    main()
