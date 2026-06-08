import urllib.parse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


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

    @model_validator(mode="after")
    def build_database_urls(self) -> "Settings":
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

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if v in {"change-me-in-production", "change-me", "your-secret-key"}:
            raise ValueError("JWT_SECRET must not be a default/placeholder value")
        return v

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

    WECHAT_PAY_MCHID: str = ""
    WECHAT_PAY_API_KEY: str = ""
    WECHAT_PAY_APPID: str = ""
    WECHAT_PAY_NOTIFY_URL: str = ""

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

    model_config = {
        "env_file": ".env.development",
        "extra": "ignore",
    }


settings = Settings()
