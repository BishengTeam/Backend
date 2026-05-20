# 管理后台 API 开发计划

> 编制日期：2026-05-19
> 说明：管理后台 API 与 C 端小程序 API 在同一 FastAPI 应用中，共享 DB/Redis，认证独立。

---

## 一、接口全量清单（13 组，约 38 个接口）

| 组 | 前缀 | 数量 | 复用 C 端 | 说明 |
|------|------|:---:|:---:|------|
| 认证 | `/admin/auth` | 1 | 否 | 账号密码登录 |
| 数据看板 | `/admin/statistics` | 1 | 否 | 聚合查询 |
| 用户管理 | `/admin/users` | 2 | user 表已有 | 列表 + 详情/封禁 |
| 订单管理 | `/admin/orders` | 3 | order 表已有 | 列表 + 详情 + 退款 |
| 课程管理 | `/admin/courses` | 4 | course 表已有 | CRUD |
| 认证管理 | `/admin/certifications` | 3 | certification 表已有 | CRUD |
| 价格配置 | `/admin/prices` | 3 | price_config 表已有 | CRUD |
| 题库管理 | `/admin/quiz` | 5 | quiz 模块全部完成 | 分类 CRUD + 题目 CRUD + 导入 |
| 专区内容 | `/admin/zones` | 4 | 新表 | CRUD + 排序上下线 |
| 优惠券管理 | `/admin/coupons` | 3 | coupon 表已有 | CRUD + 批量创建 |
| 协议管理 | `/admin/agreements` | 3 | agreement 表已有 | 模板 CRUD + 审核盖章 |
| 工单管理 | `/admin/tickets` | 2 | ticket 表已有 | 列表 + 处理 |
| 系统设置 | `/admin/settings` | 3 | 新表 | 管理员 CRUD + 角色 |

---

## 二、开发批次

### 第〇批：基础设施（Day 1 上午）

| # | 接口 | 说明 |
|------|------|------|
| 1 | `POST /admin/auth/login` | 账号密码登录，返回 JWT（type=admin，含 role） |

**工程改动**：
- 新建 `app/models/admin_user.py`（id, username, password_hash, role, is_active）
- 新建 `app/schemas/admin.py`（AdminLoginRequest, AdminLoginResponse）
- 新建 `app/services/admin_auth.py`
- 新建 `app/api/admin/__init__.py` + `app/api/admin/auth.py`
- 新建 `app/middleware/auth.py` 追加 `get_current_admin` 依赖
- Alembic 迁移

---

### 第一批：有表有 C 端，补写操作（Day 1 下午 — Day 2）

| # | 接口 | 复用基础 | 说明 |
|------|------|------|------|
| 2 | `GET /admin/users` | user 表 | 列表 + 分页 + openid/phone 筛选 |
| 3 | `PUT /admin/users/{id}` | user 表 | 封禁/解封（is_active）+ 查看详情 |
| 4 | `GET /admin/orders` | order 表 | 列表 + 状态筛选 + 时间筛选 + 分页 |
| 5 | `GET /admin/orders/{id}` | order 表 | 详情（含 transaction_id、paid_at） |
| 6 | `POST /admin/orders/{id}/refund` | order 表 | 退款（status→refunded），预留微信退款对接点 |
| 7 | `POST /admin/courses` | course 表 | 创建课程 |
| 8 | `PUT /admin/courses/{id}` | course 表 | 编辑课程（含班次 JSON） |
| 9 | `DELETE /admin/courses/{id}` | course 表 | 下架（is_active=false，非物理删除） |
| 10 | `GET /admin/courses` | course 表 | 全量列表（含已下架），C 端只返回上架 |
| 11 | `POST /admin/certifications` | certification 表 | 创建认证类型 |
| 12 | `PUT /admin/certifications/{id}` | certification 表 | 编辑认证类型 |
| 13 | `POST /admin/prices` | price_config 表 | 新增价格行 |
| 14 | `PUT /admin/prices/{id}` | price_config 表 | 修改价格 |
| 15 | `DELETE /admin/prices/{id}` | price_config 表 | 停用价格 |

---

### 第二批：题库 + 专区（Day 3 — Day 4）

| # | 接口 | 复用基础 | 说明 |
|------|------|------|------|
| 16 | `POST /admin/quiz/categories` | quiz_category 表 | 创建分类 |
| 17 | `PUT /admin/quiz/categories/{id}` | quiz_category 表 | 编辑分类 |
| 18 | `DELETE /admin/quiz/categories/{id}` | quiz_category 表 | 删除分类 |
| 19 | `POST /admin/quiz/questions` | quiz_question 表 | 创建题目 |
| 20 | `PUT /admin/quiz/questions/{id}` | quiz_question 表 | 编辑题目 |
| 21 | `DELETE /admin/quiz/questions/{id}` | quiz_question 表 | 删除题目 |
| 22 | `POST /admin/quiz/import` | quiz_question 表 | Excel 批量导入 |
| 23 | `POST /admin/zones` | 新表 | 创建专区内容 |
| 24 | `PUT /admin/zones/{id}` | 新表 | 编辑 + 排序 |
| 25 | `DELETE /admin/zones/{id}` | 新表 | 下架 |
| 26 | `GET /admin/zones` | 新表 | 全量列表 |

