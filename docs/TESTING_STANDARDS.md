# 测试标准

> 更新日期：2026-05-18  
> 适用范围：后端接口、Service、数据库迁移、外部集成边界

---

## 1. 测试分类

### 1.1 本地无数据库测试（unit）

目录：`tests/unit/`

用途：在没有数据库、Redis、微信、支付、Dify 等真实环境时，快速验证代码是否符合预期。

可以覆盖：

- Pydantic schema 校验。
- 纯函数、校验器、状态机、答案判分等无副作用逻辑。
- 路由是否存在、`response_model` 是否显式声明。
- Service 是否遵守分层规范，例如不接收 `Request` / `Response`。
- 通过测试专用 fixture/mock 验证外部依赖调用边界。

允许 mock 的范围：

- 只能写在 `tests/`、测试 fixture 或测试辅助对象中。
- 可以 mock 当前用户、外部服务、时间、随机数、签名结果、DB session 边界。
- 不允许为了测试在 `app/` 生产代码中硬编码 mock 分支、假数据或占位回复。

结论口径：本地无数据库测试通过，只能证明“代码结构/本地逻辑/测试 mock 场景通过”，不能证明真实数据库读写、事务、约束、幂等已验证。

### 1.2 PostgreSQL 数据库集成测试（integration/db）

目录：`tests/integration/db/`

用途：在真实 PostgreSQL 测试库中，通过 ORM、迁移和真实 SQL 验证业务状态。

必须覆盖：

- Alembic 迁移可执行到最新版本。
- 真实表结构、索引、唯一约束、检查约束生效。
- Service/API 对 PostgreSQL 的真实读写。
- 事务提交/回滚、幂等、分页、状态流转、并发冲突。
- 测试数据隔离和清理。

数据库要求：

- 必须使用 PostgreSQL。
- 不接受 SQLite 作为数据库集成测试替代。
- 使用独立测试库和独立连接串，例如 `TEST_DATABASE_URL` / `TEST_DATABASE_URL_SYNC`。
- 测试不得依赖人工预置数据，必须由 fixture/seed 创建并清理。

---

## 2. 测试框架与命令

统一使用 `pytest + pytest-asyncio`。

本地无数据库测试：

```powershell
python -m pytest tests/unit -v
```

数据库集成测试：

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://<user>:<password>@localhost:5432/<test_db>"
$env:TEST_DATABASE_URL_SYNC="postgresql://<user>:<password>@localhost:5432/<test_db>"
alembic upgrade head
python -m pytest tests/integration/db -v
```

全量测试：

```powershell
python -m compileall -q app alembic tests
python -m pytest tests/unit -v
python -m pytest tests/integration/db -v
```

---

## 3. 接口列表状态口径

`docs/接口列表.md` 中接口状态必须按验证证据标注：

| 状态 | 含义 |
|------|------|
| ⏳ | 待开发或无可执行代码 |
| 🧱 | 代码完成，尚未测试 |
| 🧪 | 代码完成 + 本地无数据库测试通过，待数据库集成测试或外部联调 |
| ✅ | 代码完成 + 本地无数据库测试通过 + PostgreSQL 数据库集成测试通过 |

规则：

- 涉及数据库读写、事务、约束、分页、状态流转的接口，未完成 PostgreSQL 集成测试前不得标 `✅`。
- 不依赖数据库的纯配置/健康检查接口，可在本地无数据库测试充分覆盖后标 `✅`。
- 涉及微信、支付、Dify、OSS 等外部服务的接口，在当前无外部联调条件时，只能标 `🧪` 并明确“待外部联调”。
- 文案必须区分“本地无DB测试”和“数据库集成测试”，禁止笼统写“本地测试”导致误解。

示例：

```md
🧪 POST /api/quiz/submit — 答题提交（代码完成 + 本地无DB测试，待数据库集成测试）
✅ POST /api/quiz/submit — 答题提交（代码完成 + 本地无DB测试 + PostgreSQL数据库集成测试）
```

---

## 4. 当前限制

当前仓库已有本地无数据库测试；数据库集成测试目录和执行标准已定义，但在未提供 PostgreSQL 测试库连接串前，不应宣称任何数据库状态流转已完成真实验证。
