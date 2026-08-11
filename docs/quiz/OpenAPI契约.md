# 题库模块 OpenAPI 契约

> 版本：2026-08-11 全链路目标契约
> 适用前缀：`/api/quiz/*`、`/admin/quiz/*`
> 业务依据：[数据字典与状态机](./数据字典与状态机.md)
> 任务依据：[题库模块全链路开发 Todo](./题库模块全链路开发Todo.md)

## 1. 契约源

以下代码是当前机器可读契约：

- `app/contracts/quiz.py`：方法、路径、鉴权、权限、限流、错误码、请求模型、响应模型和示例注册表。
- `app/schemas/quiz_contract.py`：用户端严格 Pydantic 模型。
- `app/schemas/admin_quiz_contract.py`：管理端严格 Pydantic 模型。
- `app/domain/community/src/rule/quiz.py`：题型、选项、答案、题干规范化和判分规则。

当前机器契约注册 43 个 operation：用户端 22 个、管理端 21 个。本文件在此基础上冻结 5 个目标新增管理接口，目标总数为 48 个：

- `GET /admin/quiz/categories/{category_id}/impact`
- `POST /admin/quiz/imports/{job_id}/retry`
- `GET /admin/quiz/imports/{job_id}/source-url`
- `GET /admin/quiz/stats/overview`
- `GET /admin/quiz/stats/questions`

这 5 个接口当前尚未进入 `app/contracts/quiz.py`、Schema 和运行时路由，由 QF-02、QF-07、QF-11、QF-14 跟踪。在机器契约、Backend、Admin 类型和测试同步前，不得把“目标 48 个”表述为“运行时已有 48 个”。

所有新版路由必须直接使用统一严格模型，不得重新定义同义 Schema。旧路由已从 Backend 运行时删除，不提供字段别名、双写、重定向或兼容响应；历史迁移中的旧表名仅用于破坏性迁移审计。

## 2. 通用响应

成功响应统一为：

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

失败响应统一为：

```json
{
  "code": 40201,
  "message": "数据已被其他管理员修改，请刷新后重试",
  "data": null
}
```

分页数据统一包含 `items`、`total`、`page`、`page_size`。时间字段使用带时区 ISO 8601。

## 3. 错误码

| HTTP | 业务码 | 含义 |
|---:|---:|---|
| 422 | `40001` | Pydantic 或字段级参数校验失败 |
| 401 | `40100` | 未登录或令牌无效 |
| 403 | `40101` | 用户或管理员权限不足 |
| 422 | `40200` | 题量、状态、分类、题型等业务规则不满足 |
| 409 | `40201` | 乐观锁版本冲突或并发状态冲突 |
| 429 | `40202` | 用户或管理员题库接口限流 |
| 404 | `40300` | 资源不存在或资源不属于当前用户 |
| 500 | `50000` | 未处理的服务端错误 |

管理写接口携带 `lock_version`。版本过期必须返回 HTTP 409 和 `40201`，不得覆盖较新数据。

## 4. 鉴权和限流

- `GET /api/quiz/categories` 公开访问。
- 其余 `/api/quiz/*` 接口必须使用用户 Bearer Token。
- `/admin/quiz/*` 必须使用管理员 Bearer Token，并按注册表要求检查 `quiz:list`、`quiz:write` 或 `quiz:import`。
- `GET /api/quiz/questions`：每用户每分钟 60 次。
- 练习作答和考试答案保存：每用户每分钟 120 次。
- 管理普通写操作：每管理员每分钟 120 次。
- 管理批量发布/停用：每管理员每分钟 30 次。
- 导入创建和人工重试：每管理员每分钟 10 次。
- OSS 源文件/错误报告短签：每管理员每分钟 60 次。
- 超限不写业务数据。

实现状态（2026-08-11）：题库用户级限流、旧路由清理、显式响应模型、OpenAPI 元数据和后台任务监控已接入；`scripts/quality_gate.sh backend` 返回 0，当前无数据库测试为 `426 passed`。自动化测试只逐项核对现有 43 个 operation；目标新增 5 个接口、管理限流、真实 PostgreSQL/Redis/OSS 和独立 Worker 尚未验收，因此不得据此宣称 48 个目标接口已经完成。

后台任务状态可从 `/health` 与 `/ready` 的 `details.quiz_tasks` 查看。每个处理器返回队列深度、运行次数/耗时、成功失败数、重试数、最近心跳和最近异常类型；导入及 OSS 清理失败会保留审计并在后续轮次重试。

## 5. 用户端接口

