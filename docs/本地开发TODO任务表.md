# 本地开发 TODO 任务表

> 编制日期：2026-05-14
> 背景：甲方暂未提供云服务器，全部开发工作在本地环境进行
> 本地环境：Windows 10 + Python FastAPI + PostgreSQL (本地) + Redis (本地)

---

## 本地可完成 vs 需延后事项总览

| 类别 | 可本地完成 | 需云服务器延后 |
|------|-----------|---------------|
| 项目脚手架 | 全部 | — |
| 数据库设计 | DDL/迁移/CRUD | — |
| 22 个 API 模块 | 全部代码开发 + 单元测试 | — |
| 微信登录 | code→openid 逻辑代码 | 真实小程序 code 验证 |
| 微信支付 | 统一下单 + 回调代码 | 沙箱/正式环境联调 |
| OSS 存储 | 本地文件存储替代 | CDN/CORS/域名绑定 |
| 订阅消息 | 推送逻辑代码 | 真实模板消息发送 |
| 前端联调 | localhost 联调 | 微信小程序合法域名 |

---

## 第一阶段：本地环境搭建（5月14日 — 5月15日）

### 1.1 本地基础设施安装

- [ ] 安装 PostgreSQL 本地实例（端口 5432）
- [ ] 安装 Redis 本地实例（端口 6379），或使用 Memurai 替代
- [ ] 创建 Python 虚拟环境（venv），Python 3.11+
- [ ] 配置 `.env` 本地开发环境变量（数据库连接串/Redis）

### 1.2 FastAPI 项目脚手架

- [ ] 项目目录结构创建（参考下方目录结构）
- [ ] FastAPI 应用初始化 + CORS 中间件配置
- [ ] 配置管理模块（pydantic-settings 多环境）
- [ ] 日志系统搭建（logging / loguru）
- [ ] 全局异常处理中间件
- [ ] 数据库连接池配置（asyncpg / SQLAlchemy async）
- [ ] Redis 客户端封装
- [ ] Alembic 迁移工具配置
- [ ] 统一响应格式封装（`{code, message, data}`）
- [ ] Git 仓库初始化 + `.gitignore`

```
项目目录结构
backend/
├── app/
│   ├── api/              # 路由层（按模块分文件）
│   ├── core/             # 核心配置（config/security/logging）
│   ├── db/               # 数据库（models/session/alembic）
│   ├── middleware/       # 中间件（auth/cors/rate_limit）
│   ├── schemas/          # Pydantic 请求/响应模型
│   ├── services/         # 业务逻辑层
│   ├── integrations/     # 外部服务对接（微信/存储）
│   ├── utils/            # 工具函数
│   └── main.py           # 应用入口
├── alembic/              # 数据库迁移
├── tests/                # 测试用例
├── scripts/              # 脚本工具
├── uploads/              # 本地文件存储（生产用 OSS 替代）
├── .env.development      # 本地开发环境变量
├── .env.example          # 环境变量示例
└── requirements.txt
```

### 1.3 数据库设计

- [ ] 编写所有表的 SQLAlchemy ORM Model（13 张表，见下方清单）
- [ ] 创建 Alembic 初始 Migration（DDL）
- [ ] 执行 Migration，验证所有表创建成功
- [ ] 编写种子数据脚本（价格配置 / 认证信息 / 题库分类的初始数据）

**13 张核心表**：user / order / conversation / price_config / certification / course / course_enrollment / quiz_category / quiz_question / quiz_record / quiz_checkin / user_points / points_history / coupon / user_coupon / agreement / competition_reg / ticket

---

## 第二阶段：核心 API 开发（5月15日 — 5月29日）

### 2.1 用户服务模块 `B-01`（P0）— 预计 5月15-16日

- [ ] `POST /api/auth/login` — 微信 code 换取 openid + 签发 JWT（先用 mock code 测试）
- [ ] `GET /api/user/profile` — 获取用户资料
- [ ] `PUT /api/user/profile` — 更新用户资料（修改次数限制逻辑）
- [ ] `DELETE /api/user/account` — 账号注销
- [ ] 手机号解密存储逻辑（getPhoneNumber 敏感数据解密 — 代码完成，待真实环境验证）
- [ ] JWT Token 签发 + 刷新 + 过期处理 + 认证中间件
- [ ] 登录海报弹窗配置接口
- [ ] 考试意向字段采集

### 2.2 客服对话模块 `B-02`（P1）— 预计 5月16-17日

> 当前实现消息收发骨架 + 快速问题配置，预留 AI 智能客服转发扩展点。

- [ ] `POST /api/chat` — 消息接收 + 会话存储（Redis），预留 `ChatBackend` 接口
- [ ] `GET /api/chat/stream` — SSE 流式响应骨架
- [ ] `GET /api/quick-questions` — 推荐问题列表（后台可配置）
- [ ] 对话上下文管理（Redis 会话存储）

### 2.3 认证列表 + 报名缴费 + 支付（P0）— 预计 5月17-22日

