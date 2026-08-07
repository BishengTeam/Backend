# GOV-02 至 GOV-06 实施记录

> 记录日期：2026-08-07

## 已完成

- `GOV-02`：确认 `数据字典与状态机.md` 为人社唯一业务依据，补充契约维护规则和容量口径说明。
- `GOV-03`：新增 `接口契约与错误码.md`，并新增 `scripts/check_renshe_contract.py`，校验人社路径、鉴权、六类材料和企业接口停用状态。
- `GOV-04`：`pytest.ini` 增加项目根路径和严格 marker；缺少测试数据库时统一跳过 `integration_db`，避免测试收集阶段误报；默认 pytest 命令固定为模块/虚拟环境执行方式。
- `GOV-05`：新增 `scripts/quality_gate.sh` 和 `docs/质量命令.md`，固定 Backend、Admin、Platform 的质量入口及依赖检查。
- `GOV-06`：新增 `scripts/check_migrations.py`，静态检查唯一 Alembic head 和禁止部署脚本使用 `stamp`；修复旧迁移在 Alembic offline/mock 连接下的 schema inspection，使 offline SQL 能完整生成。

## 当前证据

```text
Backend unit: 335 passed
Backend default pytest: 335 passed, 99 skipped (缺少独立 PostgreSQL 测试库)
Renshe contract: 23 paths / 24 operations, enterprise paths 0, materials 6
Alembic static: one head quiz002, 48 revisions
Alembic offline SQL: base -> head generated successfully with a verified quiz backup reference
```

## 尚待外部条件

- 提供专用 PostgreSQL 测试库后执行 `scripts/check_migrations.py --full-cycle`，验证真实升级、降级和再次升级。
- Admin 和 Platform 需在各自可写、依赖完整的项目环境执行质量命令；本 Backend 变更不修改两个项目的业务代码。
- 三端负责人完成契约评审后，才能把 GOV-02/GOV-03 标记为最终 `DONE`。