| 方法 | 路径 | 请求模型 | 响应数据模型 |
|---|---|---|---|
| GET | `/api/quiz/categories` | 无 | `list[QuizCategoryNode]` |
| GET | `/api/quiz/questions` | `QuizQuestionListQuery` | `PaginatedData[QuizPublicQuestion]` |
| POST | `/api/quiz/practice-sessions` | `QuizPracticeSessionCreate` | `QuizPracticeSessionResponse` |
| GET | `/api/quiz/practice-sessions/current` | 无 | `QuizPracticeSessionResponse/null` |
| GET | `/api/quiz/practice-sessions/{session_id}` | 无 | `QuizPracticeSessionResponse` |
| POST | `/api/quiz/practice-sessions/{session_id}/attempts` | `QuizPracticeAttemptCreate` | `QuizPracticeAttemptResult` |
| POST | `/api/quiz/practice-sessions/{session_id}/abandon` | 无 | `QuizPracticeAbandonResponse` |
| GET | `/api/quiz/practice-history` | `QuizPracticeHistoryQuery` | `PaginatedData[QuizPracticeHistoryItem]` |
| GET | `/api/quiz/wrong-book` | `QuizWrongBookQuery` | `PaginatedData[QuizWrongBookItem]` |
| GET | `/api/quiz/collections` | 分页参数 | `PaginatedData[QuizCollectionItem]` |
| POST | `/api/quiz/collections` | `QuizCollectionCreate` | `QuizCollectionMutationResponse` |
| DELETE | `/api/quiz/collections/{question_id}` | 无 | `QuizCollectionMutationResponse` |
| GET | `/api/quiz/checkin` | 无 | `QuizCheckinStatusResponse` |
| GET | `/api/quiz/checkin/calendar` | `QuizCheckinCalendarQuery` | `list[QuizCheckinDay]` |
| GET | `/api/quiz/stats` | 无 | `QuizStatsResponse` |
| POST | `/api/quiz/exams` | `QuizExamCreate` | `QuizExamDetailResponse` |
| GET | `/api/quiz/exams/current` | 无 | `QuizExamDetailResponse/null` |
| GET | `/api/quiz/exams` | `QuizExamListQuery` | `PaginatedData[QuizExamListItem]` |
| GET | `/api/quiz/exams/{exam_id}` | 无 | `QuizExamDetailResponse` |
| PUT | `/api/quiz/exams/{exam_id}/answers/{exam_question_id}` | `QuizExamAnswerSave` | `QuizExamAnswerSaved` |
| POST | `/api/quiz/exams/{exam_id}/submit` | 无 | `QuizExamActionResponse` |
| POST | `/api/quiz/exams/{exam_id}/abandon` | 无 | `QuizExamActionResponse` |

为避免不同 Markdown 解析器吞掉表格中的反引号，保留一份可直接扫描的
`METHOD /path` 索引。它与上表和 `app/contracts/quiz.py` 必须保持同步：

```text
GET /api/quiz/categories
GET /api/quiz/questions
POST /api/quiz/practice-sessions
GET /api/quiz/practice-sessions/current
GET /api/quiz/practice-sessions/{session_id}
POST /api/quiz/practice-sessions/{session_id}/attempts
POST /api/quiz/practice-sessions/{session_id}/abandon
GET /api/quiz/practice-history
GET /api/quiz/wrong-book
GET /api/quiz/collections
POST /api/quiz/collections
DELETE /api/quiz/collections/{question_id}
GET /api/quiz/checkin
GET /api/quiz/checkin/calendar
GET /api/quiz/stats
POST /api/quiz/exams
GET /api/quiz/exams/current
GET /api/quiz/exams
GET /api/quiz/exams/{exam_id}
PUT /api/quiz/exams/{exam_id}/answers/{exam_question_id}
POST /api/quiz/exams/{exam_id}/submit
POST /api/quiz/exams/{exam_id}/abandon
```

考试详情使用判别联合模型：

- `in_progress`：只含题干、选项和用户已保存答案，不含标准答案或解析。
- `abandoned`：只含题干、选项和是否作答，不返回保存答案、标准答案、解析或成绩。
- `completed/timed_out`：返回最终答案、标准答案、解析、判定和成绩。

### 5.1 模拟考试调用流程

1. 考试页先调用 `GET /api/quiz/exams/current`；有活动考试则恢复，无活动考试再调用 `POST /api/quiz/exams`。
2. 每题保存调用 `PUT /api/quiz/exams/{exam_id}/answers/{exam_question_id}`。首次提交 `lock_version=0`；后续必须传上一次响应的版本号，HTTP 409 时重新获取考试详情，不得静默覆盖。
3. 用户交卷调用 `POST /api/quiz/exams/{exam_id}/submit`；放弃调用 `POST /api/quiz/exams/{exam_id}/abandon`。截止时间到达后服务端自动转为 `timed_out`。
4. 结算后调用 `GET /api/quiz/exams/{exam_id}` 获取成绩、标准答案和解析；`GET /api/quiz/exams` 用于历史分页，`GET /api/quiz/stats` 用于实时个人统计。

