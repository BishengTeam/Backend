import logging
import sys

from app.core.config import settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"


def setup_logging() -> None:
    level = logging.DEBUG if settings.APP_DEBUG else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stdout)

    for lib in ("uvicorn", "sqlalchemy.engine", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)
