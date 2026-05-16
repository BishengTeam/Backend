# 小程序平台后端 开发规范

> 编制日期：2026-05-14
> 技术栈：Python 3.11+ / FastAPI / SQLAlchemy (async) / PostgreSQL / Redis / Dify / Ollama

## 1. 核心原则

### 1.1 单一职责

每个文件、每个函数、每个类只做一件事。判断标准：
- 能用一句话描述其职责，且这句话中没有"和"字
- 修改一个功能时，不需要修改不相关的文件

### 1.2 依赖方向

```
Router → Service → Repository (DB) / ExternalService
不允许: Service → Router
不允许: Service → HTTP Request/Response 对象
不允许: modules/A/services/* → modules/B/services/*（除非 B 的 service 在 __init__.py 中显式导出）
```

### 1.3 代码即文档

- 命名自解释，不需要注释说"做什么"
- 仅在 WHY 不显而易见时写注释（边界条件、性能考量、Bug 规避）
- 不为类型写注释 — Pydantic 模型和类型提示已经足够

---

## 2. 后端目录结构

```
backend/
├── app/
│   ├── main.py                # FastAPI 应用入口，挂载路由、中间件
│   ├── core/                  # 全局基础设施
│   │   ├── config.py          # pydantic-settings 配置管理
│   │   ├── security.py        # JWT 签发/验证、密码哈希
│   │   ├── database.py        # SQLAlchemy async engine + session 工厂
│   │   ├── redis.py           # Redis 客户端封装
│   │   └── logging.py         # 日志配置
│   ├── middleware/             # 中间件
│   │   ├── cors.py            # CORS 配置
│   │   ├── auth.py            # JWT 认证中间件/依赖注入
│   │   ├── security.py        # 输入/输出内容安全审查
│   │   └── request_id.py      # 请求 ID 追踪
│   ├── models/                # SQLAlchemy ORM 模型（纯表定义）
│   │   ├── base.py            # 声明基类 + 通用 mixin (id, created_at, updated_at)
│   │   ├── user.py
│   │   ├── order.py
│   │   ├── course.py
│   │   └── ...
│   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── user.py
│   │   ├── order.py
│   │   └── ...
│   ├── api/                   # 路由层（按模块分文件，仅处理 HTTP）
│   │   ├── __init__.py        # 聚合所有 router
│   │   ├── auth.py
│   │   ├── user.py
│   │   ├── chat.py
│   │   ├── orders.py
│   │   ├── courses.py
│   │   └── ...
│   ├── services/              # 业务逻辑层
│   │   ├── auth.py
│   │   ├── user.py
│   │   ├── chat.py
│   │   ├── payment.py
│   │   ├── quiz.py
│   │   └── ...
│   ├── integrations/          # 外部服务对接
│   │   ├── wechat.py          # 微信登录/支付/订阅消息
│   │   ├── dify.py            # Dify 工作流调用
│   │   ├── ollama.py          # Ollama 模型调用（备用）
│   │   └── storage.py         # 存储抽象层（本地/OSS）
│   └── utils/                 # 纯工具函数（无副作用、无 DB 访问）
│       ├── pinyin.py
│       ├── pdf.py
│       ├── excel.py
│       └── helpers.py
├── alembic/                   # 数据库迁移
│   ├── versions/
│   └── env.py
├── tests/                     # 测试
│   ├── conftest.py            # fixture 工厂
│   ├── test_auth.py
│   └── ...
├── scripts/                   # 运维/工具脚本
│   ├── seed_data.py
│   ├── import_quiz.py         # 题库导入工具
│   └── init_prices.py
├── uploads/                   # 本地开发文件存储（生产用 OSS 替代）
├── .env.development           # 本地开发环境变量
├── .env.example               # 环境变量模板（不含真实密钥）
├── pyproject.toml             # 项目元数据 + 依赖
├── requirements.txt           # 锁定的依赖版本
└── README.md
```

### 目录职责约束

| 目录 | 可以 | 禁止 |
|------|------|------|
| `api/` | 参数解析、调用 service、返回响应 | 写业务逻辑、直接操作 DB |
| `services/` | 业务编排、调用 repository/外部服务 | 直接操作 HTTP 上下文 |
| `models/` | ORM 表定义、字段描述 | 业务逻辑、API 调用 |
| `schemas/` | Pydantic 模型定义 | 业务逻辑、DB 访问 |
| `middleware/` | 请求预处理/后处理 | 业务逻辑 |
| `integrations/` | 封装外部 API 调用 | 包含本项目业务规则 |
| `utils/` | 纯函数、无状态工具 | DB 访问、外部 API 调用 |
| `core/` | 全局配置和基础设施 | 业务逻辑 |

---

## 3. 模块分层规范

