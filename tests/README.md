# 测试目录说明

本项目按测试环境拆分目录：

- `tests/unit/`：本地无数据库测试。允许在测试代码中使用 mock/fixture 快速验证 schema、纯业务逻辑、路由声明和分层规范。
- `tests/integration/db/`：PostgreSQL 数据库集成测试。必须使用独立 PostgreSQL 测试库，验证迁移、真实读写、事务、约束、幂等和状态流转。

统一测试框架为 `pytest + pytest-asyncio`。当前仓库保留的历史 `unittest.TestCase` 测试可以被 pytest 自动收集；新增测试优先使用 pytest 风格编写。

## 本地无数据库测试

```bash
.venv/bin/python -m pytest tests/unit -v
```

## PostgreSQL 数据库集成测试

```bash
export TEST_DATABASE_URL="postgresql+asyncpg://<user>:<password>@localhost:5432/<test_db>"
export TEST_DATABASE_URL_SYNC="postgresql://<user>:<password>@localhost:5432/<test_db>"
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m pytest tests/integration/db -v
```

未配置测试库时，标记为 `integration_db` 的测试会统一跳过，不能被视为业务 DB 验证通过；质量报告必须保留 skipped 数量。提供两个测试 URL 后才会执行真实 PostgreSQL 读写、事务和迁移检查。

## 默认命令

从 `Backend` 根目录执行：

```bash
.venv/bin/pytest -q
```

该命令会运行所有本地单元测试，并在没有独立测试库时跳过数据库集成测试。使用 `python -m pytest` 或 `.venv/bin/pytest` 均应得到相同的收集结果。
