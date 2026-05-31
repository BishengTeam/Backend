from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "weMiniApp"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_TIMEZONE: str = "Asia/Shanghai"

    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

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

    WECHAT_APPID: str = ""
    WECHAT_SECRET: str = ""

    WECHAT_PAY_MCHID: str = ""
    WECHAT_PAY_API_KEY: str = ""
    WECHAT_PAY_APPID: str = ""
    WECHAT_PAY_NOTIFY_URL: str = ""

    CHAT_BACKEND: str = "disabled"
    DIFY_API_BASE: str = ""
    DIFY_API_KEY: str = ""

    LOGIN_POSTER_URL: str | None = None

    UPLOAD_DIR: str = "./uploads"
    STORAGE_TYPE: str = "local"

    model_config = {
        "env_file": ".env.development",
        "extra": "ignore",
    }


settings = Settings()