每个业务模块横跨三层，结构如下：

```
模块示例: 用户认证 (auth)
─────────────────────────
api/auth.py         →   POST /api/auth/login  →  参数校验 → 调用 auth_service.login()
services/auth.py    →   def login(code)       →  调用 wechat.code2session() → JWT 签发 → 返回 token
integrations/wechat.py → def code2session()  →  HTTP 调用微信接口
models/user.py      →   User 表定义
schemas/user.py     →   LoginRequest, LoginResponse, UserProfileResponse
```

### api/ 层规范

```python
# api/user.py
from fastapi import APIRouter, Depends
from app.schemas.user import UserProfileResponse, UserProfileUpdate
from app.services.user import UserService
from app.middleware.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/user", tags=["用户"])

@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前用户资料"""
    return await UserService().get_profile(current_user.id)

@router.put("/profile", response_model=UserProfileResponse)
async def update_profile(
    body: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
):
    """更新用户资料"""
    return await UserService().update_profile(current_user.id, body)
```

规则：
- Router 函数体不超过 5 行 — 仅做参数收集 + 调用 service
- 不直接访问 `request`/`response` 对象（通过 Depends 注入所需信息）
- `response_model` 始终显式声明
- Depends 做认证注入，不在函数体内手动验证 token

### services/ 层规范

```python
# services/user.py
from app.models.user import User
from app.schemas.user import UserProfileUpdate
from app.core.database import get_db

class UserService:

    async def get_profile(self, user_id: int) -> User:
        async with get_db() as db:
            return await db.get(User, user_id)

    async def update_profile(self, user_id: int, data: UserProfileUpdate) -> User:
        async with get_db() as db:
            user = await db.get(User, user_id)
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(user, key, value)
            await db.commit()
            await db.refresh(user)
            return user
```

规则：
- Service 方法签名参数是原始类型/Pydantic model，不接受 HTTP 对象
- 每个 Service 类对应一个业务域
- DB 操作通过 `get_db()` 上下文管理器

### integrations/ 层规范

```python
# integrations/wechat.py
import httpx
from app.core.config import settings

class WechatClient:
    """微信 API 封装，仅做 HTTP 调用和数据转换，不包含业务判断"""

    async def code2session(self, code: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.WECHAT_APPID,
                    "secret": settings.WECHAT_SECRET,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            return resp.json()
```

规则：
- integrations 层只做 HTTP 调用 + 数据格式转换
- 不包含业务判断（成功/失败处理在 service 层）
- 配置从 `settings` 读取，不硬编码

---

## 4. 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 文件名 | snake_case | `auth_service.py`, `user_profile.py` |
| 目录名 | snake_case 或复数 | `services/`, `integrations/` |
| 类名 | PascalCase | `UserService`, `WechatClient`, `OrderRepository` |
| 函数/方法 | snake_case | `get_user_by_id()`, `is_token_expired()` |
| 变量 | snake_case | `user_id`, `access_token` |
| 常量 | UPPER_SNAKE_CASE | `MAX_PROFILE_EDITS`, `DEFAULT_PAGE_SIZE` |
| Pydantic 模型 | PascalCase + 后缀 | `UserCreateRequest`, `OrderListResponse` |
| ORM 模型 | PascalCase（单数） | `User`, `Order`, `CourseEnrollment` |
| 数据库表名 | 小写单数 | `user`, `order`, `course_enrollment` |
| API 路径 | kebab-case | `/api/quick-questions`, `/api/wrong-book` |
| 环境变量 | UPPER_SNAKE_CASE | `DATABASE_URL`, `JWT_SECRET` |

### Pydantic 模型命名后缀约定

| 后缀 | 用途 |
|------|------|
| `*Request` | 请求体 |
| `*Response` | 响应体 |
| `*Create` | 创建请求 |
| `*Update` | 更新请求 |
| `*Query` | 查询参数 |
| `*Filter` | 筛选条件 |

---

## 5. API 设计规范

### 5.1 路径格式

```
/api/<resource>          — 资源列表 / 创建
/api/<resource>/{id}     — 指定资源
/api/<resource>/<action> — 资源上的动作
```

### 5.2 HTTP 方法语义

| 方法 | 语义 | 示例 |
|------|------|------|
| GET | 读取 | `GET /api/orders?status=paid` |
| POST | 创建 / 触发动作 | `POST /api/orders` `POST /api/quiz/submit` |
| PUT | 全量更新 | `PUT /api/user/profile` |
| DELETE | 删除 | `DELETE /api/user/account` |

### 5.3 统一响应格式

```json
{
  "code": 200,
  "message": "ok",
  "data": { ... }
}
```

| code | 含义 |
|------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 参数校验失败 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 业务冲突（如重复报名） |
| 422 | 业务逻辑错误（如积分不足） |
| 500 | 服务器内部错误 |

