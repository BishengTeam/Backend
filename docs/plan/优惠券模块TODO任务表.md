# 优惠券模块 TODO 任务表

> 编制日期：2026-05-19  
> 对应接口：`GET /api/coupons`、`POST /api/coupons/assign`、`POST /api/coupons/verify`  
> 环境依赖：不依赖微信环境；依赖本地/测试 PostgreSQL  
> 现有基础：`coupon`、`user_coupon` ORM 模型已存在

---

## 1. 状态说明

- `未开始`：尚未进入实现。
- `进行中`：已有代码或文档改动，但未完成验收。
- `已完成`：代码、迁移、测试、接口文档均满足本行验收标准。
- `阻塞`：依赖业务规则、外部配置或测试环境。

---

## 2. 第一阶段：最小可用优惠券能力

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| CPN-01 | P0 | 明确优惠券业务规则 | `docs/plan/`、业务口径 | 无 | 明确券类型、金额/折扣含义、有效期、适用门槛、下发场景、核销后状态 | 未开始 |
| CPN-02 | P0 | 检查并补强优惠券表约束 | `app/models/coupon.py`、Alembic | CPN-01 | `coupon.code` 唯一；`user_coupon` 防重复下发；状态和金额字段具备必要约束 | 未开始 |
| CPN-03 | P0 | 定义优惠券请求/响应 Schema | `app/schemas/coupon.py` | CPN-01 | 包含用户券列表响应、自动下发请求/响应、核销验证请求/响应 | 未开始 |
| CPN-04 | P0 | 实现用户优惠券列表 Service | `app/services/coupon.py` | CPN-02、CPN-03 | 仅返回当前用户优惠券；支持按状态筛选；返回券码、金额、门槛、有效期 | 未开始 |
| CPN-05 | P0 | 实现优惠券自动下发 Service | `app/services/coupon.py` | CPN-01、CPN-02 | 根据业务场景选择可用券；重复下发幂等；下发与记录写入同事务 | 未开始 |
| CPN-06 | P0 | 实现优惠券核销验证 Service | `app/services/coupon.py` | CPN-01、CPN-02 | 校验归属、状态、有效期、订单金额门槛；返回可抵扣金额，不直接修改订单 | 未开始 |
| CPN-07 | P0 | 实现 `GET /api/coupons` | `app/api/coupons.py` | CPN-04 | 路由只做参数收集与调用 service；显式 `response_model` | 未开始 |
| CPN-08 | P0 | 实现 `POST /api/coupons/assign` | `app/api/coupons.py` | CPN-05 | 支持预设库自动下发；重复请求返回已有用户券，不重复创建 | 未开始 |
| CPN-09 | P0 | 实现 `POST /api/coupons/verify` | `app/api/coupons.py` | CPN-06 | 返回核销是否可用、抵扣金额、不可用原因；不依赖微信支付 | 未开始 |
| CPN-10 | P0 | 注册优惠券路由 | `app/api/__init__.py`、`app/main.py` | CPN-07~CPN-09 | `/api/coupons*` 可被应用挂载并出现在 OpenAPI | 未开始 |

---

## 3. 第二阶段：与订单/支付的边界

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| CPN-11 | P1 | 明确优惠券与订单的集成点 | `app/services/order.py`、计划文档 | CPN-06 | 明确当前阶段只做核销验证，还是创建订单时写入优惠券抵扣信息 | 未开始 |
| CPN-12 | P1 | 预留核销状态流转 | `app/services/coupon.py` | CPN-11 | 状态至少包含 `unused`、`used`、`expired`；后续支付成功后可落库核销 | 未开始 |
| CPN-13 | P1 | 补充过期券处理策略 | `app/services/coupon.py`、脚本可选 | CPN-01 | 列表和核销均能识别过期券；是否批量标记 `expired` 有明确口径 | 未开始 |

---

## 4. 第三阶段：测试与验收

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| CPN-14 | P0 | 补充本地无数据库测试 | `tests/unit/` | CPN-03~CPN-10 | 覆盖 Schema 校验、路由 `response_model`、Service 分层约束、幂等/核销结构约束 | 未开始 |
| CPN-15 | P0 | 补充 PostgreSQL 集成测试 | `tests/integration/db/` | CPN-04~CPN-09 | 覆盖列表用户隔离、自动下发幂等、有效期校验、金额门槛、已使用券不可核销 | 未开始 |
| CPN-16 | P1 | 补充 HTTP 冒烟测试 | `tests/` 或脚本 | CPN-07~CPN-15 | 三个接口在本地服务下可调用，认证和响应格式正确 | 未开始 |
| CPN-17 | P1 | 更新接口状态和模块文档 | `docs/接口列表.md`、相关计划文档 | CPN-14~CPN-16 | 根据测试证据将接口标记为 `🧪` 或 `✅`，文案区分无 DB 测试和 DB 集成测试 | 未开始 |

---

## 5. 关键验收场景

| 场景 | 预期 |
| --- | --- |
| 用户查询优惠券列表 | 只返回当前用户的券，支持按状态筛选 |
| 自动下发优惠券 | 找到符合场景的券并创建 `user_coupon` |
| 重复自动下发 | 不重复创建，返回已有用户券 |
| 核销未归属券 | 返回不可用或抛业务异常 |
| 核销过期券 | 返回不可用原因 |
| 核销未达门槛券 | 返回不可用原因 |
| 核销可用券 | 返回可抵扣金额和用户券 ID |

---

## 6. 暂定业务口径

- 优惠券状态暂定：`unused`、`used`、`expired`。
- 优惠券类型暂定：`fixed` 表示固定金额抵扣；如需折扣券，后续扩展 `percent`。
- `POST /api/coupons/verify` 当前只做可用性验证，不直接改订单、不触发微信支付。
- `POST /api/coupons/assign` 当前按场景自动下发，优先支持深信服场景；具体场景枚举由 CPN-01 确认。
