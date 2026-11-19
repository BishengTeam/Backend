"""公开材料引用与孤儿文件审计（默认只读，删除需显式开关）。"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import JSON, String, Text, select

from app.adapter.database import Base, get_db_ctx
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheApplicationVersion,
    RensheMaterial,
)
from app.domain.user.src.index import UserRealname, UserStudent
from app.services.upload import ALLOWED_EXTENSIONS, UPLOAD_DIR

PUBLIC_PREFIX = "/api/media/"
PUBLIC_SUBDIRS = ("cert-attachments",)


def _public_value(value) -> str | None:
    return value if isinstance(value, str) and value.startswith(PUBLIC_PREFIX) else None


def _iter_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, str):
        yield value


def _walk_json(value, path: str):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_json(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


async def _pii_findings(db):
    findings: list[tuple[str, str, str]] = []

    for row in (await db.scalars(select(UserRealname))).all():
        for column in ("id_card_front_oss", "id_card_back_oss", "avatar_oss"):
            value = _public_value(getattr(row, column))
            if value:
                findings.append((f"user_realname.user_id={row.user_id}", column, value))
    for row in (await db.scalars(select(UserStudent))).all():
        for column in ("student_card_oss", "enrollment_pdf_oss", "degree_cert_oss"):
            value = _public_value(getattr(row, column))
            if value:
                findings.append((f"user_student.user_id={row.user_id}", column, value))
    for row in (await db.scalars(select(RensheMaterial))).all():
        for column in ("storage_key", "source_storage_key"):
            value = _public_value(getattr(row, column))
            if value:
                findings.append((f"renshe_material.id={row.id}", column, value))

    json_columns = [
        (RensheApplication, "draft_data"),
        (RensheApplicationVersion, "realname_snapshot"),
        (RensheApplicationVersion, "student_snapshot"),
        (RensheApplicationVersion, "form_data"),
    ]
    for model, column in json_columns:
        for row in (await db.scalars(select(model))).all():
            payload = getattr(row, column) or {}
            for json_path, value in _walk_json(payload, f"{model.__tablename__}.{column}"):
                public = _public_value(value)
                if public:
                    findings.append((f"{model.__tablename__}.id={row.id}", json_path, public))
    return findings


def _is_json_type(column_type) -> bool:
    return isinstance(column_type, JSON) or type(column_type).__name__ == "JSONB"


async def _all_media_references(db) -> set[str]:
    """扫描所有字符串/JSON 列，收集公开媒体引用（仅文件名）。"""
    referenced: set[str] = set()
    for table in Base.metadata.sorted_tables:
        columns = [
            column for column in table.columns
            if isinstance(column.type, (String, Text)) or _is_json_type(column.type)
        ]
        if not columns:
            continue
        try:
            stream = await db.stream(select(*columns))
            async for row in stream:
                for value in row:
                    for text in _iter_strings(value):
                        if text.startswith(PUBLIC_PREFIX):
                            referenced.add(text.removeprefix(PUBLIC_PREFIX).split("/")[-1])
        except Exception as exc:  # 单表读取失败不应中断审计
            print(f"SKIP_TABLE {table.name}: {type(exc).__name__}")
    return referenced


def _public_files() -> list[Path]:
    root = Path(UPLOAD_DIR)
    if not root.is_dir():
        return []
    files = [path for path in root.iterdir() if path.is_file()]
    for subdir in PUBLIC_SUBDIRS:
        directory = root / subdir
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file())
    return files


def _scan_public_files(referenced: set[str]):
    dangerous: list[Path] = []
    orphans: list[Path] = []
    for path in _public_files():
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            dangerous.append(path)
        elif path.name not in referenced:
            orphans.append(path)
    return dangerous, orphans


def _safe_upload_path(relative: str) -> Path | None:
    key = relative.strip().lstrip("/")
    if not key or ".." in key or "\\" in key:
        return None
    root = Path(UPLOAD_DIR).resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        return None
    return target


def delete_media_url(url: str) -> bool:
    """清理脚本专用：任意扩展名删除，但必须位于上传根目录内。"""
    if not url.startswith(PUBLIC_PREFIX):
        return False
    target = _safe_upload_path(url.removeprefix(PUBLIC_PREFIX))
    if target is None or not target.is_file():
        return False
    try:
        target.unlink()
    except OSError as exc:
        print(f"DELETE_FAILED {target}: {exc}")
        return False
    return True


async def audit(delete_files: bool, scan_orphans: bool, delete_dangerous: bool, delete_orphans: bool):
    async with get_db_ctx() as db:
        findings = await _pii_findings(db)
        referenced = await _all_media_references(db) if scan_orphans else set()

    for owner, column, value in findings:
        action = ""
        if delete_files and delete_media_url(value):
            action = " [file deleted]"
        print(f"{owner}.{column} -> {value}{action}")
    print(f"public_references={len(findings)}")

    dangerous_count = orphan_count = 0
    if scan_orphans:
        dangerous, orphans = _scan_public_files(referenced)
        dangerous_count, orphan_count = len(dangerous), len(orphans)
        for path in dangerous:
            action = ""
            if delete_dangerous and delete_media_url(f"{PUBLIC_PREFIX}{path.relative_to(UPLOAD_DIR)}"):
                action = " [deleted]"
            print(f"DANGEROUS_EXTENSION {path}{action}")
        for path in orphans:
            action = ""
            if delete_orphans and delete_media_url(f"{PUBLIC_PREFIX}{path.relative_to(UPLOAD_DIR)}"):
                action = " [deleted]"
            print(f"ORPHAN {path}{action}")
        print(f"dangerous_files={dangerous_count} orphan_files={orphan_count}")
    return len(findings), dangerous_count, orphan_count


def main() -> int:
    parser = argparse.ArgumentParser(description="public material reference audit")
    parser.add_argument("--delete-files", action="store_true", help="delete referenced public PII files")
    parser.add_argument("--scan-orphans", action="store_true", help="scan public media roots")
    parser.add_argument("--delete-dangerous", action="store_true", help="delete dangerous legacy files")
    parser.add_argument("--delete-orphans", action="store_true", help="delete unreferenced public files")
    args = parser.parse_args()
    references, dangerous, orphans = asyncio.run(
        audit(args.delete_files, args.scan_orphans, args.delete_dangerous, args.delete_orphans)
    )
    return 0 if references == 0 and dangerous == 0 and orphans == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