**工程改动**：
- 新建 `app/models/zone.py`（zone_type, title, cover_url, description, link_url, sort_order, is_active）
- 对应 schema / service / api
- Alembic 迁移

---

### 第三批：优惠券 + 协议 + 工单（Day 5 — Day 6）

| # | 接口 | 复用基础 | 说明 |
|------|------|------|------|
| 27 | `GET /admin/coupons` | coupon 表 | 优惠券库列表 |
| 28 | `POST /admin/coupons` | coupon 表 | 批量创建 + 单个创建 |
| 29 | `DELETE /admin/coupons/{id}` | coupon 表 | 作废 |
| 30 | `GET /admin/agreements` | agreement 表 | 协议列表 |
| 31 | `POST /admin/agreements` | agreement 表 | 创建模板（富文本 content） |
| 32 | `PUT /admin/agreements/{id}/review` | agreement 表 | 审核 + 盖章 |
| 33 | `GET /admin/tickets` | ticket 表 | 工单列表 + 状态筛选 |
| 34 | `PUT /admin/tickets/{id}` | ticket 表 | 处理工单（分配老师/关闭） |

---

### 第四批：数据看板 + 系统设置 + 竞赛（Day 7）

| # | 接口 | 复用基础 | 说明 |
|------|------|------|------|
| 35 | `GET /admin/statistics/dashboard` | 多表聚合 | 总用户数/订单数/营收/转化率（近 30 天） |
| 36 | `GET /admin/settings/admins` | admin_user 表 | 管理员列表 |
| 37 | `POST /admin/settings/admins` | admin_user 表 | 创建管理员 |
| 38 | `PUT /admin/settings/admins/{id}` | admin_user 表 | 修改角色/启用禁用 |
| 39 | `GET /admin/competition/export` | competition_reg 表 | Excel 导出 |

---

## 三、认证方案

```
POST /admin/auth/login
Body: { username, password }
  → 查 admin_user 表
  → verify password_hash
  → JWT 签发：{ type: "admin", admin_id, role, exp }
  → 返回 { access_token, admin { id, username, role } }

get_current_admin 依赖：
  → decode JWT
  → 校验 type == "admin"
  → 查 admin_user 表，校验存在且 is_active
  → 返回 AdminUser

管理员角色：
  super_admin | content_editor | customer_service | finance | auditor
```

---

## 四、文件规划

```
app/
├── api/
│   └── admin/
│       ├── __init__.py          # 聚合 admin router（prefix=/admin）
│       ├── auth.py              # POST /admin/auth/login
│       ├── users.py             # GET /admin/users  PUT /admin/users/{id}
│       ├── orders.py            # GET /admin/orders  GET/POST /admin/orders/{id}*
│       ├── courses.py           # CRUD /admin/courses
│       ├── certifications.py    # POST /admin/certifications  PUT /admin/certifications/{id}
│       ├── prices.py            # POST /admin/prices  PUT /admin/prices/{id}  DELETE
│       ├── quiz.py              # CRUD /admin/quiz/categories  CRUD /admin/quiz/questions  POST import
│       ├── zones.py             # CRUD /admin/zones
│       ├── coupons.py           # CRUD /admin/coupons
│       ├── agreements.py        # CRUD /admin/agreements  PUT review
│       ├── tickets.py           # GET /admin/tickets  PUT /admin/tickets/{id}
│       ├── statistics.py        # GET /admin/statistics/dashboard
│       ├── competition.py       # GET /admin/competition/export
│       └── settings.py          # CRUD /admin/settings/admins
├── models/
│       ├── admin_user.py        # 管理员表
│       └── zone.py              # 专区内容表
├── schemas/
│       ├── admin.py             # AdminAuth, AdminUserResponse
│       └── zone.py              # ZoneCreate, ZoneResponse
├── services/
│       ├── admin_auth.py
│       ├── admin_user.py
│       ├── admin_order.py       # C 端 order service 不够用，管理侧独立
│       ├── admin_course.py
│       ├── admin_quiz.py
│       ├── zone.py
│       └── admin_statistics.py
└── middleware/
        └── auth.py              # 追加 get_current_admin
```

只新建 2 个 model（admin_user、zone），其余全部复用现有表。C 端 service 不改动，admin service 走独立文件，避免耦合。

---

## 五、建议启动顺序

```
Day 1 上午   第〇批：admin 认证（铺路）
Day 1 下午   第一批前半：用户管理 + 认证管理 + 价格配置（最轻）
Day 2        第一批后半：订单管理 + 课程管理
Day 3-4      第二批：题库管理 + 专区内容
Day 5-6      第三批：优惠券 + 协议 + 工单
Day 7        第四批：数据看板 + 系统设置 + 竞赛导出
```

总计 7 天，39 个接口，跨 2 个新表 + 10 个现有表。
