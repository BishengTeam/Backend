# 订单支付库存防超卖 TODO 计划表

> 编制日期：2026-05-18  
> 来源文档：[订单支付库存防超卖开发计划.md](订单支付库存防超卖开发计划.md)

---

## 1. 状态说明

- `未开始`：尚未进入实现。
- `进行中`：已有代码或文档改动，但未完成验收。
- `已完成`：代码、迁移、测试、文档均满足本行验收标准。
- `阻塞`：依赖业务规则、外部配置或测试环境。

---

## 2. 第一期：最小闭环

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| T-01 | P0 | 确认库存归属模型和业务规则 | 业务设计、`docs/接口文档.md` | 无 | 第一期采用 `cert_type` 级别认证报名名额库存：`inventory_type='certification'`，`ref_code` 关联 `cert_type`；退款默认不恢复库存，后续由配置决定 | 已完成 |
| T-02 | P0 | 扩展订单状态机，新增 `closed` | `app/services/order.py`、`app/schemas/order.py` | T-01 | 状态迁移包含 `pending -> closed`，`closed` 为终态，非法迁移会抛 `ConflictException` | 已完成 |
| T-03 | P0 | 扩展订单表字段 | `app/models/order.py`、Alembic | T-02 | 新增 `inventory_id`、`expires_at`、`closed_at`、`close_reason`；`ck_order_status` 包含 `closed` | 已完成 |
| T-04 | P0 | 新增库存/名额表 | `app/models/`、Alembic | T-01 | 表包含总量、可售、锁定、已售字段；数据库约束保证库存数量非负 | 未开始 |
| T-05 | P0 | 新增库存流水表 | `app/models/`、Alembic | T-04 | 能记录 lock、confirm、release 等动作及变更前后数量 | 未开始 |
| T-06 | P0 | 实现库存原子锁定能力 | `app/services/` | T-04、T-05 | 使用条件 `UPDATE ... WHERE available_quota >= 1`，库存不足时不创建订单 | 未开始 |
| T-07 | P0 | 改造创建订单接口 | `app/services/order.py`、`app/api/orders.py` | T-03、T-06 | 下单与锁库存处于同一事务；订单创建为 `pending` 且写入 `expires_at` | 未开始 |
| T-08 | P0 | 改造支付预下单过期校验 | `app/services/payment.py` | T-03、T-07 | 过期 `pending` 订单会先关闭并释放库存，不能继续发起微信支付 | 未开始 |
| T-09 | P0 | 改造支付成功回调确认成交 | `app/services/payment.py` | T-06、T-07 | `pending -> paid` 时库存 `locked -> sold`；重复回调不会重复确认成交 | 未开始 |
| T-10 | P0 | 增加 `transaction_id` 唯一约束 | `app/models/order.py`、Alembic | T-09 | 同一微信交易号不能绑定多个订单，PostgreSQL 约束测试通过 | 未开始 |
| T-11 | P0 | 新增超时关闭服务或脚本 | `app/services/`、`scripts/` | T-08 | 扫描过期 `pending` 订单，执行 `pending -> closed` 并释放库存 | 未开始 |
| T-12 | P0 | 补充本地无数据库测试 | `tests/unit/` | T-02、T-08、T-09 | 覆盖状态机、过期支付拒绝、回调幂等结构约束 | 未开始 |
| T-13 | P0 | 补充 PostgreSQL 集成测试 | `tests/integration/db/` | T-07、T-09、T-11 | 覆盖并发下单不超卖、重复回调不重复成交、超时释放库存 | 未开始 |
| T-14 | P1 | 更新接口文档和接口状态 | `docs/接口文档.md`、`docs/接口列表.md` | T-12、T-13 | 文档说明 `closed`、`expires_at`、库存不足、订单过期错误；状态标注符合测试证据 | 未开始 |

---

## 3. 第二期：补偿和运营能力

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| T-15 | P1 | 接入微信查单接口 | `app/integrations/wechat_pay.py` | 第一期完成 | 能按 `out_trade_no` 查询微信真实支付状态，并处理第三方异常 | 未开始 |
| T-16 | P1 | 新增支付对账补偿任务 | `app/services/`、`scripts/` | T-15 | 可将漏回调成功订单补偿为 `paid`，过期未支付订单补偿为 `closed` | 未开始 |
| T-17 | P1 | 增加异常订单标记 | `order` 表、管理侧服务 | T-16 | 对已关闭但微信成功等冲突订单有可追踪异常状态或处理记录 | 未开始 |
| T-18 | P1 | 明确并实现退款库存策略 | `app/services/payment.py`、库存服务 | T-01、T-09 | `paid/completed -> refunded` 时按业务规则决定是否恢复可售库存 | 未开始 |
| T-19 | P2 | 增加库存流水查询能力 | 管理侧 API、`inventory_record` | T-05 | 可按订单、库存对象、动作类型查询库存流水 | 未开始 |
| T-20 | P2 | 增加库存对账导出 | 管理侧 API、脚本 | T-19 | 可导出库存、订单、支付状态对账数据 | 未开始 |
| T-21 | P2 | 增加监控和告警口径 | 日志、监控配置、运维文档 | T-16、T-17 | 对库存不足、异常补偿、回调验签失败、状态冲突有可观测记录 | 未开始 |
