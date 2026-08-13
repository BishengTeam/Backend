import urllib.parse
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


SECRET_FILE_FIELDS = (
    "DB_PASSWORD",
    "DATABASE_URL",
    "DATABASE_URL_SYNC",
    "REDIS_URL",
    "JWT_SECRET",
    "PII_HASH_KEY",
    "WECHAT_SECRET",
    "WECHAT_PAY_PRIVATE_KEY",
    "WECHAT_PAY_API_V3_KEY",
    "WECHAT_PAY_PLATFORM_CERTIFICATE",
    "ALIYUN_OSS_ACCESS_KEY_ID",
    "ALIYUN_OSS_ACCESS_KEY_SECRET",
    "QUIZ_OSS_ACCESS_KEY_ID",
    "QUIZ_OSS_ACCESS_KEY_SECRET",
    "QUIZ_METRICS_BEARER_TOKEN",
)
SECRET_FILE_NAMES = {f"{field_name}_FILE" for field_name in SECRET_FILE_FIELDS}


def _load_secret_files(values: Any) -> Any:
    """Resolve explicit ``FIELD_FILE`` inputs before regular validation.

    Docker/Kubernetes secrets are mounted as files.  Supporting this common
    convention avoids placing credentials in Compose inspection output while
    retaining the ordinary environment variables for local development.
    Direct values and file paths are mutually exclusive to prevent ambiguous
    rotations.  File content is stripped only at its outer boundary.
    """

    if values is None:
        data: dict[str, Any] = {}
    elif isinstance(values, dict):
        data = dict(values)
    else:
        try:
            data = dict(values)
        except (TypeError, ValueError):
            return values

    for field_name in SECRET_FILE_FIELDS:
        file_name = f"{field_name}_FILE"
        file_path = data.pop(file_name, None)
        direct = data.get(field_name)
        if not file_path:
            continue
        if direct not in (None, ""):
            raise ValueError(f"{field_name} and {file_name} are mutually exclusive")
        path = Path(str(file_path))
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"cannot read secret file for {field_name}") from exc
        if not value:
            raise ValueError(f"secret file for {field_name} is empty")
        data[field_name] = value
    return data