#### 认证列表 `B-07`
- [ ] `GET /api/certifications` — 多认证类型列表接口
- [ ] 考试代码与中文全称自动匹配映射表
- [ ] `GET /api/cert/export` — 报名信息导出 Excel
- [ ] 学信网验证引导数据接口

#### 报名缴费 `B-04`
- [ ] `POST /api/orders` — 订单创建（含表单校验）
- [ ] 订单状态机（待支付→已支付→已完成/已退款）
- [ ] `GET /api/orders` — 用户订单列表（分页+状态筛选）
- [ ] `GET /api/orders/{id}` — 订单详情
- [ ] NISP 特殊流程：先支付后填表逻辑
- [ ] 深信服优惠券自动下发 + 动态验证码对接代码

#### 价格配置 `B-05`
- [ ] PRICE_CONFIG 表 CRUD API
- [ ] 多认证类型 × 多用户类型价格映射查询
- [ ] 后台价格配置管理接口

#### 微信支付 `B-09`（代码完成，真实联调延后）
- [ ] `POST /api/payment/prepay` — 微信支付统一下单代码
- [ ] 支付回调处理代码（幂等 + 签名验证 + 订单状态更新）
- [ ] 订阅消息推送代码
- [ ] access_token 自动刷新管理（Redis）
- [ ] ⚠ 本地 Mock 支付回调测试用端点

### 2.4 课程 + 安全（P0）— 预计 5月22-25日

#### 课程/视频服务 `B-13`
- [ ] `GET /api/courses` — 课程列表（分类筛选）
- [ ] `GET /api/courses/:id` — 课程详情（介绍/时间/班次/班主任联系方式）
- [ ] `POST /api/courses/enroll` — 课程报名缴费
- [ ] `GET /api/courses/my` — 我的课程列表 + 学习权限状态
- [ ] 视频上传 URL 签名生成（本地文件存储替代 OSS）

#### 安全模块
- [ ] XSS/SQL 注入防护中间件
- [ ] 请求频率限制（Rate Limiting，基于 Redis）
- [ ] 敏感操作日志记录（支付/退款/管理员）

### 2.5 联调 + 核心 API 自测 — 预计 5月25-27日

- [ ] 核心 API 集成测试（报名流程 / 支付模拟 / 课程）
- [ ] Bug 修复
- [ ] **M2 自查清单**：5 大模块 API 可本地调用

---

## 第三阶段：全部 API 开发（6月1日 — 6月12日）

### 3.1 专区内容服务 `B-08`（P1）— 预计 6月1-3日

- [ ] `GET /api/zones` — 专区聚合页数据
- [ ] `GET /api/zones/{type}` — 认证/学习/竞赛/活动/就业 专区列表
- [ ] `CRUD /api/admin/zones` — 后台专区内容管理

### 3.2 竞赛统计 `B-19`（P2）— 预计 6月3-4日

- [ ] `GET /api/competition/stats` — 报名人数统计
- [ ] `GET /api/competition/export` — Excel 导出

### 3.3 题库中心 `B-14`（P1）— 预计 6月4-7日

- [ ] `GET /api/quiz/categories` — 题库分类树
- [ ] `GET /api/quiz/questions` — 题目列表（分页/分类筛选）
- [ ] `POST /api/quiz/submit` — 答题提交 + 即时判分
- [ ] `CRUD /api/quiz/wrong-book` — 错题本
- [ ] `CRUD /api/quiz/collections` — 收藏
- [ ] `GET/POST /api/quiz/checkin` — 打卡记录与连续天数统计
- [ ] 题库批量导入工具（Excel → DB，含格式校验）
- [ ] 模考/测评/刷题挑战模式配置

### 3.4 分享服务 `B-20`（P2）— 预计 6月7-8日

- [ ] `POST /api/share` — 分享链接生成
- [ ] `GET /api/share/:code` — 分享记录追踪
- [ ] 拼团接口预留

### 3.5 积分 + 优惠券（P1）— 预计 6月8-10日

#### 积分 `B-15`
- [ ] `GET /api/points` — 积分余额查询
- [ ] `POST /api/points/redeem` — 积分兑换
- [ ] `GET /api/points/history` — 积分记录
- [ ] 积分规则引擎

#### 优惠券 `B-16`
- [ ] `GET /api/coupons` — 用户优惠券列表
- [ ] `POST /api/coupons/assign` — 预设库自动下发
- [ ] `POST /api/coupons/verify` — 核销验证
- [ ] 后台优惠券库管理

### 3.6 客服 + 协议（P1）— 预计 6月10-11日

#### 人工客服 `B-06`
- [ ] 转人工触发逻辑
- [ ] `POST /api/tickets` — 工单创建 + 老师分配
- [ ] 会话状态管理
- [ ] 老师信息配置接口

#### 电子协议 `B-17`
- [ ] `GET /api/agreements` — 协议列表
- [ ] `POST /api/agreements` — 协议创建
- [ ] `PUT /api/agreements/:id/sign` — 上传签名
- [ ] 协议状态流转引擎
- [ ] 后台协议管理

### 3.7 OSS + NISP 专项（P1）— 预计 6月11-12日

