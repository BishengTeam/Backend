# domain/order — 订单支付领域

订单创建、支付、库存管理、优惠券的领域知识对象。

## 公开能力
- 订单状态机（pending → paid → completed/refunded/closed）
- 库存锁定/确认/释放（原子操作 + 审计流水）
- 价格配置查询规则
- 优惠券管理规则
- 报名数据校验规则

## 上游依赖
- product/domain/user/（用户身份 + 实名认证）
- PostgreSQL 事务（行锁保证库存原子性）

## 下游影响
- api/orders, api/payment, api/coupon（用户端）
- api/admin/orders, api/admin/coupons, api/admin/prices, api/admin/statistics（管理端）
- services/cleanup（过期订单清理定时任务）

## 边界规则
- 对外仅暴露 index/ 公开入口
- 领域内模型、规则、转换通过 index/ 聚合
- 跨域依赖进入下游域 index/，不直接引用内部文件

## 文档入口
- doc/reference/（待建）
