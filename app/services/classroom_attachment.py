"""课堂问答题附件：OSS 直传、HTML 消毒、交卷绑定与保留清理。"""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.classroom.src.index import (
    Classroom,
    ClassroomMember,
    ClassroomQuestion,
    ClassroomQuiz,
    ClassroomQuizAttachment,
    ClassroomQuizSubmission,
)
from app.port.exceptions import (
    BusinessException,
    NotFoundException,
    ThirdPartyException,
    ValidationException,
)
from app.services.course_storage import CourseStorage

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)

ATTACHMENT_UPLOAD_SIGN_TTL_SECONDS = 3600
ATTACHMENT_READ_TTL_SECONDS = 3600
ATTACHMENT_ORPHAN_HOURS = 24
ATTACHMENT_RETENTION_YEARS = 2

IMAGE_MAX_BYTES = 10 * 1024 * 1024
DOCUMENT_MAX_BYTES = 20 * 1024 * 1024
ARCHIVE_MAX_BYTES = 50 * 1024 * 1024
QUESTION_IMAGE_LIMIT = 9
QUESTION_FILE_LIMIT = 5
QUESTION_TOTAL_BYTES = 100 * 1024 * 1024
SHORT_ANSWER_HTML_MAX_BYTES = 64 * 1024

ATTACHMENT_KEY_PREFIX = "classroom-attachments"

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_DOCUMENT_EXTENSIONS = {".doc", ".docx"}
_ARCHIVE_EXTENSIONS = {".zip"}
_IMAGE_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_DOCUMENT_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ARCHIVE_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
}

_ALLOWED_HTML_TAGS = frozenset(
    {"p", "br", "strong", "b", "em", "i", "u", "s", "h2", "h3", "ol", "ul", "li", "img"}
)
_VOID_TAGS = frozenset({"br", "img"})
_ALLOWED_IMG_ATTRS = frozenset({"src", "alt", "width", "height"})
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>")
_SRC_ATTR_RE = re.compile(r'\bsrc="([^"]*)"')


def normalize_filename(value: str) -> str:
    """客户端文件名只保留basename，防止路径注入对象元数据。"""

    return Path(value.replace("\\", "/")).name.strip()[:256]


def validate_attachment(
    *, filename: str, content_type: str, size_bytes: int
) -> tuple[str, str, str]:
    """返回 (extension, kind, normalized_content_type)。规则即产品决策，勿随意放宽。"""

    name = normalize_filename(filename)
    if not name:
        raise ValidationException("文件名无效")
    extension = Path(name).suffix.lower()
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if extension in _IMAGE_EXTENSIONS:
        if size_bytes <= 0 or size_bytes > IMAGE_MAX_BYTES:
            raise ValidationException("图片大小必须在 10MB 以内")
        if normalized_type not in _IMAGE_CONTENT_TYPES:
            raise ValidationException("图片类型必须是 JPG、PNG 或 WebP")
        return extension, "image", normalized_type
    if extension in _DOCUMENT_EXTENSIONS:
        if size_bytes <= 0 or size_bytes > DOCUMENT_MAX_BYTES:
            raise ValidationException("Word 文件大小必须在 20MB 以内")
        if normalized_type not in _DOCUMENT_CONTENT_TYPES:
            raise ValidationException("Word 文件类型必须是 doc 或 docx")
        return extension, "document", normalized_type
    if extension in _ARCHIVE_EXTENSIONS:
        if size_bytes <= 0 or size_bytes > ARCHIVE_MAX_BYTES:
            raise ValidationException("压缩包大小必须在 50MB 以内")
        if normalized_type not in _ARCHIVE_CONTENT_TYPES:
            raise ValidationException("压缩包类型必须是 zip")
        return extension, "archive", normalized_type
    raise ValidationException("附件仅支持 JPG、PNG、WebP、doc、docx 或 zip")


