"""Content validation for private course uploads."""
import json
import math
import subprocess
from pathlib import Path

from app.port.exceptions import ValidationException


def inspect_resource(path: Path) -> dict:
    if path.suffix.lower() == ".pdf":
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValidationException("PDF 文件不能超过 50MB")
        try:
            import pypdfium2 as pdfium
            with path.open("rb") as file:
                if not file.read(1024).lstrip().startswith(b"%PDF-"):
                    raise ValueError("invalid signature")
            with pdfium.PdfDocument(str(path)) as document:
                count = len(document)
                if count <= 0:
                    raise ValueError("no pages")
                # Load every page to reject broken page trees; no full-document rasterization.
                for index in range(count):
                        page = document[index]
                        try:
                            if min(page.get_size()) <= 0:
                                raise ValueError("invalid page")
                        finally:
                            page.close()
            return dict(resource_type="document", page_count=count, duration=None, content_type="application/pdf")
        except Exception as error:
            raise ValidationException("PDF 损坏、无有效页面或已加密无法阅读，请重新导出后上传") from error
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True, timeout=60)
        info = json.loads(result.stdout)
        container = info.get("format", {}).get("format_name", "")
        allowed = {".mp4": "mp4", ".mov": "mov", ".mkv": "matroska"}
        if allowed[path.suffix.lower()] not in container.split(","):
            raise ValueError("container does not match extension")
        if not any(stream.get("codec_type") == "video" for stream in info.get("streams", [])):
            raise ValueError("no video stream")
        duration = math.ceil(float(info["format"]["duration"]))
        if duration <= 0:
            raise ValueError("invalid duration")
        mime = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}[path.suffix.lower()]
        return dict(resource_type="video", page_count=None, duration=duration, content_type=mime)
    except FileNotFoundError as error:
        raise ValidationException("视频验证服务不可用，请联系管理员安装 ffprobe 后重试") from error
    except Exception as error:
        raise ValidationException("视频内容无效或无法读取时长，请检查文件后重试") from error
