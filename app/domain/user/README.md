# domain/user — 用户身份领域

用户账户、实名认证、管理员账号、积分系统的领域知识对象。

## 公开能力
- 用户基础模型（User: openid/phone/is_active）
- 实名身份认证（UserIdentity: user_type/status/edit_count）
- 管理员账号（AdminUser: username/role + ADMIN_ROLES 角色常量）
- 已删除用户 openid 追踪（DeletedOpenid）
- 积分账户与流水（UserPoints balance + PointsHistory 唯一约束）

## 上游依赖
- 微信 API（登录获取 openid）
- PostgreSQL 约束（UserPoints.balance >= 0, PointsHistory 唯一索引）

## 下游影响
- 几乎所有服务模块依赖 User / UserIdentity
- middleware/auth.py（认证中间件）
- 种子数据脚本

## 边界规则
- 对外仅暴露 index/ 公开入口
- ADMIN_ROLES 角色常量从 rule/admin_roles.py 导出

## 文档入口
- doc/reference/（待建）
