# domain/content — 内容运营领域

活动、协议签署、Banner、工单、专区的领域知识对象。

## 公开能力
- 活动管理（Activity + ActivityRegistration + ActivityReminder）
- 用户协议签署（Agreement type/status/signature）
- Banner 轮播（is_active + sort + 时间范围）
- 用户工单（Ticket status: waiting_manual/processing/resolved）
- 内容专区（Zone zone_type: article/tool/activity）

## 上游依赖
- product/domain/user/（用户身份，所有子模型均有 user_id 外键）

## 下游影响
- api/activity, api/agreement, api/ticket, api/zone（用户端）
- api/admin/agreements, api/admin/banners, api/admin/tickets, api/admin/settings, api/admin/zones（管理端）

## 边界规则
- 对外仅暴露 index/ 公开入口
- 纯模型聚合域，无状态机/复杂规则

## 文档入口
- doc/reference/（待建）
