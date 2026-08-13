# 接口审计 — 批次2：核心实体 Schema 统一表达

> 归档说明：2026-08-09 移入历史区，仅保留当时审计结论，不作为当前接口契约。
> 日期：2026-06-07
> 依据：[接口审计标准](../../../接口审计标准.md) 反模式 #3（表达分裂）
> 计划：[接口审计计划](接口审计计划.md)

---

## 审计方法

对 9 个核心实体，列出所有引用该实体 Schema 的接口，逐字段对比用户端和管理端的 Schema 类型是否一致。

---

## 1. User / UserIdentity

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `UserProfile` | `schemas/user.py:8` | 4 | `POST /api/auth/login` → `LoginResponse.user` |
| `UserProfileDetail` | `schemas/user.py:53` | 14 | `GET /api/user/profile` |
| `UserIdentityResponse` | `schemas/user.py:36` | 10 | `POST/GET /api/user/identity` |
| `AdminUserListItem` | `schemas/admin.py:65` | 5 | `GET /admin/users` 列表 + 详情 |

### 对比

| 字段 | UserProfile | UserProfileDetail | AdminUserListItem |
|------|:----------:|:-----------------:|:-----------------:|
| id | ✅ | ✅ | ✅ |
| openid | ✅ | ✅ | ✅ |
| phone | ✅ | ✅ | ✅ |
| email | — | ✅ | — |
| real_name | — | ✅ | — |
| id_card | — | ✅ | — |
| user_type | — | ✅ | — |
| gender | — | ✅ | — |
| education | — | ✅ | — |
| school | — | ✅ | — |
| major | — | ✅ | — |
| organization | — | ✅ | — |
| identity_status | — | ✅ | — |
| is_active | — | — | ✅ |
| created_at | ✅ | ✅ | ✅ |

**判定**：✅ 无表达分裂。4 个 Schema 对应 4 个不同使用场景：
- `UserProfile`：登录响应中的最小用户卡片
- `UserProfileDetail`：用户自己的完整资料
- `UserIdentityResponse`：实名认证信息（独立实体）
- `AdminUserListItem`：管理端列表摘要

各 Schema 字段集不同是场景驱动的合理差异。

---

## 2. Order

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `OrderResponse` | `schemas/order.py:49` | 15 | `GET /api/orders` 列表，`GET /admin/orders` 列表 |
| `OrderDetailResponse` | `schemas/order.py:70` | 18 | `GET /api/orders/{id}` 详情，`GET /admin/orders/{id}` 详情 |

### 对比

| 字段 | OrderResponse | OrderDetailResponse |
|------|:------------:|:-------------------:|
| id, cert_type, candidate_name, candidate_phone, candidate_idcard, price, status, out_trade_no, inventory_id, expires_at, closed_at, close_reason, created_at, extra_data, attachments | ✅ | ✅ |
| transaction_id | — | ✅ |
| paid_at | — | ✅ |
| updated_at | — | ✅ |

**判定**：✅ 无表达分裂。
- `OrderResponse` 和 `OrderDetailResponse` 是列表摘要 / 详情视图的区别
- **用户端和管理端复用同一 Schema** — 这是最佳实践

---

## 3. Course

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `CourseListResponse` | `schemas/course.py:9` | 7 | `GET /api/courses` 列表 |
| `CourseDetailResponse` | `schemas/course.py:22` | 10 | `GET /api/courses/{id}` 详情 |
| `CourseBrief` | `schemas/zone.py:35` | 7 | `GET /api/zones` → `zones["study"].courses` |
| `AdminCourseListItem` | `schemas/admin_course.py:33` | 12 | `GET /admin/courses` 列表 |

### 对比

| 字段 | CourseListResponse | CourseDetailResponse | CourseBrief | AdminCourseListItem |
|------|:-----------------:|:-------------------:|:----------:|:-------------------:|
| id, title, category, description, cover_url, price, teacher_name | ✅ | ✅ | ✅ | ✅ |
| video_url | — | ✅ | — | ✅ |
| batches | — | ✅ | — | ✅ |
| teacher_contact | — | ✅ | — | ✅ |
| is_active | — | — | — | ✅ |
| created_at | — | — | — | ✅ |

