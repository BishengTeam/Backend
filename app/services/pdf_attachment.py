"""赛事附件 PDF → 页图转换。

原件存私有目录（不经 /media 暴露），仅转换出的页图落入公开媒体目录，
配合小程序「内容可见、文件不可下载」的展示约束；截图为该方案边界外的手段。
"""

import io
import uuid
from pathlib import Path

import pypdfium2 as pdfium

from app.port.config import settings
from app.port.exceptions import BusinessException
from app.services.upload import UploadService

# 单附件上限：页数与体积，防止同步转换拖垮 worker
MAX_PDF_PAGES = 30
MAX_PDF_SIZE = 20 * 1024 * 1024
# 页图目标宽度（px）：小程序正文宽约 750rpx，2 倍图可保证清晰度
TARGET_PAGE_WIDTH = 1080
# 附件标题展示上限
MAX_TITLE_LENGTH = 64


def _save_private_pdf(content: bytes) -> Path:
    """原件落私有目录，仅供留档/重渲染，绝不经 /media 对外暴露。"""
    target_dir = Path(settings.PRIVATE_UPLOAD_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{uuid.uuid4().hex}.pdf"
    file_path.write_bytes(content)
    return file_path


def _render_jpeg(page: pdfium.PdfPage) -> bytes:
    scale = max(TARGET_PAGE_WIDTH / page.get_width(), 1.0)
    image = page.render(scale=scale).to_pil().convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def render_pdf_attachment(content: bytes, filename: str) -> dict:
    """校验并渲染附件 PDF，返回 {title, page_count, pages}。

    pages 为公开媒体 URL 列表；任何解析/限制问题抛 BusinessException。
    """
    if len(content) > MAX_PDF_SIZE:
        raise BusinessException("附件 PDF 不能超过 20MB")
    if not content.startswith(b"%PDF"):
        raise BusinessException("仅支持上传 PDF 文件")

    try:
        doc = pdfium.PdfDocument(content)
    except Exception:
        raise BusinessException("PDF 无法解析，请确认文件未损坏")

    try:
        page_count = len(doc)
        if page_count == 0:
            raise BusinessException("PDF 内容为空")
        if page_count > MAX_PDF_PAGES:
            raise BusinessException(f"附件最多 {MAX_PDF_PAGES} 页，当前 {page_count} 页")

        pages: list[str] = []
        for page in doc:
            result = UploadService.save_bytes(_render_jpeg(page), ".jpg")
            pages.append(result["url"])
    finally:
        doc.close()

    _save_private_pdf(content)

    title = (Path(filename).stem or "附件").strip()[:MAX_TITLE_LENGTH]
    return {"title": title, "page_count": page_count, "pages": pages}
