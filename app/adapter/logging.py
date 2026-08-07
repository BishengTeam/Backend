import logging
import sys
from contextvars import ContextVar

from app.port.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(request_id)s | %(name)s:%(lineno)d | %(message)s"
)


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def setup_logging() -> None:
    level = logging.DEBUG if settings.APP_DEBUG else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestIDFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    # P3 结构化日志：production 模式下可在此处切换 JSON handler
    # 需配合 python-json-logger 使用，例如：
    #   from pythonjsonlogger.json import JsonFormatter
    #   handler.setFormatter(JsonFormatter(LOG_FORMAT))

    uvicorn_log_level = logging.INFO if settings.APP_DEBUG else logging.WARNING
    logging.getLogger("uvicorn").setLevel(uvicorn_log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