**判定**：
- ⚠️ **重复定义**：`CourseBrief` ≡ `CourseListResponse` 字段完全一致但是两个独立 Pydantic 类型（低严重度，批次1已标记）
- ✅ 列表/详情差异合理：`CourseDetailResponse` 增加 video_url, batches, teacher_contact
- ✅ 用户端/管理端差异合理：`AdminCourseListItem` 增加管理字段 is_active, created_at

---

## 4. Certification

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `CertificationResponse` | `schemas/certification.py:9` | 7 | `GET /api/cert` 列表 |
| `CertificationDetailResponse` | `schemas/certification.py:22` | 9 | `GET /api/cert/{id}` 详情（继承 CertificationResponse） |
| `AdminCertificationListItem` | `schemas/admin_certification.py:6` | 9 | `GET /admin/certifications` 列表 |

### 对比

| 字段 | CertDetailResponse | AdminCertListItem |
|------|:------------------:|:-----------------:|
| id, name, chinese_name, code, vendor, requires_xuexin, pay_first, created_at | ✅ | ✅ |
| updated_at | ✅ | — |
| is_active | — | ✅ |

**判定**：⚠️ 轻微分裂。`CertificationDetailResponse`(继承基类) 有 `updated_at` 无 `is_active`，`AdminCertificationListItem`(独立定义) 有 `is_active` 无 `updated_at`。虽可解释（用户端看更新时间，管理端看上架状态），但两个 Schema 各自独立定义而非继承/复用同一个「详情」基类。

---

## 5. Quiz

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `QuizQuestionResponse` | `schemas/quiz.py:32` | 6 | `GET /api/quiz/*` 用户端题目 |
| `AdminQuizQuestionResponse` | `schemas/admin_quiz.py:7` | 9 | `GET /admin/quiz/*` 管理端题目 |

### 对比

| 字段 | QuizQuestionResponse | AdminQuizQuestionResponse |
|------|:--------------------:|:-------------------------:|
| id, category_id, question_type, question_text, options, explanation | ✅ | ✅ |
| correct_answer | — | ✅ |
| created_at, updated_at | — | ✅ |

**判定**：✅ 无表达分裂。`correct_answer` 不在用户端暴露是**安全设计**，不是表达分裂。

---

## 6. Activity ⚠️

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `ActivityResponse` | `schemas/activity.py:7` | 10 | `GET /api/activities` 列表 |
| `ActivityBrief` | `schemas/zone.py:56` | 8 | `GET /api/zones` → `zones["activity"].activities` |
| `AdminActivityListItem` | `schemas/admin_activity.py:28` | 9 | `GET /admin/activities` 列表 |

### 对比

| 字段 | ActivityResponse | ActivityBrief | AdminActivityListItem |
|------|:---------------:|:------------:|:---------------------:|
| id, title, cover_url, location, start_time, end_time, max_participants | ✅ | ✅ | ✅ |
| **description** | ✅ | ✅ | **❌ 缺失** |
| is_active | ✅ | — | ✅ |
| created_at | ✅ | — | ✅ |

**判定**：🔴 **反模式 #3 表达分裂（高）**

同一个 Activity 实体出现了 **3 个不同面貌**：

| 变体 | 字段数 | 差异 |
|------|--------|------|
| `ActivityResponse` | 10 | 全字段（用户端列表） |
| `ActivityBrief` | 8 | 缺 is_active, created_at（聚合接口） |
| `AdminActivityListItem` | 9 | **缺 description**（管理端列表） |

核心问题：管理端列表 `AdminActivityListItem` **缺少 description 字段**，而用户端列表 `ActivityResponse` 包含该字段。这意味着管理端列表无法预览活动描述。

**证据**：
- `app/schemas/activity.py:7-17` — ActivityResponse 含 description
- `app/schemas/zone.py:56-64` — ActivityBrief 无 is_active, created_at
- `app/schemas/admin_activity.py:28-39` — AdminActivityListItem 无 description

---

## 7. Zone

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `ZoneBrief` | `schemas/zone.py:15` | 7 | `GET /api/zones` |
| `AdminZoneListItem` | `schemas/admin_zone.py:41` | 12 | `GET /admin/zones` 列表 |

