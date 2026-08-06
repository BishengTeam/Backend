# 题库模块 OpenAPI 契约

> 版本：2026-08-06 冻结稿
> 适用前缀：`/api/quiz/*`、`/admin/quiz/*`
> 业务依据：[数据字典与状态机](./数据字典与状态机.md)

## 1. 契约源

以下代码是阶段一冻结的机器可读契约：

- `app/contracts/quiz.py`：方法、路径、鉴权、权限、限流、错误码、请求模型、响应模型和示例注册表。
- `app/schemas/quiz_contract.py`：用户端严格 Pydantic 模型。
- `app/schemas/admin_quiz_contract.py`：管理端严格 Pydantic 模型。
- `app/domain/community/src/rule/quiz.py`：题型、选项、答案、题干规范化和判分规则。

后续阶段必须直接使用这些模型替换现有路由契约，不得重新定义同义 Schema。当前旧路由的运行时代码将在对应业务阶段替换，不属于 QB-01 的兼容承诺。

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
| 429 | `40202` | 用户级题库接口限流 |
| 404 | `40300` | 资源不存在或资源不属于当前用户 |
| 500 | `50000` | 未处理的服务端错误 |

管理写接口携带 `lock_version`。版本过期必须返回 HTTP 409 和 `40201`，不得覆盖较新数据。

## 4. 鉴权和限流

- `GET /api/quiz/categories` 公开访问。
- 其余 `/api/quiz/*` 接口必须使用用户 Bearer Token。
- `/admin/quiz/*` 必须使用管理员 Bearer Token，并按注册表要求检查 `quiz:list`、`quiz:write` 或 `quiz:import`。
- `GET /api/quiz/questions`：每用户每分钟 60 次。
- 练习作答和考试答案保存：每用户每分钟 120 次。
- 超限不写业务数据。

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

考试详情使用判别联合模型：

- `in_progress`：只含题干、选项和用户已保存答案，不含标准答案或解析。
- `abandoned`：只含题干、选项和是否作答，不返回保存答案、标准答案、解析或成绩。
- `completed/timed_out`：返回最终答案、标准答案、解析、判定和成绩。

## 6. 管理端接口

| 方法 | 路径 | 权限 | 请求模型 | 响应数据模型 |
|---|---|---|---|---|
| GET | `/admin/quiz/categories` | `quiz:list` | `AdminQuizCategoryQuery` | `list[AdminQuizCategoryResponse]` |
| POST | `/admin/quiz/categories` | `quiz:write` | `AdminQuizCategoryCreate` | `AdminQuizCategoryResponse` |
| PUT | `/admin/quiz/categories/{category_id}` | `quiz:write` | `AdminQuizCategoryUpdate` | `AdminQuizCategoryResponse` |
| DELETE | `/admin/quiz/categories/{category_id}` | `quiz:write` | `AdminQuizVersionRequest` | `null` |
| POST | `/admin/quiz/categories/{category_id}/status` | `quiz:write` | `AdminQuizCategoryStatusUpdate` | `AdminQuizCategoryResponse` |
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
| GET | `/admin/quiz/audit-logs` | `quiz:list` | `AdminQuizAuditQuery` | `PaginatedData[AdminQuizAuditLogResponse]` |

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

## 8. 明确删除的旧接口

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