- [ ] `POST /api/upload` — 文件上传（**本地文件存储**）
- [ ] `GET /api/media/:id` — 获取文件访问 URL
- [ ] NISP 身份证图片→PDF 转换
- [ ] 姓名拼音自动生成
- [ ] 学籍报告查询通道配置

### 3.8 管理后台 API + 全量自测 — 6月12日

#### 管理后台 `B-23`
- [ ] 用户管理 CRUD
- [ ] 订单管理 CRUD
- [ ] 数据统计分析接口
- [ ] 系统配置接口

#### M3 自测
- [ ] 全量 API 本地自测
- [ ] 模块间交叉验证
- [ ] 回归测试

---

## 第四阶段：AI 智能客服预留

> AI 智能客服不在后端交付范围内，后端仅完成对接骨架。

- [ ] `ChatBackend` 抽象接口定义（`integrations/chat_backend.py`），当前实现为人工客服转发
- [ ] SSE 流式响应管道就绪，后续 AI 服务接入时无需改动 API 层
- [ ] 对话表 `conversation` 预留 `backend_type` 字段（manual / ai）

---

## 第五阶段：测试与上线准备（6月15日 — 6月24日）

### 5.1 后端测试（6月15-17日）

- [ ] 22 个模块核心接口单元测试（覆盖率 ≥ 80%）
- [ ] 支付全链路模拟测试
- [ ] 题库判分测试（正误判断 / 错题收录 / 打卡连续天数）
- [ ] 积分/优惠券测试（兑换上限 / 下发幂等 / 核销流转）
- [ ] 协议流转测试（全状态机覆盖）
- [ ] NISP 专项测试（先付后填 / 身份证→PDF / 拼音生成）
- [ ] 本地并发压力测试（支付/题库提交/报名）

### 5.2 性能优化（6月17-18日）

- [ ] 数据库索引优化（高频查询字段）
- [ ] Redis 缓存策略实现
- [ ] 慢查询排查与优化

### 5.3 云环境迁移准备（6月19日之后，待甲方提供服务器）

- [ ] 整理云服务器部署 checklist
- [ ] 准备 Docker / Docker Compose 部署配置
- [ ] 准备 Nginx 配置文件模板
- [ ] 环境变量差异对比表（本地 vs 生产）
- [ ] 微信支付正式环境切换准备
- [ ] OSS 迁移脚本（本地存储 → 云 OSS）
- [ ] 生产数据初始化脚本

---

## 本地开发每日速查

| 日期 | 主要任务 | 产出 |
|------|---------|------|
| 5/14 | 环境安装 + 项目脚手架 + 数据库设计 | 仓库可运行 + DDL 就绪 |
| 5/15 | 用户模块 B-01 | 登录/资料/认证 可用 |
| 5/16 | 客服对话 B-02 | 消息/会话存储/推荐问题 |
| 5/17-18 | 认证列表 B-07 + 价格 B-05 | 认证/价格 API 就绪 |
| 5/19-21 | 报名缴费 B-04 | 订单/状态机/NISP/深信服流程 |
| 5/22-23 | 微信支付 B-09（代码）+ 课程 B-13 | 支付代码 + 课程 API |
| 5/24-25 | 安全模块 + 核心联调 | 安全中间件 + 5 大模块联调 |
| 5/26-27 | Bug 修复 + M2 自查 | 核心 API 可调用 |
| 6/1-3 | 专区 B-08 + 竞赛 B-19 | 专区/竞赛 API |
| 6/4-7 | 题库中心 B-14 | 题库/错题本/收藏/打卡 |
| 6/7-8 | 分享 B-20 | 分享链接/追踪 |
| 6/8-10 | 积分 B-15 + 优惠券 B-16 | 积分/优惠券 API |
| 6/10-11 | 客服 B-06 + 协议 B-17 | 工单/协议流转 |
| 6/11-12 | OSS B-18 + 管理后台 B-23 + M3 自查 | 全部 API 就绪 |
| 6/15-17 | 全量测试 | 覆盖率 ≥ 80% |
| 6/17-18 | 性能优化 | 慢查询/缓存 |
| 6/19+ | 待服务器就绪后迁移 | 云部署 |

---

## 本地开发注意事项

1. **微信相关联调**：微信登录、支付、订阅消息的代码逻辑正常编写，使用 Mock 数据验证；`appid`/`secret` 配置放在 `.env` 中，待甲方提供后填入真实值
2. **文件存储**：使用本地 `./uploads/` 目录替代 OSS，抽象出 `StorageService` 接口，后续只需替换实现即可切换到云 OSS
3. **AI 智能客服**：B-02 模块通过 `ChatBackend` 抽象接口预留扩展点，当前实现为简单消息存储，后续注入 AI 服务实现即可，无需改动 API 层
4. **HTTPS 证书**：本地开发使用 HTTP，不涉及 SSL 配置
5. **域名白名单**：前端联调时使用 `localhost` + 端口，无需配置微信合法域名
6. **每天结束时**：在完成任务前打勾 `[x]`，保持进度可见
