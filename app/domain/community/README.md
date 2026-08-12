# domain/community — 社区互动领域

题库答题、快速提问、分享、收藏、AI 对话的领域知识对象。

## 公开能力
- 题库分类与题目管理（QuizCategory 树形结构、QuizQuestion 单选/多选/判断）
- 答题记录与错题/收藏（QuizRecord is_wrong/is_collected）
- 每日打卡签到（QuizCheckin 连续天数）
- 快速提问（QuickQuestion 预设问题）
- 分享追踪（Share code + visit_count）
- 收藏聚合（Collection target_type + target_id）
- AI 对话会话（Conversation session_id + messages）

## 上游依赖
- product/domain/user/（用户身份）
- Dify（AI 对话引擎，integrations/chat_backend.py）

## 下游影响
- api/quiz, api/chat, api/share, api/collection（用户端）
- api/admin/quiz（管理端）

## 边界规则
- 对外仅暴露 index/ 公开入口
- 计分逻辑、每日打卡规则保留在 services/quiz.py（领域规则尚未达到独立抽取条件）

## 文档入口
- doc/reference/（待建）