class _WhitelistSanitizer(HTMLParser):
    """标签白名单重建器：只输出允许标签，属性只保留 img 的白名单四项。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag not in _ALLOWED_HTML_TAGS:
            return
        if tag == "img":
            kept = [
                (name, value)
                for name, value in attrs
                if name in _ALLOWED_IMG_ATTRS and value
            ]
            if not any(name == "src" for name, _ in kept):
                return
            rendered = "".join(
                f' {name}="{html_module.escape(value, quote=True)}"'
                for name, value in kept
            )
            self.parts.append(f"<img{rendered}>")
        else:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):  # noqa: ANN001
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in _ALLOWED_HTML_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if data:
            self.parts.append(html_module.escape(data, quote=False))


def sanitize_short_answer_html(value: str) -> str:
    """问答题答案消毒：白名单标签重建，其余内容当纯文本转义或丢弃。"""

    parser = _WhitelistSanitizer()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:  # malformed html: 宁可拒绝也不冒险
        raise ValidationException("问答题答案格式无效") from exc
    return "".join(parser.parts)


def extract_attachment_key(value: str) -> str | None:
    """从裸 key、bucket URL 或签名 URL 提取 classroom-attachments 对象 key。"""

    if not value:
        return None
    if value.startswith(f"{ATTACHMENT_KEY_PREFIX}/"):
        return value.split("?", 1)[0]
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    path = parsed.path.lstrip("/")
    if path.startswith(f"{ATTACHMENT_KEY_PREFIX}/"):
        return path.split("?", 1)[0]
    return None


def filter_short_answer_images(
    value: str,
    allowed_keys: set[str],
    sign: Callable[[str], str],
) -> str:
    """只保留归属本用户的内嵌图，并替换为新的签名读地址；其余整标签剥离。"""

    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _SRC_ATTR_RE.search(tag)
        if src_match is None:
            return ""
        key = extract_attachment_key(html_module.unescape(src_match.group(1)))
        if key is None or key not in allowed_keys:
            return ""
        try:
            new_src = sign(key)
        except Exception:
            return ""
        escaped = html_module.escape(new_src, quote=True)
        return _SRC_ATTR_RE.sub(lambda _m: f'src="{escaped}"', tag, count=1)

    return _IMG_TAG_RE.sub(_replace, value)


def canonicalize_short_answer_html(value: str, allowed_keys: set[str]) -> str:
    """交卷落库形态：只保留归属本用户的图，src 改写为裸对象 key（读取时再重签）。"""

    return filter_short_answer_images(value, allowed_keys, lambda key: key)


def resign_short_answer_html(
    value: str, signer: Callable[[str], str]
) -> str:
    """读取形态：把存储的裸 key src 重签为短时读地址。"""

    keys = {
        key
        for key in (
            extract_attachment_key(html_module.unescape(match.group(1)))
            for match in _SRC_ATTR_RE.finditer(value)
        )
        if key
    }
    return filter_short_answer_images(value, keys, signer)


def count_short_answer_images(value: str) -> int:
    return len(_IMG_TAG_RE.findall(value))


def _attachment_item(row: ClassroomQuizAttachment, url: str) -> dict:
    return {
        "id": row.id,
        "question_id": row.question_id,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "url": url,
    }


def make_read_signer() -> Callable[[str], str]:
    """本地同步签名器：OSS 未配置时退化为透传（key 原样）。"""

    try:
        bucket = CourseStorage._bucket()
    except Exception:
        return lambda key: key

    def _sign(key: str) -> str:
        try:
            return bucket.sign_url(
                "GET", key, ATTACHMENT_READ_TTL_SECONDS, slash_safe=True
            )
        except Exception:
            return key

    return _sign


class ClassroomAttachmentService:
    """学生作答附件：上传签发、草稿列表、删除、读签名、保留清理。"""

    @staticmethod
    async def _uploadable_quiz_question(
        db, user_id: int, quiz_id: int, question_id: int
    ) -> tuple[ClassroomQuiz, ClassroomQuestion]:
        quiz = await db.get(ClassroomQuiz, quiz_id)
        if quiz is None:
            raise NotFoundException("测验")
        member = (await db.execute(
            select(ClassroomMember.id).where(
                ClassroomMember.classroom_id == quiz.classroom_id,
                ClassroomMember.user_id == user_id,
            )
        )).scalar_one_or_none()
        if member is None:
            raise BusinessException("未加入该课堂")
        classroom = await db.get(Classroom, quiz.classroom_id)
        if classroom is None or classroom.status != "active":
            raise BusinessException("课堂不存在或已停课")
        if quiz.status != "ongoing":
            raise BusinessException("测验已结束，无法上传附件")
        if quiz.started_at + timedelta(minutes=quiz.duration_minutes) <= _now():
            raise BusinessException("测验已结束，无法上传附件")
        submitted = (await db.execute(
            select(ClassroomQuizSubmission.id).where(
                ClassroomQuizSubmission.quiz_id == quiz_id,
                ClassroomQuizSubmission.user_id == user_id,
            ).limit(1)
        )).scalar()
        if submitted:
            raise BusinessException("已提交过答卷")
        question = await db.get(ClassroomQuestion, question_id)
        if (
            question is None
            or question.classroom_id != quiz.classroom_id
            or question.id not in (quiz.question_ids or [])
            or question.type != "short"
        ):
            raise ValidationException("附件只能上传到本测验的问答题")
        return quiz, question

    @classmethod
    async def create_upload(
        cls,
        *,
        user_id: int,
        quiz_id: int,
        question_id: int,
        filename: str,
        content_type: str,
        size_bytes: int,
    ) -> dict:
        name = normalize_filename(filename)
        extension, kind, normalized_type = validate_attachment(
            filename=name, content_type=content_type, size_bytes=size_bytes
        )
        async with get_db_ctx() as db:
            quiz, _question = await cls._uploadable_quiz_question(
                db, user_id, quiz_id, question_id
            )
            installation = await CourseStorage.installation_id()
            object_key = (
                f"{ATTACHMENT_KEY_PREFIX}/{installation}/c{quiz.classroom_id}"
                f"/q{quiz_id}/u{user_id}/{question_id}/{uuid.uuid4().hex}{extension}"
            )

            def _sign_put() -> str:
                return CourseStorage._bucket().sign_url(
                    "PUT",
                    object_key,
                    ATTACHMENT_UPLOAD_SIGN_TTL_SECONDS,
                    # OSS V1 签名默认覆盖 content-type：签名与直传请求头必须逐字节一致，
                    # 因此把规范化后的类型签进去并回传给客户端使用。
                    headers={"Content-Type": normalized_type},
                    slash_safe=True,
                )

            try:
                upload_url = await asyncio.to_thread(_sign_put)
            except ThirdPartyException:
                raise
            except Exception as exc:
                raise ThirdPartyException("课堂附件上传地址签发失败") from exc
            row = ClassroomQuizAttachment(
                quiz_id=quiz_id,
                user_id=user_id,
                question_id=question_id,
                kind=kind,
                status="uploaded",
                object_key=object_key,
                filename=name,
                content_type=normalized_type,
                size_bytes=size_bytes,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return {
                "attachment_id": row.id,
                "object_key": object_key,
                "upload_url": upload_url,
                "content_type": normalized_type,
                "expires_at": datetime.now(timezone.utc)
                + timedelta(seconds=ATTACHMENT_UPLOAD_SIGN_TTL_SECONDS),
            }

    @staticmethod
    async def _member_quiz(db, user_id: int, quiz_id: int) -> ClassroomQuiz:
        quiz = await db.get(ClassroomQuiz, quiz_id)
        if quiz is None:
            raise NotFoundException("测验")
        member = (await db.execute(
            select(ClassroomMember.id).where(
                ClassroomMember.classroom_id == quiz.classroom_id,
                ClassroomMember.user_id == user_id,
            )
        )).scalar_one_or_none()
        if member is None:
            raise BusinessException("未加入该课堂")
        classroom = await db.get(Classroom, quiz.classroom_id)
        if classroom is None or classroom.status != "active":
            raise BusinessException("课堂不存在或已停课")
        return quiz

    @classmethod
    async def list_drafts(cls, user_id: int, quiz_id: int) -> list[dict]:
        async with get_db_ctx() as db:
            await cls._member_quiz(db, user_id, quiz_id)
            rows = (await db.execute(
                select(ClassroomQuizAttachment).where(
                    ClassroomQuizAttachment.quiz_id == quiz_id,
                    ClassroomQuizAttachment.user_id == user_id,
                    ClassroomQuizAttachment.status == "uploaded",
                ).order_by(ClassroomQuizAttachment.id)
            )).scalars().all()
            urls = await cls.sign_read_urls([row.object_key for row in rows])
            return [
                _attachment_item(row, urls.get(row.object_key, ""))
                for row in rows
            ]

    @classmethod
    async def delete(cls, user_id: int, quiz_id: int, attachment_id: int) -> None:
        async with get_db_ctx() as db:
            await cls._member_quiz(db, user_id, quiz_id)
            row = await db.get(ClassroomQuizAttachment, attachment_id)
            if (
                row is None
                or row.quiz_id != quiz_id
                or row.user_id != user_id
            ):
                raise NotFoundException("附件")
            if row.status != "uploaded":
                raise BusinessException("已随答卷提交的附件不能删除")
            await cls._delete_objects_quietly([row.object_key])
            await db.delete(row)
            await db.commit()

    @staticmethod
    async def sign_read_urls(keys: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(keys))
        if not unique:
            return {}

        def _sign_all() -> dict[str, str]:
            bucket = CourseStorage._bucket()
            return {
                key: bucket.sign_url(
                    "GET", key, ATTACHMENT_READ_TTL_SECONDS, slash_safe=True
                )
                for key in unique
            }

        return await asyncio.to_thread(_sign_all)

    @staticmethod
    async def _delete_objects_quietly(keys: list[str]) -> None:
        unique = [key for key in dict.fromkeys(keys) if key]
        if not unique:
            return

        def _delete_all() -> None:
            bucket = CourseStorage._bucket()
            for key in unique:
                try:
                    bucket.delete_object(key)
                except Exception:  # 对象可能未真正上传；行删除仍继续
                    logger.warning("课堂附件对象删除失败: key=%s", key)

        await asyncio.to_thread(_delete_all)

    @classmethod
    async def bind_submitted_attachments(
        cls,
        db,
        *,
        user_id: int,
        quiz: ClassroomQuiz,
        submission_id: int,
        requested: dict[str, list[int]],
        canonical_answers: dict[int, str],
        short_questions: dict[int, ClassroomQuestion],
    ) -> dict[int, str]:
        """交卷绑定：显式 attachment_id + HTML 内嵌图行；执行每题限额。"""

        rows = (await db.execute(
            select(ClassroomQuizAttachment).where(
                ClassroomQuizAttachment.quiz_id == quiz.id,
                ClassroomQuizAttachment.user_id == user_id,
                ClassroomQuizAttachment.status == "uploaded",
            )
        )).scalars().all()
        by_id = {row.id: row for row in rows}
        by_key = {row.object_key: row for row in rows}

        explicit: dict[int, list[ClassroomQuizAttachment]] = {}
        for raw_question_id, attachment_ids in (requested or {}).items():
            try:
                question_id = int(raw_question_id)
            except (TypeError, ValueError):
                raise ValidationException("附件题目归属无效")
            if question_id not in short_questions:
                raise ValidationException("附件只能提交到问答题")
            current = explicit.setdefault(question_id, [])
            for attachment_id in dict.fromkeys(attachment_ids or []):
                row = by_id.get(attachment_id)
                if row is None or row.question_id != question_id:
                    raise ValidationException("附件不存在或不属于当前题目")
                if row not in current:
                    current.append(row)

        for question_id, answer_html in (canonical_answers or {}).items():
            if question_id not in short_questions or not answer_html:
                continue
            current = explicit.setdefault(question_id, [])
            for match in _SRC_ATTR_RE.finditer(answer_html):
                key = extract_attachment_key(html_module.unescape(match.group(1)))
                row = by_key.get(key or "")
                if row is not None and row not in current:
                    current.append(row)

        per_question: dict[int, list[ClassroomQuizAttachment]] = {
            qid: list(items) for qid, items in explicit.items()
        }
        now = _now()
        for question_id, bound in per_question.items():
            images = [row for row in bound if row.kind == "image"]
            files = [row for row in bound if row.kind in ("document", "archive")]
            if len(images) > QUESTION_IMAGE_LIMIT:
                raise ValidationException(
                    f"每题最多上传 {QUESTION_IMAGE_LIMIT} 张图片（含正文插图）"
                )
            if len(files) > QUESTION_FILE_LIMIT:
                raise ValidationException(
                    f"每题最多上传 {QUESTION_FILE_LIMIT} 个 Word 或压缩包"
                )
            if sum(row.size_bytes for row in bound) > QUESTION_TOTAL_BYTES:
                raise ValidationException("每题上传总量不能超过 100MB")
            for row in bound:
                row.status = "bound"
                row.submission_id = submission_id
                row.bound_at = now
        return {row.id: row.object_key for bound in per_question.values() for row in bound}


async def cleanup_classroom_attachments() -> tuple[int, int]:
    """孤儿（24h 未交卷）与到期（课堂停课满 2 年）附件清理。返回 (孤儿数, 到期数)。"""

    orphaned = 0
    expired = 0
    async with get_db_ctx() as db:
        orphan_cutoff = _now() - timedelta(hours=ATTACHMENT_ORPHAN_HOURS)
        orphan_rows = (await db.execute(
            select(ClassroomQuizAttachment).where(
                ClassroomQuizAttachment.status == "uploaded",
                ClassroomQuizAttachment.created_at < orphan_cutoff,
            ).limit(500)
        )).scalars().all()
        retention_cutoff = _now() - timedelta(days=365 * ATTACHMENT_RETENTION_YEARS)
        expired_rows = (await db.execute(
            select(ClassroomQuizAttachment)
            .join(ClassroomQuiz, ClassroomQuiz.id == ClassroomQuizAttachment.quiz_id)
            .join(Classroom, Classroom.id == ClassroomQuiz.classroom_id)
            .where(
                ClassroomQuizAttachment.status == "bound",
                Classroom.status == "stopped",
                Classroom.stopped_at.is_not(None),
                Classroom.stopped_at < retention_cutoff,
            ).limit(500)
        )).scalars().all()
        rows = [*orphan_rows, *expired_rows]
        if rows:
            await ClassroomAttachmentService._delete_objects_quietly(
                [row.object_key for row in rows]
            )
            for row in rows:
                await db.delete(row)
            await db.commit()
            orphaned = len(orphan_rows)
            expired = len(expired_rows)
    return orphaned, expired