## 6. 管理端接口

下表描述最终目标契约。标记“目标新增”的 5 行尚未进入当前运行时，其余 21 行已存在但仍需按全链路 Todo 完成真实联调。

| 方法 | 路径 | 权限 | 请求模型 | 响应数据模型 |
|---|---|---|---|---|
| GET | `/admin/quiz/categories` | `quiz:list` | `AdminQuizCategoryQuery` | `list[AdminQuizCategoryResponse]` |
| POST | `/admin/quiz/categories` | `quiz:write` | `AdminQuizCategoryCreate` | `AdminQuizCategoryResponse` |
| PUT | `/admin/quiz/categories/{category_id}` | `quiz:write` | `AdminQuizCategoryUpdate` | `AdminQuizCategoryResponse` |
| DELETE | `/admin/quiz/categories/{category_id}` | `quiz:write` | `AdminQuizVersionRequest` | `null` |
| POST | `/admin/quiz/categories/{category_id}/status` | `quiz:write` | `AdminQuizCategoryStatusUpdate` | `AdminQuizCategoryResponse` |
| GET | `/admin/quiz/categories/{category_id}/impact` | `quiz:write` | `AdminQuizCategoryImpactQuery` | `AdminQuizCategoryImpactResponse`（目标新增） |
| GET | `/admin/quiz/questions` | `quiz:list` | `AdminQuizQuestionQuery` | `PaginatedData[AdminQuizQuestionResponse]` |
| POST | `/admin/quiz/questions` | `quiz:write` | `AdminQuizQuestionCreate` | `AdminQuizQuestionResponse` |
| PUT | `/admin/quiz/questions/{question_id}` | `quiz:write` | `AdminQuizQuestionUpdate` | `AdminQuizQuestionResponse` |
| DELETE | `/admin/quiz/questions/{question_id}` | `quiz:write` | `AdminQuizVersionRequest` | `null` |
| POST | `/admin/quiz/questions/{question_id}/publish` | `quiz:write` | `AdminQuizVersionRequest` | `AdminQuizQuestionResponse` |
| POST | `/admin/quiz/questions/{question_id}/disable` | `quiz:write` | `AdminQuizVersionRequest` | `AdminQuizQuestionResponse` |
| POST | `/admin/quiz/questions/{question_id}/restore` | `quiz:write` | `AdminQuizVersionRequest` | `AdminQuizQuestionResponse` |
| POST | `/admin/quiz/questions/batch-publish` | `quiz:write` | `AdminQuizBatchRequest` | `AdminQuizBatchResponse` |
| POST | `/admin/quiz/questions/batch-disable` | `quiz:write` | `AdminQuizBatchRequest` | `AdminQuizBatchResponse` |
| GET | `/admin/quiz/questions/{question_id}/stats` | `quiz:list` | 无 | `AdminQuizQuestionStatsResponse` |
| POST | `/admin/quiz/imports/csv` | `quiz:import` | 文件和 `AdminQuizCsvImportMetadata` | `AdminQuizImportJobResponse` |
| POST | `/admin/quiz/imports/json` | `quiz:import` | `AdminQuizJsonImportRequest` | `AdminQuizImportJobResponse` |
| GET | `/admin/quiz/imports` | `quiz:list` | `AdminQuizImportJobQuery` | `PaginatedData[AdminQuizImportJobResponse]` |
| GET | `/admin/quiz/imports/{job_id}` | `quiz:list` | 无 | `AdminQuizImportJobResponse` |
| GET | `/admin/quiz/imports/{job_id}/report-url` | `quiz:list` | 无 | `AdminQuizSignedUrlResponse` |
| GET | `/admin/quiz/imports/{job_id}/source-url` | `quiz:list` | 无 | `AdminQuizSignedUrlResponse`（目标新增） |
| POST | `/admin/quiz/imports/{job_id}/retry` | `quiz:import` | 无 | `AdminQuizImportJobResponse`（目标新增） |
| GET | `/admin/quiz/stats/overview` | `quiz:list` | 无 | `AdminQuizStatsOverviewResponse`（目标新增） |
| GET | `/admin/quiz/stats/questions` | `quiz:list` | `AdminQuizStatsQuestionQuery` | `PaginatedData[AdminQuizQuestionStatsListItem]`（目标新增） |
| GET | `/admin/quiz/audit-logs` | `quiz:list` | `AdminQuizAuditQuery` | `PaginatedData[AdminQuizAuditLogResponse]` |

### 6.1 目标新增模型

`AdminQuizCategoryImpactQuery`：

