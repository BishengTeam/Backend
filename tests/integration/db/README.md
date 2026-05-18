# PostgreSQL 数据库集成测试

本目录只放需要真实 PostgreSQL 测试库的测试。

要求：

- 必须设置 `TEST_DATABASE_URL`，格式以 `postgresql+asyncpg://` 开头。
- 必须设置 `TEST_DATABASE_URL_SYNC`，格式以 `postgresql://` 开头。
- 测试前执行 `alembic upgrade head`。
- 测试数据由 fixture/seed 创建并清理，禁止依赖人工预置数据。
- SQLite 不能作为本目录测试的替代数据库。