def _with_database_driver(url: str, *, async_driver: bool) -> str:
    """Normalize a PostgreSQL URL for the requested SQLAlchemy driver.

    Deployments commonly provide only one of ``DATABASE_URL`` (async engine)
    and ``DATABASE_URL_SYNC`` (Alembic).  Treating a plain ``postgresql://``
    URL as an async URL, or copying a ``+psycopg2`` URL into the async engine,
    fails only at process startup and is particularly hard to diagnose.  The
    URL authority, path and query are preserved verbatim; only the scheme is
    changed.
    """

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if not scheme.startswith("postgres"):
        return url
    if async_driver:
        target_scheme = "postgresql+asyncpg"
    else:
        target_scheme = "postgresql"
    if scheme == target_scheme:
        return url
    return urllib.parse.urlunsplit(
        (target_scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


class Settings(BaseSettings):
    APP_NAME: str = "weMiniApp"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_TIMEZONE: str = "Asia/Shanghai"

    # 数据库连接组件（优先），有特殊字符的密码不会经过 configparser
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "bisheng"
    DB_PASSWORD: str = ""
    DB_NAME: str = "wemini_app_dev"

    # 也支持直接传完整 URL（不含特殊字符时）
    DATABASE_URL: str = ""
    DATABASE_URL_SYNC: str = ""

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    HEALTH_CHECK_TIMEOUT_SECONDS: float = 3.0

    # Deployment-only indirection.  These paths are consumed by the pre-model
    # validator and are never used as application credentials themselves.
    DB_PASSWORD_FILE: str = ""
    DATABASE_URL_FILE: str = ""
    DATABASE_URL_SYNC_FILE: str = ""
    REDIS_URL_FILE: str = ""
    JWT_SECRET_FILE: str = ""
    PII_HASH_KEY_FILE: str = ""
    WECHAT_SECRET_FILE: str = ""
    WECHAT_PAY_PRIVATE_KEY_FILE: str = ""
    WECHAT_PAY_API_V3_KEY_FILE: str = ""
    WECHAT_PAY_PLATFORM_CERTIFICATE_FILE: str = ""
    ALIYUN_OSS_ACCESS_KEY_ID_FILE: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET_FILE: str = ""
    QUIZ_OSS_ACCESS_KEY_ID_FILE: str = ""
    QUIZ_OSS_ACCESS_KEY_SECRET_FILE: str = ""
    QUIZ_METRICS_BEARER_TOKEN_FILE: str = ""

    @model_validator(mode="before")
    @classmethod
    def load_secret_files(cls, values: Any) -> Any:
        return _load_secret_files(values)

    def model_dump(self, *args, **kwargs):
        """Keep secret mount paths out of diagnostics and accidental dumps."""

        excluded = kwargs.pop("exclude", None)
        if excluded is None:
            merged_exclude: set[str] = set(SECRET_FILE_NAMES)
        elif isinstance(excluded, set):
            merged_exclude = set(excluded) | SECRET_FILE_NAMES
        elif isinstance(excluded, dict):
            merged_exclude = dict(excluded)
            merged_exclude.update({name: True for name in SECRET_FILE_NAMES})
        else:
            merged_exclude = excluded
        return super().model_dump(*args, exclude=merged_exclude, **kwargs)

    @model_validator(mode="after")
    def build_database_urls(self) -> "Settings":
        self.DATABASE_URL = (self.DATABASE_URL or "").strip()
        self.DATABASE_URL_SYNC = (self.DATABASE_URL_SYNC or "").strip()
        if self.DATABASE_URL:
            # The async engine must never receive a plain/synchronous driver
            # URL.  Keep an explicitly selected sync driver below for Alembic.
            self.DATABASE_URL = _with_database_driver(
                self.DATABASE_URL, async_driver=True
            )
        elif self.DATABASE_URL_SYNC:
            self.DATABASE_URL = _with_database_driver(
                self.DATABASE_URL_SYNC, async_driver=True
            )
        # A deployment may provide one complete URL instead of repeating
        # credentials in DB_* variables.  Derive the companion driver URL so
        # Alembic/health checks and the async engine use the same endpoint.
        if not self.DATABASE_URL_SYNC and self.DATABASE_URL:
            self.DATABASE_URL_SYNC = _with_database_driver(
                self.DATABASE_URL, async_driver=False
            )
        elif self.DATABASE_URL_SYNC.startswith("postgresql+asyncpg://"):
            self.DATABASE_URL_SYNC = _with_database_driver(
                self.DATABASE_URL_SYNC, async_driver=False
            )
        if not self.DATABASE_URL:
            encoded = urllib.parse.quote_plus(self.DB_PASSWORD)
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.DB_USER}:{encoded}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        if not self.DATABASE_URL_SYNC:
            encoded = urllib.parse.quote_plus(self.DB_PASSWORD)
            self.DATABASE_URL_SYNC = (
                f"postgresql://{self.DB_USER}:{encoded}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return self

    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 120
    PII_HASH_KEY: str = ""

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if v in {"change-me-in-production", "change-me", "your-secret-key"}:
            raise ValueError("JWT_SECRET must not be a default/placeholder value")
        return v

    @model_validator(mode="after")
    def configure_pii_hash_key(self) -> "Settings":
        if not self.PII_HASH_KEY:
            if self.APP_ENV == "production":
                raise ValueError("PII_HASH_KEY is required in production")
            self.PII_HASH_KEY = self.JWT_SECRET
        if len(self.PII_HASH_KEY) < 32:
            raise ValueError("PII_HASH_KEY must be at least 32 characters")
        return self

    @field_validator("APP_DEBUG")
    @classmethod
    def validate_debug_in_production(cls, v: bool, info) -> bool:
        env = info.data.get("APP_ENV", "")
        if env == "production" and v:
            import warnings
            warnings.warn("APP_DEBUG forced to False in production environment", UserWarning)
            return False
        return v

    WECHAT_APPID: str = ""
    WECHAT_SECRET: str = ""

    # All payment traffic uses WeChat Pay API V3.  Production explicitly
    # enables the capability after the merchant materials are provisioned.
    WECHAT_PAY_ENABLED: bool = False
    WECHAT_PAY_API_VERSION: str = "v3"
    WECHAT_PAY_MCHID: str = ""
    WECHAT_PAY_APPID: str = ""
    WECHAT_PAY_NOTIFY_URL: str = ""
    WECHAT_PAY_REFUND_NOTIFY_URL: str = ""
    WECHAT_PAY_CERT_SERIAL_NO: str = ""
    WECHAT_PAY_PRIVATE_KEY: str = ""
    WECHAT_PAY_API_V3_KEY: str = ""
    WECHAT_PAY_PLATFORM_CERTIFICATE: str = ""
    WECHAT_PAY_PLATFORM_CERT_SERIAL_NO: str = ""
    WECHAT_PAY_NOTIFICATION_TOLERANCE_SECONDS: int = 300
    WECHAT_PAY_RECONCILE_POLL_SECONDS: int = 30
    WECHAT_PAY_RECONCILE_BATCH_SIZE: int = 100
    WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS: int = 30
    WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE: int = 100
    WECHAT_PAY_REFUND_RECONCILE_AFTER_SECONDS: int = 60
    WECHAT_PAY_SYNC_RATE_PER_MINUTE: int = 10

    CHAT_BACKEND: str = "disabled"
    DIFY_API_BASE: str = ""
    DIFY_API_KEY: str = ""

    # 实名核验: none(仅格式校验) / aliyun / tencent
    IDENTITY_VERIFY_PROVIDER: str = "none"
    # 阿里云 身份证实名认证
    ALIYUN_ACCESS_KEY_ID: str = ""
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_VERIFY_APP_CODE: str = ""  # 云市场 AppCode
    # 腾讯云 实名认证
    TENCENT_SECRET_ID: str = ""
    TENCENT_SECRET_KEY: str = ""

    LOGIN_POSTER_URL: str | None = None

    CORS_ORIGINS: list[str] = []

    UPLOAD_DIR: str = "./uploads"
    STORAGE_TYPE: str = "local"

    # Human-resources certification materials. Production is private Aliyun OSS.
    RENSHE_STORAGE_TYPE: str = "local"
    ALIYUN_OSS_ENDPOINT: str = ""
    ALIYUN_OSS_BUCKET: str = ""
    ALIYUN_OSS_ACCESS_KEY_ID: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET: str = ""
    ALIYUN_OSS_PREFIX: str = "renshe"
    ALIYUN_OSS_SIGNED_URL_TTL_SECONDS: int = 300
    RENSHE_TEMPLATE_DIR: str = "../docs/renshe"
    RENSHE_WORKER_POLL_SECONDS: int = 5
    # Production retention is frozen at 30 days.  Non-production deployments
    # may shorten it only through deployment configuration for destructive OSS
    # cleanup UAT; no business API exposes this value.
    RENSHE_CLEANUP_RETENTION_DAYS: float = 30

    # Frozen quiz-domain limits and worker settings.  Development keeps the
    # embedded loop enabled for convenience; production compose explicitly
    # disables it in the Web process and runs ``app.quiz_worker`` separately.
    QUIZ_TASKS_ENABLED: bool = True
    QUIZ_EMBEDDED_WORKER_ENABLED: bool = True
    QUIZ_WORKER_PROCESS: bool = False
    QUIZ_EXAM_DURATION_SECONDS: int = 3600
    QUIZ_MIN_QUESTION_COUNT: int = 10
    QUIZ_MAX_QUESTION_COUNT: int = 100
    QUIZ_WRONG_MAX_QUESTION_COUNT: int = 20
    QUIZ_IMPORT_MAX_FILE_BYTES: int = 10 * 1024 * 1024
    QUIZ_IMPORT_MAX_QUESTIONS: int = 5000
    QUIZ_IMPORT_RETENTION_DAYS: int = 7
    QUIZ_IMPORT_STORAGE_TYPE: str = "local"
    QUIZ_OSS_ENDPOINT: str = ""
    QUIZ_OSS_BUCKET: str = ""
    QUIZ_OSS_ACCESS_KEY_ID: str = ""
    QUIZ_OSS_ACCESS_KEY_SECRET: str = ""
    QUIZ_OSS_PREFIX: str = "quiz-imports"
    QUIZ_OSS_SIGNED_URL_TTL_SECONDS: int = 300
    QUIZ_WORKER_POLL_SECONDS: int = 5
    QUIZ_WORKER_HEARTBEAT_SECONDS: int = 15
    QUIZ_WORKER_STALE_SECONDS: int = 120
    QUIZ_WORKER_MAX_RETRIES: int = 5
    QUIZ_QUESTION_LIST_RATE_PER_MINUTE: int = 60
    QUIZ_ANSWER_SAVE_RATE_PER_MINUTE: int = 120
    QUIZ_ADMIN_WRITE_RATE_PER_MINUTE: int = 120
    QUIZ_ADMIN_BATCH_RATE_PER_MINUTE: int = 30
    QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE: int = 10
    QUIZ_ADMIN_SIGNED_URL_RATE_PER_MINUTE: int = 60
    QUIZ_METRICS_ENABLED: bool = True
    # Prometheus must authenticate with this dedicated Bearer token.  It is
    # never emitted in metrics, health documents or logs.
    QUIZ_METRICS_BEARER_TOKEN: str = ""

    @model_validator(mode="after")
    def validate_renshe_storage(self) -> "Settings":
        if not 0.5 <= self.HEALTH_CHECK_TIMEOUT_SECONDS <= 10:
            raise ValueError("HEALTH_CHECK_TIMEOUT_SECONDS must be between 0.5 and 10")
        if not 1 <= self.ALIYUN_OSS_SIGNED_URL_TTL_SECONDS <= 300:
            raise ValueError("ALIYUN_OSS_SIGNED_URL_TTL_SECONDS must be between 1 and 300")
        if not 1 <= self.RENSHE_WORKER_POLL_SECONDS <= 300:
            raise ValueError("RENSHE_WORKER_POLL_SECONDS must be between 1 and 300")
        if not 0 < self.RENSHE_CLEANUP_RETENTION_DAYS <= 30:
            raise ValueError(
                "RENSHE_CLEANUP_RETENTION_DAYS must be greater than 0 and at most 30"
            )
        if self.APP_ENV == "production":
            if self.RENSHE_CLEANUP_RETENTION_DAYS != 30:
                raise ValueError(
                    "RENSHE_CLEANUP_RETENTION_DAYS must remain 30 in production"
                )
            if self.RENSHE_STORAGE_TYPE != "aliyun_oss":
                raise ValueError("RENSHE_STORAGE_TYPE must be aliyun_oss in production")
            required = {
                "ALIYUN_OSS_ENDPOINT": self.ALIYUN_OSS_ENDPOINT,
                "ALIYUN_OSS_BUCKET": self.ALIYUN_OSS_BUCKET,
                "ALIYUN_OSS_ACCESS_KEY_ID": self.ALIYUN_OSS_ACCESS_KEY_ID,
                "ALIYUN_OSS_ACCESS_KEY_SECRET": self.ALIYUN_OSS_ACCESS_KEY_SECRET,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"missing production OSS settings: {', '.join(missing)}")
        return self

    @model_validator(mode="after")
    def validate_production_integrations(self) -> "Settings":
        """Fail fast when a production process could not serve RS-ZY safely.

        Development and test environments may use local storage and disabled
        payment.  Production must receive explicit database, Redis, WeChat
        login, and V3 payment configuration; no V2 or fake-success fallback is
        accepted for the human-resources flow.
        """

        if self.APP_ENV != "production":
            return self

        # ``build_database_urls`` fills a URL from component settings before
        # this validator runs, so validate the resulting URL itself rather than
        # checking only whether the string is non-empty.  This still permits a
        # deployment to provide a complete DATABASE_URL without duplicating
        # its password in DB_* variables.
        parsed_database = urllib.parse.urlparse(self.DATABASE_URL)
        if not (
            parsed_database.scheme.startswith("postgresql")
            and parsed_database.hostname
            and parsed_database.username
            and parsed_database.password
            and parsed_database.path.strip("/")
        ):
            raise ValueError(
                "production database URL must include PostgreSQL host, user, password and database"
            )
        if not self.REDIS_URL.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss:// in production")

        missing_login = [
            name
            for name, value in {
                "WECHAT_APPID": self.WECHAT_APPID,
                "WECHAT_SECRET": self.WECHAT_SECRET,
            }.items()
            if not value
        ]
        if missing_login:
            raise ValueError(
                "missing production WeChat login settings: "
                + ", ".join(missing_login)
            )

        if not self.WECHAT_PAY_ENABLED:
            raise ValueError(
                "WECHAT_PAY_ENABLED must be true in production; "
                "disable production traffic until V3 payment is provisioned"
            )
        if self.WECHAT_PAY_API_VERSION.lower() != "v3":
            raise ValueError("WECHAT_PAY_API_VERSION must be v3 in production")
        missing_payment = [
            name
            for name, value in {
                "WECHAT_PAY_MCHID": self.WECHAT_PAY_MCHID,
                "WECHAT_PAY_APPID": self.WECHAT_PAY_APPID or self.WECHAT_APPID,
                "WECHAT_PAY_CERT_SERIAL_NO": self.WECHAT_PAY_CERT_SERIAL_NO,
                "WECHAT_PAY_PRIVATE_KEY": self.WECHAT_PAY_PRIVATE_KEY,
                "WECHAT_PAY_API_V3_KEY": self.WECHAT_PAY_API_V3_KEY,
                "WECHAT_PAY_PLATFORM_CERTIFICATE": self.WECHAT_PAY_PLATFORM_CERTIFICATE,
                "WECHAT_PAY_PLATFORM_CERT_SERIAL_NO": self.WECHAT_PAY_PLATFORM_CERT_SERIAL_NO,
                "WECHAT_PAY_NOTIFY_URL": self.WECHAT_PAY_NOTIFY_URL,
                "WECHAT_PAY_REFUND_NOTIFY_URL": self.WECHAT_PAY_REFUND_NOTIFY_URL,
            }.items()
            if not value
        ]
        if missing_payment:
            raise ValueError(
                "missing production WeChat Pay V3 settings: "
                + ", ".join(missing_payment)
            )
        return self

    @model_validator(mode="after")
    def validate_wechat_pay_runtime(self) -> "Settings":
        if self.WECHAT_PAY_ENABLED and self.WECHAT_PAY_API_VERSION.lower() != "v3":
            raise ValueError("WECHAT_PAY_API_VERSION must be v3 when payment is enabled")
        if self.WECHAT_PAY_ENABLED and self.WECHAT_PAY_API_V3_KEY:
            if len(self.WECHAT_PAY_API_V3_KEY.encode("utf-8")) != 32:
                raise ValueError("WECHAT_PAY_API_V3_KEY must be exactly 32 bytes")
        if (
            self.WECHAT_PAY_ENABLED
            and self.WECHAT_PAY_NOTIFY_URL
            and self.WECHAT_PAY_REFUND_NOTIFY_URL
            and self.WECHAT_PAY_NOTIFY_URL == self.WECHAT_PAY_REFUND_NOTIFY_URL
        ):
            raise ValueError(
                "WECHAT_PAY_NOTIFY_URL and WECHAT_PAY_REFUND_NOTIFY_URL must differ"
            )
        if (
            self.APP_ENV == "production"
            and self.WECHAT_PAY_NOTIFY_URL
            and not self.WECHAT_PAY_NOTIFY_URL.startswith("https://")
        ):
            raise ValueError("WECHAT_PAY_NOTIFY_URL must use https:// in production")
        if (
            self.APP_ENV == "production"
            and self.WECHAT_PAY_REFUND_NOTIFY_URL
            and not self.WECHAT_PAY_REFUND_NOTIFY_URL.startswith("https://")
        ):
            raise ValueError(
                "WECHAT_PAY_REFUND_NOTIFY_URL must use https:// in production"
            )
        if not 60 <= self.WECHAT_PAY_NOTIFICATION_TOLERANCE_SECONDS <= 600:
            raise ValueError(
                "WECHAT_PAY_NOTIFICATION_TOLERANCE_SECONDS must be between 60 and 600"
            )
        if not 5 <= self.WECHAT_PAY_RECONCILE_POLL_SECONDS <= 300:
            raise ValueError(
                "WECHAT_PAY_RECONCILE_POLL_SECONDS must be between 5 and 300"
            )
        if not 1 <= self.WECHAT_PAY_RECONCILE_BATCH_SIZE <= 500:
            raise ValueError(
                "WECHAT_PAY_RECONCILE_BATCH_SIZE must be between 1 and 500"
            )
        if not 5 <= self.WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS <= 300:
            raise ValueError(
                "WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS must be between 5 and 300"
            )
        if not 1 <= self.WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE <= 500:
            raise ValueError(
                "WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE must be between 1 and 500"
            )
        if not 10 <= self.WECHAT_PAY_REFUND_RECONCILE_AFTER_SECONDS <= 86400:
            raise ValueError(
                "WECHAT_PAY_REFUND_RECONCILE_AFTER_SECONDS must be between 10 and 86400"
            )
        if not 1 <= self.WECHAT_PAY_SYNC_RATE_PER_MINUTE <= 60:
            raise ValueError(
                "WECHAT_PAY_SYNC_RATE_PER_MINUTE must be between 1 and 60"
            )
        return self

    @model_validator(mode="after")
    def validate_quiz_runtime(self) -> "Settings":
        frozen_values = {
            "QUIZ_EXAM_DURATION_SECONDS": (self.QUIZ_EXAM_DURATION_SECONDS, 3600),
            "QUIZ_MIN_QUESTION_COUNT": (self.QUIZ_MIN_QUESTION_COUNT, 10),
            "QUIZ_MAX_QUESTION_COUNT": (self.QUIZ_MAX_QUESTION_COUNT, 100),
            "QUIZ_WRONG_MAX_QUESTION_COUNT": (
                self.QUIZ_WRONG_MAX_QUESTION_COUNT,
                20,
            ),
            "QUIZ_IMPORT_MAX_FILE_BYTES": (
                self.QUIZ_IMPORT_MAX_FILE_BYTES,
                10 * 1024 * 1024,
            ),
            "QUIZ_IMPORT_MAX_QUESTIONS": (self.QUIZ_IMPORT_MAX_QUESTIONS, 5000),
            "QUIZ_IMPORT_RETENTION_DAYS": (self.QUIZ_IMPORT_RETENTION_DAYS, 7),
            "QUIZ_QUESTION_LIST_RATE_PER_MINUTE": (
                self.QUIZ_QUESTION_LIST_RATE_PER_MINUTE,
                60,
            ),
            "QUIZ_ANSWER_SAVE_RATE_PER_MINUTE": (
                self.QUIZ_ANSWER_SAVE_RATE_PER_MINUTE,
                120,
            ),
            "QUIZ_ADMIN_WRITE_RATE_PER_MINUTE": (
                self.QUIZ_ADMIN_WRITE_RATE_PER_MINUTE,
                120,
            ),
            "QUIZ_ADMIN_BATCH_RATE_PER_MINUTE": (
                self.QUIZ_ADMIN_BATCH_RATE_PER_MINUTE,
                30,
            ),
            "QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE": (
                self.QUIZ_ADMIN_IMPORT_RATE_PER_MINUTE,
                10,
            ),
            "QUIZ_ADMIN_SIGNED_URL_RATE_PER_MINUTE": (
                self.QUIZ_ADMIN_SIGNED_URL_RATE_PER_MINUTE,
                60,
            ),
        }
        changed = [
            f"{name} must remain {expected}"
            for name, (actual, expected) in frozen_values.items()
            if actual != expected
        ]
        if changed:
            raise ValueError("; ".join(changed))
        if not 1 <= self.QUIZ_OSS_SIGNED_URL_TTL_SECONDS <= 300:
            raise ValueError("QUIZ_OSS_SIGNED_URL_TTL_SECONDS must be between 1 and 300")
        if not 1 <= self.QUIZ_WORKER_POLL_SECONDS <= 60:
            raise ValueError("QUIZ_WORKER_POLL_SECONDS must be between 1 and 60")
        if not 1 <= self.QUIZ_WORKER_HEARTBEAT_SECONDS < self.QUIZ_WORKER_STALE_SECONDS:
            raise ValueError(
                "QUIZ_WORKER_HEARTBEAT_SECONDS must be positive and below "
                "QUIZ_WORKER_STALE_SECONDS"
            )
        if not 1 <= self.QUIZ_WORKER_MAX_RETRIES <= 20:
            raise ValueError("QUIZ_WORKER_MAX_RETRIES must be between 1 and 20")
        if self.QUIZ_WORKER_PROCESS and not self.QUIZ_TASKS_ENABLED:
            raise ValueError(
                "QUIZ_TASKS_ENABLED must be true when QUIZ_WORKER_PROCESS is enabled"
            )
        if self.QUIZ_WORKER_PROCESS and self.QUIZ_EMBEDDED_WORKER_ENABLED:
            raise ValueError(
                "QUIZ_EMBEDDED_WORKER_ENABLED must be false in the standalone quiz worker"
            )
        if self.QUIZ_TASKS_ENABLED and not self.REDIS_URL.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss:// when quiz tasks are enabled")
        if self.APP_ENV == "production":
            if not self.QUIZ_TASKS_ENABLED:
                raise ValueError("QUIZ_TASKS_ENABLED must be true in production")
            if self.QUIZ_EMBEDDED_WORKER_ENABLED:
                raise ValueError(
                    "QUIZ_EMBEDDED_WORKER_ENABLED must be false in production"
                )
            if self.QUIZ_IMPORT_STORAGE_TYPE != "aliyun_oss":
                raise ValueError("QUIZ_IMPORT_STORAGE_TYPE must be aliyun_oss in production")
            required = {
                "QUIZ_OSS_ENDPOINT": self.QUIZ_OSS_ENDPOINT,
                "QUIZ_OSS_BUCKET": self.QUIZ_OSS_BUCKET,
                "QUIZ_OSS_ACCESS_KEY_ID": self.QUIZ_OSS_ACCESS_KEY_ID,
                "QUIZ_OSS_ACCESS_KEY_SECRET": self.QUIZ_OSS_ACCESS_KEY_SECRET,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    f"missing production quiz OSS settings: {', '.join(missing)}"
                )
            if not self.QUIZ_METRICS_ENABLED:
                raise ValueError("QUIZ_METRICS_ENABLED must be true in production")
            if len(self.QUIZ_METRICS_BEARER_TOKEN) < 32:
                raise ValueError(
                    "QUIZ_METRICS_BEARER_TOKEN must contain at least 32 characters "
                    "in production"
                )
        return self

    model_config = {
        # Local development follows the repository README and reads `.env`.
        # Tests provide explicit, non-secret values from tests/conftest.py and
        # production deployments should inject real environment variables.
        "env_file": (".env", ".env.development"),
        "extra": "ignore",
    }


settings = Settings()