**判定**：✅ 无表达分裂。管理端增加 is_active, is_banner, start_time, end_time, created_at 属于合理的管理字段扩展。

---

## 8. Coupon

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `CouponResponse` | `schemas/coupon.py:7` | 9 | `GET /api/coupons` 用户券列表 |
| `AdminCouponListItem` | `schemas/admin_coupon.py:25` | 9 | `GET /admin/coupons` 券模板列表 |

### 对比

| 字段 | CouponResponse | AdminCouponListItem |
|------|:------------:|:-------------------:|
| id, code, type, value, min_order_amount, valid_from, valid_to | ✅ | ✅ |
| status | ✅ | — |
| used_at | ✅ | — |
| is_active | — | ✅ |
| created_at | — | ✅ |

**判定**：✅ 无表达分裂。
- `CouponResponse.status/used_at` 是用户领取后的券状态（来源 `user_coupon` 表）
- `AdminCouponListItem.is_active/created_at` 是券模板管理字段（来源 `coupon` 表）
- 两者本质是**不同实体**（用户券 vs 券模板），各自字段合理

---

## 9. Job ⚠️

### Schema 清单

| Schema | 文件 | 字段数 | 使用场景 |
|--------|------|--------|----------|
| `JobResponse` | `schemas/job.py:6` | 9 | `GET /api/jobs` 列表 |
| `AdminJobListItem` | `schemas/admin_job.py:28` | 9 | `GET /admin/jobs` 列表 |

### 对比

| 字段 | JobResponse | AdminJobListItem |
|------|:----------:|:----------------:|
| id, title, company, location, salary_range, description, requirements, created_at | ✅ | ✅ |
| **contact_info** | ✅ | **❌ 缺失** |
| is_active | — | ✅ |

**判定**：🔴 **反模式 #3 表达分裂（中）**

| 变体 | 差异 |
|------|------|
| `JobResponse`（用户端） | 有 **contact_info**（求职者需要联系方式） |
| `AdminJobListItem`（管理端） | 有 **is_active**，**无 contact_info** |

核心问题：管理端无法在列表中看到联系方式。如果管理端需要在列表之外查看联系方式（比如在详情/编辑接口中），这属于设计取舍；但如果管理端列表就是唯一管理视图，缺失 contact_info 会导致管理员需要点进详情才能看到。

**证据**：
- `app/schemas/job.py:6-16` — JobResponse 含 contact_info
- `app/schemas/admin_job.py:28-39` — AdminJobListItem 无 contact_info

---

## 汇总：问题清单

| # | 反模式 | 严重 | 实体 | 涉及 Schema | 描述 |
|---|--------|------|------|-------------|------|
| 1 | **#3 表达分裂** | **高** | Activity | `ActivityResponse`(10字段) vs `ActivityBrief`(8字段) vs `AdminActivityListItem`(9字段，缺description) | 3 个变体，管理端列表缺 description |
| 2 | **#3 表达分裂** | **中** | Job | `JobResponse`(含contact_info) vs `AdminJobListItem`(无contact_info，有is_active) | 管理端列表缺联系方式 |
| 3 | 重复定义 | 低 | Course | `CourseBrief` ≡ `CourseListResponse` | 字段完全一致但是两个独立类型 |
| 4 | 轻微分裂 | 低 | Certification | `CertificationDetailResponse` vs `AdminCertificationListItem` | updated_at vs is_active 互换，各自独立定义 |

### 证据路径

| 问题 # | 证据文件 | 行号 |
|--------|---------|------|
| 1 | `schemas/activity.py`, `schemas/zone.py`, `schemas/admin_activity.py` | 7-17, 56-64, 28-39 |
| 2 | `schemas/job.py`, `schemas/admin_job.py` | 6-16, 28-39 |
| 3 | `schemas/zone.py`, `schemas/course.py` | 35-43, 9-19 |
| 4 | `schemas/certification.py`, `schemas/admin_certification.py` | 22-28, 6-16 |

---

## 对批次 4 的影响

- Activity 的 3 变体表达分裂和 Job 的 contact_info 缺失是需要修复的高/中优问题
- 其余 5 个实体（User, Order, Quiz, Zone, Coupon）的 Schema 差异均可由场景差异合理
