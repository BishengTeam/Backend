# Backend — FastAPI 后端服务

**对象身份**: `product/app/backend`，为 Platform 小程序和 Admin 管理后台提供 REST API。

## 技术栈

Python 3.11+ / FastAPI + Uvicorn / SQLAlchemy 2.0 async / PostgreSQL (asyncpg) / Redis 7+ / Alembic / slowapi / PyJWT

## 公开能力

- **用户端** — `GET/POST/PUT/DELETE /api/*`（认证、用户、课程、订单、支付、积分、答题、聊天、活动等 22 个模块）
- **管理端** — `/admin/*`
- **健康检查** — `GET /health`（DB + Redis 探活）、`GET /ready`（就绪探针）

## 上游依赖

| 依赖       | 用途                 |
| ---------- | -------------------- |
| PostgreSQL | 主数据存储            |
| Redis      | 缓存 / 令牌黑名单     |
| 微信 API   | 小程序登录、微信支付   |
| Dify       | AI 对话引擎           |

## 下游影响

- **Admin 管理后台** — 运营管理界面，消费 `/admin/*` API
- **Platform 小程序前端** — 用户端，消费 `/api/*` API

## 边界规则

开发与协作规范详见 [`docs/DEVELOPMENT_STANDARDS.md`](docs/DEVELOPMENT_STANDARDS.md)（分支策略、代码风格、提交流程、API 设计约定等）。

## 文档入口

| 文档                                    | 说明             |
| --------------------------------------- | ---------------- |
| `docs/DEVELOPMENT_STANDARDS.md`         | 开发规范         |
| `docs/TESTING_STANDARDS.md`             | 测试规范         |
| `docs/后台接口文档.md`                   | 后台接口说明     |
| `docs/接口文档.md` `docs/接口文档规范.md` | API 文档与规范   |
| `docs/接口列表.md`                      | 接口清单         |
| `docs/plan/`                            | 计划与设计文档   |

## 快速开始

```bash
cp .env.example .env && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --reload
```
