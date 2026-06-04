# domain/certification — 认证培训就业领域

认证项目、课程报名、岗位申请、竞赛报名的领域知识对象。

## 公开能力
- 认证类型查询（按厂商筛选、深信服考试券）
- 课程列表/报名/我的课程（含防重复报名校验）
- 岗位列表/申请（含防重复申请校验）
- 竞赛报名与统计

## 上游依赖
- product/domain/user/（用户身份）
- product/domain/order/（价格索引，仅 Certification.code 被订单领域引用）

## 下游影响
- api/certification, api/courses, api/job, api/competition（用户端）
- api/admin/certifications, api/admin/courses（管理端）
- services/order（创建订单时查证认证类型）

## 边界规则
- 对外仅暴露 index/ 公开入口
- 领域内模型通过 index/ 聚合

## 文档入口
- doc/reference/（待建）
