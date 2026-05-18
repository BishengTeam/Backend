# 测试目录说明

本项目按测试环境拆分目录：

- `tests/unit/`：本地无数据库测试。允许在测试代码中使用 mock/fixture 快速验证 schema、纯业务逻辑、路由声明和分层规范。
- `tests/integration/db/`：PostgreSQL 数据库集成测试。必须使用独立 PostgreSQL 测试库，验证迁移、真实读写、事务、约束、幂等和状态流转。

统一测试框架为 `pytest + pytest-asyncio`。当前仓库保留的历史 `unittest.TestCase` 测试可以被 pytest 自动收集；新增测试优先使用 pytest 风格编写。

## 本地无数据库测试

```powershell
python -m pytest tests/unit -v
```

## PostgreSQL 数据库集成测试

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://<user>:<password>@localhost:5432/<test_db>"
$env:TEST_DATABASE_URL_SYNC="postgresql://<user>:<password>@localhost:5432/<test_db>"
alembic upgrade head
python -m pytest tests/integration/db -v
```

未配置测试库时，数据库集成测试应跳过或失败在环境检查阶段，不能被视为业务 DB 验证通过。
