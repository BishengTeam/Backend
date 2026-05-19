# 积分模块 TODO 任务表

> 编制日期：2026-05-19  
> 对应接口：`GET /api/points`、`GET /api/points/history`、`POST /api/points/claim`、`POST /api/points/redeem`  
> 环境依赖：不依赖微信环境；依赖本地/测试 PostgreSQL  
> 现有基础：`user_points`、`points_history` ORM 模型已存在

---

## 1. 状态说明

- `未开始`：尚未进入实现。
- `进行中`：已有代码或文档改动，但未完成验收。
- `已完成`：代码、迁移、测试、接口文档均满足本行验收标准。
- `阻塞`：依赖业务规则、外部配置或测试环境。

---

## 2. 第一阶段：最小可用积分能力

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| PTS-01 | P0 | 明确积分业务规则 | `docs/plan/`、业务口径 | 无 | 明确积分余额单位、允许负数与否、领取场景、领取周期、兑换类型、兑换扣减规则、历史记录 action_type 枚举 | 已完成 |
| PTS-02 | P0 | 检查并补强积分表约束 | `app/models/points.py`、Alembic | PTS-01 | `user_points.user_id` 唯一；余额非负；流水 amount 可正可负；必要索引齐全 | 已完成 |
| PTS-03 | P0 | 定义积分请求/响应 Schema | `app/schemas/points.py` | PTS-01 | 包含余额响应、流水分页响应、领取请求/响应、兑换请求/响应；字段命名符合规范 | 已完成 |
| PTS-04 | P0 | 实现积分余额查询 Service | `app/services/points.py` | PTS-02、PTS-03 | 用户无积分账户时返回 0 或自动初始化，口径统一；不直接访问 HTTP 对象 | 已完成 |
| PTS-05 | P0 | 实现积分流水查询 Service | `app/services/points.py` | PTS-02、PTS-03 | 支持分页，按创建时间倒序；只返回当前用户流水 | 已完成 |
| PTS-06 | P0 | 实现积分发放通用能力 | `app/services/points.py` | PTS-01、PTS-02 | `grant_points()` 支持余额增加与正数流水同事务；可供签到、题库任务、后台调整、支付返积分等场景复用 | 已完成 |
| PTS-07 | P0 | 实现用户领取积分能力 | `app/services/points.py` | PTS-01、PTS-06 | `claim_points()` 按 scene/周期幂等发放；重复领取不重复加分；并发领取不重复发放 | 已完成 |
| PTS-08 | P0 | 实现积分兑换事务能力 | `app/services/points.py` | PTS-01、PTS-02 | 兑换扣减与流水写入在同一事务；余额不足抛 `BusinessException`；并发下不透支 | 已完成 |
| PTS-09 | P0 | 实现 `GET /api/points` | `app/api/points.py` | PTS-04 | 路由只做参数收集与调用 service；显式 `response_model` | 已完成 |
| PTS-10 | P0 | 实现 `GET /api/points/history` | `app/api/points.py` | PTS-05 | 支持 `page`、`page_size`；统一分页响应格式 | 已完成 |
| PTS-11 | P0 | 实现 `POST /api/points/claim` | `app/api/points.py` | PTS-07 | 支持用户按领取场景领取积分；返回领取结果、发放积分、领取后余额和流水 ID | 已完成 |
| PTS-12 | P0 | 实现 `POST /api/points/redeem` | `app/api/points.py` | PTS-08 | 支持考试费减免/课程兑换的最小闭环；返回扣减后余额和流水 ID | 已完成 |
| PTS-13 | P0 | 注册积分路由 | `app/api/__init__.py`、`app/main.py` | PTS-09~PTS-12 | `/api/points*` 可被应用挂载并出现在 OpenAPI | 已完成 |

---

## 3. 第二阶段：测试与验收

| ID | 优先级 | TODO | 涉及范围 | 前置依赖 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| PTS-14 | P0 | 补充本地无数据库测试 | `tests/unit/` | PTS-03~PTS-13 | 覆盖 Schema 校验、路由 `response_model`、Service 分层约束、领取幂等结构、兑换异常分支结构 | 已完成 |
| PTS-15 | P0 | 补充 PostgreSQL 集成测试 | `tests/integration/db/` | PTS-04~PTS-12 | 覆盖账户初始化、流水分页、领取幂等、重复领取不加分、余额不足、兑换扣减+流水同事务、并发不透支 | 已完成 |
| PTS-16 | P1 | 补充 HTTP 冒烟测试 | `tests/` 或脚本 | PTS-09~PTS-15 | 四个接口在本地服务下可调用，认证和响应格式正确 | 已完成 |
| PTS-17 | P1 | 更新接口状态和模块文档 | `docs/接口列表.md`、相关计划文档 | PTS-14~PTS-16 | 根据测试证据将接口标记为 `🧪` 或 `✅`，文案区分无 DB 测试和 DB 集成测试 | 已完成 |

---

## 4. 关键验收场景

| 场景 | 预期 |
| --- | --- |
| 新用户查询积分 | 返回余额 0，不报错 |
| 有积分用户查询余额 | 返回 `user_points.balance` |
| 查询积分历史 | 只返回当前用户流水，分页稳定 |
| 首次领取积分 | 增加余额，写入一条正数流水，返回领取后余额 |
| 重复领取积分 | 幂等返回已有领取结果，不重复增加余额 |
| 并发领取积分 | 同一用户同一 scene/周期只发放一次 |
| 余额足够兑换 | 扣减余额，写入一条负数流水，返回扣减后余额 |
| 余额不足兑换 | 抛 `BusinessException`，余额和流水均不变 |
| 并发兑换 | 数据库事务和行锁保证余额不透支 |

---

## 5. 暂定业务口径

- 积分余额使用整数，不允许负数。
- 历史流水正数表示获得积分，负数表示消耗积分。
- 领取积分入口为 `POST /api/points/claim`，由用户主动触发；系统内部发放复用 `grant_points()`。
- 领取场景暂定：`daily_checkin`、`quiz_task`、`new_user`、`activity`。
- 同一用户同一领取场景在同一业务周期内幂等，不能重复加分。
- `action_type` 暂定枚举：`claim_daily_checkin`、`claim_quiz_task`、`claim_new_user`、`claim_activity`、`redeem_exam_discount`、`redeem_course`、`adjust`。
- 考试费减免规则按开发计划先采用“月减 50，上限 100”的口径；若业务确认不同，以 PTS-01 输出为准。
