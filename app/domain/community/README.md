# domain/community — 社区互动领域

题库答题、快速提问、分享、收藏、AI 对话的领域知识对象。

## 公开能力
- 题库分类与题目管理（QuizCategory 树形结构、QuizQuestion 单选/多选/判断）
- 练习会话快照与不可变作答（QuizPracticeSession/QuizPracticeAttempt）
- 自动错题与独立收藏（QuizWrongItem/QuizCollection）
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
- 题型校验、答案规范化和完全匹配判分位于 `src/rule/quiz.py`；用户端练习和考试分别由 `services/quiz_practice.py`、`services/quiz_exam.py` 提供。旧 `services/quiz.py` 仅为未接入路由的历史源码兼容文件，不属于新版运行时契约。

## 文档入口
- doc/reference/（待建）