### 5.4 分页响应格式

```json
{
  "code": 200,
  "message": "ok",
  "data": {
    "items": [...],
    "total": 150,
    "page": 1,
    "page_size": 20
  }
}
```

### 5.5 错误响应格式

```json
{
  "code": 422,
  "message": "积分余额不足，当前余额 30，需要 100",
  "data": null
}
```

---

## 6. 数据库规范

### 6.1 表设计

- 每张表必须包含 `id`（主键）、`created_at`、`updated_at` 字段
- 表名使用**小写单数**（SQLAlchemy 默认行为，用 `__tablename__` 显式声明）
- 字段名使用 `snake_case`
- 外键字段名格式：`{related_table}_id`
- 价格/金额字段使用 `Integer`（以"分"为单位），避免浮点精度问题

### 6.2 ORM 模型基类

```python
# models/base.py
from sqlalchemy import Column, Integer, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

### 6.3 迁移规范

- 使用 Alembic 管理所有 DDL 变更
- 迁移文件纳入版本控制
- 不在迁移中写数据操作（数据初始化用 `scripts/seed_data.py`）
- 每次迁移在本地验证正向+回滚后再提交

---

## 7. 配置管理规范

### 7.1 配置类

```python
# core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str

    # JWT
    JWT_SECRET: str
    JWT_EXPIRES_IN: int = 7 * 24 * 3600  # 7天

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # 微信
    WECHAT_APPID: str = ""
    WECHAT_SECRET: str = ""

    # Dify
    DIFY_API_URL: str = "http://localhost:5001"
    DIFY_API_KEY: str = ""

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # 存储
    UPLOAD_DIR: str = "./uploads"
    STORAGE_BACKEND: str = "local"  # local | oss

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()
```

### 7.2 规则

- 所有配置项在 `core/config.py` 中统一定义
- 业务代码中 **严禁直接 `os.getenv()` 或 `os.environ[]`**
- 始终通过 `from app.core.config import settings` 访问
- 密钥类配置不在代码中设默认值（置空字符串，启动时校验）

---

## 8. 异常处理规范

### 8.1 业务异常

```python
# core/exceptions.py
class AppException(Exception):
    """应用基础异常"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message

class NotFoundException(AppException):
    def __init__(self, resource: str):
        super().__init__(code=404, message=f"{resource} 不存在")

class BusinessException(AppException):
    def __init__(self, message: str):
        super().__init__(code=422, message=message)

class UnauthorizedException(AppException):
    def __init__(self, message: str = "请先登录"):
        super().__init__(code=401, message=message)
```

### 8.2 全局异常处理器

```python
# middleware/error_handler.py
from fastapi import Request
from fastapi.responses import JSONResponse

async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )
```

### 8.3 规则

- Service 层遇到错误直接 `raise AppException`
- API 层不 try-catch（全局异常处理器统一处理）
- 只有需要特殊降级逻辑时才在 Service 中捕获异常

---

## 9. 代码审查清单

- [ ] 文件名和目录名符合命名规范
- [ ] Router 函数不超过 5 行，不含业务逻辑
- [ ] Service 不直接操作 HTTP 上下文（req/res）
- [ ] Pydantic 模型命名正确（Request/Response/Create/Update 后缀）
- [ ] API 响应格式统一 `{code, message, data}`
- [ ] 配置通过 `settings` 对象访问，不直接读环境变量
- [ ] 异常通过 `raise AppException` 抛出，不手动构造错误响应
- [ ] DB 字段使用 `snake_case`，ORM 模型使用 PascalCase
- [ ] 金额字段使用 `Integer`（分）
- [ ] 新模块的 service 通过模块级 `__init__.py` 公开导出
- [ ] 依赖方向正确：api → services → models/integrations

---

## 10. Git 规范

### 10.1 分支策略

- `main` — 生产就绪，通过 PR 合并，禁止直接推送
- `develop` — 开发主线
- `feature/<module>` — 新功能模块，如 `feature/user-auth`
- `fix/<description>` — Bug 修复

### 10.2 Commit 格式

```
<type>(<scope>): <subject>

type: feat | fix | refactor | style | docs | chore | perf | test
scope: api | service | model | db | config | test | docs
subject: 简短描述（中文，限 72 字符内）
```

示例：
```
feat(api): 实现用户登录接口 POST /api/auth/login
fix(service): 修复订单状态机回调幂等判断逻辑
chore(db): 新增 user_points 积分表 migration
```

---

> **参考依据**：SubGate 项目 [DEVELOPMENT_STANDARDS.md](../../SubGate/docs/DEVELOPMENT_STANDARDS.md)
> **编制日期**：2026-05-14