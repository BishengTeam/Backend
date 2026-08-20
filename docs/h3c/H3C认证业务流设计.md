# H3C 认证业务流设计

> 版本：2026-08-20  
> 状态：开发实现中；本文冻结本轮业务决策。  
> 需求来源：《H3C报名需求确认》与业务确认。

## 1. 业务范围

H3C 独立于普通认证报名，提供考试批次、三种类型报名、支付、审核、材料补交、退款确认和官方模板导出。

| 报名类型 | 必填材料 |
|---|---|
| 考券报名 | 考券号、优惠券证明 JPG |
| 学生报名 | 学信网在线验证码、学生证明 JPG |
| 全额报名 | 无 H3C 专属材料 |

三种类型均创建统一订单并锁定同一个批次名额。价格由批次配置，支持人工调整；0 元订单不调起微信支付，创建后直接进入待审核。

## 2. 状态机

```text
pending_payment
  -> 微信支付成功 / 0 元订单创建
  -> pending_review
  -> 审核通过 -> approved
  -> 审核拒绝:
       有补交次数 -> rejected_awaiting_resubmission
       无补交次数 -> pending_refund_confirmation

rejected_awaiting_resubmission
  -> 用户补交材料 -> pending_review
  -> 超时 -> pending_refund_confirmation

pending_refund_confirmation
  -> H3C 管理员确认
  -> refund_processing
  -> 微信退款成功 -> refunded_closed

pending_payment
  -> 用户取消 / 支付超时 -> cancelled
```

每次审核拒绝重新计算补交窗口。批次默认 72 小时、最多 2 次，均可配置。

## 3. 关键业务规则

1. 同一考试批次 + 同一身份证号只允许一条有效报名。
2. 三种报名类型共享批次总名额。
3. 订单创建即锁定名额；取消、超时、退款成功释放名额。
4. 支付前用户可取消；支付后不能主动取消或申请退款。
5. 支付成功后基础报名信息锁定，用户只能重新上传被拒绝材料。
6. 审核拒绝必须绑定具体材料项，并记录预设原因和补充说明。
7. 审核历史不可修改，只能追加。
8. 审核通过后仍允许管理员例外关闭并发起退款，操作需二次验证并审计。
9. 考试批次取消时，未支付订单自动取消，已支付订单生成待确认退款任务。
10. 导出不改变报名状态，允许重复导出并保留导出痕迹。

## 4. 数据模型

新增独立 H3C 领域表，并复用订单、支付、退款、库存和 OSS 基础能力：

```text
h3c_exam_batch       H3C 批次扩展配置，关联 plan
h3c_registration     报名主表和状态机
h3c_material_upload  上传前材料凭证
h3c_material         报名材料版本
h3c_review           不可变审核历史
h3c_refund_request   H3C 退款任务
h3c_export_job       异步导出任务
h3c_export_item      导出报名明细
```

通用 `plan` 仍负责报名开放窗口、考试时间、批次生命周期和总名额。

## 5. API 契约

### 用户端

```text
GET  /api/h3c/exam-batches
POST /api/h3c/materials
GET  /api/orders/h3c/profile
POST /api/orders/h3c
GET  /api/h3c/registrations
GET  /api/h3c/registrations/{id}
POST /api/h3c/registrations/{id}/cancel-payment
POST /api/h3c/registrations/{id}/materials
```

### 管理端

```text
POST /admin/h3c/batches
GET  /admin/h3c/batches
PUT  /admin/h3c/batches/{id}
POST /admin/h3c/batches/{id}/publish
POST /admin/h3c/batches/{id}/close-registration
POST /admin/h3c/batches/{id}/finalize
POST /admin/h3c/batches/{id}/cancel

GET  /admin/h3c/registrations
GET  /admin/h3c/registrations/{id}
POST /admin/h3c/registrations/{id}/review
POST /admin/h3c/registrations/{id}/close

GET  /admin/h3c/refunds
POST /admin/h3c/refunds/{id}/confirm

POST /admin/h3c/exports
GET  /admin/h3c/exports
GET  /admin/h3c/exports/{id}/download-url
```

## 6. 角色与安全

新增固定角色 `h3c_admin`，仅访问 H3C 业务：

```text
h3c:batch_manage
h3c:review
h3c:export
h3c:refund
h3c:order_close
```

高频审核不需要二次验证。批次取消、报名例外关闭、退款确认和导出下载必须二次验证，并写入管理员安全审计。

## 7. 导出

三份官方模板随 Backend 版本内置并记录 SHA256：

```text
app/templates/h3c/coupon.xlsx
app/templates/h3c/full.xlsx
app/templates/h3c/student.xlsx
```

导出为后台异步任务，产物二选一：

```text
embedded_xlsx  官方模板，图片缩放到约 70x70 并嵌入对应行
images_zip     ZIP 根目录仅包含证明图片
```

ZIP 文件名使用报名号和材料类型，不使用姓名或身份证号。导出产物保存于 `h3c/exports/`，保留 30 天后自动删除。