- `action`：`disable`、`move` 或 `delete`。
- `target_parent_id`：仅 `move` 使用；省略表示移动到根节点，其他动作不得传。

`AdminQuizCategoryImpactResponse` 至少包含：

- `category_id`、`action`、`target_parent_id`。
- `descendant_category_count`。
- `draft_question_count`、`published_question_count`、`disabled_question_count`。
- `affected_new_pool_question_count`：执行后从新会话题池移入或移出的发布题数。
- `history_snapshot_affected=false`：明确既有练习/考试快照不会改变。
- `can_execute`、`blocking_reasons`、`calculated_at`。

影响预览只读，不预留锁或替代执行时校验。Admin 展示预览后发起原有写接口；Backend 必须在写事务内重新检查版本、层级、状态和删除条件。

`POST /admin/quiz/imports/{job_id}/retry` 只允许 `failed`，沿用原任务 ID 和 `import_batch_key` 后重新进入 `queued`。`validation_failed` 应修正文件后创建新任务；`succeeded`、运行中状态和超过重试预算均返回业务规则错误。

`AdminQuizStatsOverviewResponse` 至少包含：

- `calculated_at`。
- 分类总数及 active/disabled 数。
- 题目总数及 draft/published/disabled 数。
- 练习会话数、练习首答数/正确数/正确率。
- completed/timed_out 考试场次、考试已答数/正确数/正确率。

`AdminQuizStatsQuestionQuery` 支持 `category_id`、`question_type`、`status`、`keyword`、`page`、`page_size`。每个 `AdminQuizQuestionStatsListItem` 返回题目 ID、题干摘要、分类、题型、状态、练习首答次数/正确率、考试作答次数/正确率和 `calculated_at`。统计不返回用户 ID，不提供用户下钻或导出。

`AdminQuizAuditQuery` 在现有字段上新增 `request_id`、`start_at` 和 `end_at`；时间使用带时区 ISO 8601，且 `start_at <= end_at`。审计响应必须脱敏，不返回 Token、完整签名 URL、异常堆栈或批量标准答案正文。

### 6.2 Admin 调用流程

1. 分类移动、停用或删除前调用影响预览；管理员确认后调用既有写接口，执行结果以事务重校验为准。
2. 导入页通过 CSV/JSON 创建任务并轮询详情；`validation_failed` 下载报告，`failed` 才能人工重试。
3. 源文件和错误报告分别调用 `source-url`、`report-url` 获取最长 300 秒短签；地址过期后重新申请，不缓存或写日志。
4. 统计页先读 `stats/overview`，再按筛选读 `stats/questions`；页面展示 `calculated_at` 和最多 1 分钟最终一致说明。
5. 审计页按管理员、动作、对象、结果、请求 ID 和时间筛选；只读查看详情，不提供编辑、删除或导出。

## 7. 请求示例

创建普通练习：

```json
{"mode":"normal","category_id":12,"question_count":20}
```

提交多选练习答案；服务端去重并按字母排序：

```json
{"session_question_id":91,"idempotency_key":"4a0a1568-72d8-4dc1","user_answer":["C","A","A"]}
```

保存考试答案并执行乐观锁检查：

```json
{"user_answer":"B","lock_version":3}
```

创建判断题草稿时可省略选项，服务端固定补为 `A=正确、B=错误`：

```json
{"category_id":12,"question_type":"judge","question_text":"HTTP 是无状态协议。","correct_answer":"A"}
```

批量发布：

```json
{"items":[{"question_id":101,"lock_version":2},{"question_id":102,"lock_version":5}]}
```

`items` 必须是当前页面明确勾选的 1 至 100 个唯一题目 ID，不接受“全部筛选结果”。任一条失败时整批不变。

分类移动影响预览：

```text
GET /admin/quiz/categories/12/impact?action=move&target_parent_id=3
```

移动到根节点时省略 `target_parent_id`；“保持原父级”不属于有效移动请求。

导入基础设施失败后人工重试：

```text
POST /admin/quiz/imports/88/retry
```

请求无业务 Body；重试的幂等和状态校验由服务端任务行完成。

## 8. 明确删除的旧接口

以下路径在当前运行时和 OpenAPI 中均不存在；客户端请求这些路径应按 404 处理，不得回退或重试旧契约。

- `POST /api/quiz/submit`
- `POST /api/quiz/wrong-book`
- `DELETE /api/quiz/wrong-book/{id}`
- `POST /api/quiz/checkin`
- 旧 `/api/quiz/exam/*`
- `GET /api/quiz/progress`
- `GET /api/quiz/recent`
- `POST /admin/quiz/questions/batch-delete`
- `POST /admin/quiz/import`
- `POST /admin/quiz/import/json`

客户端必须整体切换到新契约，不提供字段别名、双写、旧路径重定向或兼容响应。
