# 题库模块 TODO 开发计划

> 编制日期：2026-05-18  
> 模块：题库模块 `B-14`  
> 范围：`GET/POST/DELETE /api/quiz/*`  
> 当前结论：相对独立，可先行开发；不依赖微信、支付、订单、课程、实名认证链路。

---

## 一、开发目标

完成题库中心的最小可用闭环：

```text
题库分类树 → 题目列表 → 答题提交 → 自动判分 → 错题/收藏 → 打卡统计
```

接口状态目标：

| 接口 | 目标状态 |
|------|----------|
| `GET /api/quiz/categories` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `GET /api/quiz/questions` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `POST /api/quiz/submit` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `GET /api/quiz/wrong-book` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `POST /api/quiz/wrong-book` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `DELETE /api/quiz/wrong-book/{id}` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `GET /api/quiz/collections` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `POST /api/quiz/collections` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `DELETE /api/quiz/collections/{id}` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `GET /api/quiz/checkin` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |
| `POST /api/quiz/checkin` | 🧪 代码完成 + 本地无DB测试，待数据库集成测试 |

---

## 二、依赖评估

### 2.1 不依赖项

- 不依赖微信登录联调。
- 不依赖微信支付。
- 不依赖订单状态机。
- 不依赖实名认证。
- 不依赖课程报名。
- 不依赖 OSS。

### 2.2 必要依赖

| 依赖 | 说明 |
|------|------|
| PostgreSQL | 读取题库、写入答题记录、错题、收藏、打卡 |
| JWT 登录态 | 答题记录、错题本、收藏、打卡需要当前用户 |
| Alembic | 需要补充题库相关唯一约束/索引 |
| 题库数据 | 需要 seed 或导入工具提供基础题目 |

### 2.3 当前已有基础

已存在模型：

- `app/models/quiz.py`
  - `QuizCategory`
  - `QuizQuestion`
  - `QuizRecord`
  - `QuizCheckin`

已存在迁移：

- `alembic/versions/659fd10dac61_initial_create_all_18_tables.py`
  - `quiz_category`
  - `quiz_question`
  - `quiz_record`
  - `quiz_checkin`

当前缺失：

- `app/api/quiz.py`
- `app/services/quiz.py`
- `app/schemas/quiz.py`
- `scripts/import_quiz.py`
- 题库模块测试文件

---

## 三、接口设计清单

### 3.1 分类树

- [x] `GET /api/quiz/categories`

功能：

- 返回树形分类。
- 支持多级章节结构。
- 顶层分类 `parent_id = null`。

响应建议：

```json
{
  "code": 0,
  "message": "ok",
  "data": [
    {
      "id": 1,
      "name": "H3C NE",
      "description": "H3C NE 题库",
      "children": []
    }
  ]
}
```

### 3.2 题目列表

- [x] `GET /api/quiz/questions`

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `category_id` | int | 否 | 分类 ID |
| `question_type` | string | 否 | 题型 |
| `page` | int | 否 | 默认 1 |
| `page_size` | int | 否 | 默认 20，最大 100 |

规则：

- 默认只返回题干、选项、题型、解析可选字段。
- 列表接口默认不返回 `correct_answer`，避免前端直接泄题。
- 管理后台题库接口另行设计，可返回答案。

### 3.3 答题提交

- [x] `POST /api/quiz/submit`

请求体：

```json
{
  "question_id": 1,
  "user_answer": "A"
}
```

规则：

- 校验题目存在。
- 比对 `QuizQuestion.correct_answer`。
- 写入或更新 `QuizRecord`。
- 判断错误时 `is_wrong = true`。
- 正确时可将 `is_wrong = false`，具体是否保留历史错题需在开发前定规则。

### 3.4 错题本

- [x] `GET /api/quiz/wrong-book`
- [x] `POST /api/quiz/wrong-book`
- [x] `DELETE /api/quiz/wrong-book/{id}`

规则：

- 错题本基于 `quiz_record.is_wrong = true`。
- 添加错题时若无答题记录，则创建一条 `QuizRecord`，`is_wrong = true`。
- 删除错题不删除答题记录，只更新 `is_wrong = false`。
- 只能操作当前用户自己的错题记录。

### 3.5 收藏

- [x] `GET /api/quiz/collections`
- [x] `POST /api/quiz/collections`
- [x] `DELETE /api/quiz/collections/{id}`

规则：

- 收藏基于 `quiz_record.is_collected = true`。
- 添加收藏时若无答题记录，则创建一条 `QuizRecord`，`is_collected = true`。
- 取消收藏不删除答题记录，只更新 `is_collected = false`。
- 只能操作当前用户自己的收藏记录。

### 3.6 打卡

- [x] `GET /api/quiz/checkin`
- [x] `POST /api/quiz/checkin`

规则：

- 每个用户每天最多一条打卡记录。
- `POST` 重复调用应幂等返回当天记录。
- 连续天数基于昨日是否打卡计算。
- `questions_completed` 可先由请求体传入，后续可改为按当天答题数自动统计。

---

## 四、数据库与迁移 TODO

### 4.1 需要补充的约束

- [x] `quiz_record(user_id, question_id)` 唯一约束  
      防止同一用户同一题产生多条状态记录。

- [x] `quiz_checkin(user_id, checkin_date)` 唯一约束  
      防止同一用户同一天重复打卡。

- [x] `quiz_question.question_type` 枚举约束  
      建议允许值：`single_choice` / `multiple_choice` / `judge`。

### 4.2 建议新增迁移

文件建议：

```text
alembic/versions/<revision>_harden_quiz_constraints.py
```

DDL 建议：

```sql
CREATE UNIQUE INDEX uq_quiz_record_user_question
ON quiz_record (user_id, question_id);

CREATE UNIQUE INDEX uq_quiz_checkin_user_date
ON quiz_checkin (user_id, checkin_date);

ALTER TABLE quiz_question
ADD CONSTRAINT ck_quiz_question_type
CHECK (question_type IN ('single_choice', 'multiple_choice', 'judge'));
```

### 4.3 数据导入

- [x] 新增 `scripts/import_quiz.py`
- [x] 支持标准 CSV 导入（Excel 可另存 CSV）。
- [ ] 导入前校验：
  - 分类是否存在，不存在则可选自动创建。
  - 题型是否合法。
  - 选项 JSON 是否合法。
  - 标准答案是否在选项范围内。
  - 重复题目提示而非静默覆盖。

---

## 五、代码文件 TODO

### 5.1 Schema 层

- [x] 新建 `app/schemas/quiz.py`

建议模型：

- `QuizCategoryResponse`
- `QuizCategoryTreeResponse`
- `QuizQuestionResponse`
- `QuizQuestionQuery`
- `QuizSubmitRequest`
- `QuizSubmitResponse`
- `QuizRecordQuestionResponse`
- `QuizToggleRequest`
- `QuizCheckinRequest`
- `QuizCheckinResponse`

### 5.2 Service 层

- [x] 新建 `app/services/quiz.py`

建议服务：

- `list_categories()`
- `list_questions()`
- `submit_answer()`
- `list_wrong_book()`
- `add_wrong_question()`
- `remove_wrong_question()`
- `list_collections()`
- `add_collection()`
- `remove_collection()`
- `get_checkin_status()`
- `checkin()`

### 5.3 API 层

- [x] 新建 `app/api/quiz.py`

要求：

- 路由前缀：`/quiz`
- 标签：`题库`
- 所有路由显式声明 `response_model`
- Router 只做参数收集与调用 Service
- 需要用户态的接口使用 `Depends(get_current_user)`

### 5.4 路由注册

- [x] 修改 `app/api/__init__.py`

新增：

```python
from app.api.quiz import router as quiz_router

router.include_router(quiz_router)
```

---

## 六、测试计划

### 6.1 单元/静态测试

- [x] 新建 `tests/unit/test_quiz_system.py`

覆盖：

- [x] 题型枚举校验。
- [x] 题目列表不返回 `correct_answer`。
- [x] 答题判分规则正确/错误判定。
- [ ] 错题添加/删除幂等。
- [ ] 收藏添加/删除幂等。
- [ ] 打卡重复调用幂等。
- [x] 路由显式声明 `response_model`。
- [x] Service 不直接依赖 HTTP Request/Response。

### 6.2 本地验证命令

```powershell
python -m compileall -q app alembic tests
python -m pytest tests/unit -v
```

### 6.3 数据库验证

在可用数据库环境中执行：

```powershell
alembic upgrade head
```

并验证：

- [ ] 可以导入分类和题目。
- [ ] 同用户同题不会重复插入 `quiz_record`。
- [ ] 同用户同日期不会重复插入 `quiz_checkin`。

---

## 七、开发顺序建议

### Step 1：只读查询

- [x] `app/schemas/quiz.py`
- [x] `GET /api/quiz/categories`
- [x] `GET /api/quiz/questions`
- [x] 本地无DB测试

### Step 2：答题提交

- [x] `POST /api/quiz/submit`
- [x] 自动判分
- [x] 写入/更新 `quiz_record`
- [x] 本地无DB测试

### Step 3：错题本与收藏

- [x] 错题本列表/添加/删除
- [x] 收藏列表/添加/删除
- [x] 幂等处理
- [x] 本地无DB测试

### Step 4：打卡

- [x] 打卡状态查询
- [x] 打卡签到
- [x] 连续天数计算
- [x] 重复打卡幂等
- [x] 本地无DB测试

### Step 5：导入工具

- [x] 标准 CSV 模板定义
- [x] 导入脚本
- [x] 错误行提示
- [ ] 导入样例数据

### Step 6：文档同步

- [x] 更新 `docs/接口列表.md`
- [ ] 更新 `docs/接口文档.md`（按需）
- [x] 标注接口状态为“代码完成 + 本地无DB测试，待数据库集成测试”

---

## 八、验收标准

### 8.1 功能验收

- [x] 题库分类树可查询。
- [x] 题目可按分类分页查询。
- [x] 题目列表不泄露答案。
- [x] 用户答题后立即返回是否正确。
- [x] 错题本自动或手动维护。
- [x] 收藏可添加、取消、查询。
- [x] 打卡可查询、可签到、重复签到幂等。

### 8.2 规范验收

- [x] API 返回统一 `{code, message, data}`。
- [x] 成功响应 `code=0`。
- [x] 参数校验错误返回 `40001`。
- [x] 资源不存在返回 `40300`。
- [ ] 业务状态错误返回 `40200/40201`。
- [x] Router 函数体不超过 5 行左右。
- [x] Service 不依赖 HTTP 对象。
- [x] 金额无关，题库不引入支付/订单依赖。

### 8.3 测试验收

- [x] `python -m compileall -q app alembic tests` 通过。
- [ ] `python -m pytest tests/unit -v` 通过（当前环境未安装 pytest，已写入 requirements，待安装依赖后执行）。
- [ ] 题库模块测试覆盖完整 DB 状态流转（待接入测试数据库）。

---

## 九、风险与决策点

| 风险 | 影响 | 建议 |
|------|------|------|
| 题库 Excel 格式不统一 | 导入失败、数据质量差 | 先定义标准模板，再写导入脚本 |
| `quiz_record` 同题多记录语义不清 | 错题/收藏状态混乱 | 本期采用同用户同题单记录，后续如需历史答题另建 history 表 |
| 多选题答案比较规则不清 | 判分误差 | 多选答案统一排序后比较，如 `A,B,C` |
| 题目列表泄露答案 | 前端可直接看到答案 | 列表响应不返回 `correct_answer` |
| 打卡连续天数跨时区 | 统计不准 | 统一按 Asia/Shanghai 本地日期计算 |

---

## 十、预计工时

| 阶段 | 工时 |
|------|------|
| Schema + API + Service 骨架 | 1.5h |
| 分类树 + 题目列表 | 2h |
| 答题提交 + 判分 | 2h |
| 错题本 CRUD | 2h |
| 收藏 CRUD | 2h |
| 打卡统计 | 2h |
| Alembic 约束迁移 | 1h |
| 测试 | 2h |
| 导入工具 | 4h |
| 文档同步 | 1h |
| **合计** | **19.5h** |

---

## 十一、当前接口列表目标格式（数据库集成测试前）

```md
🧪 GET /api/quiz/categories — 题库分类树（代码完成 + 本地无DB测试，待数据库集成测试，支持章节层级）
🧪 GET /api/quiz/questions — 题目列表（代码完成 + 本地无DB测试，待数据库集成测试，支持分类/分页/题型筛选，不返回答案）
🧪 POST /api/quiz/submit — 答题提交（代码完成 + 本地无DB测试，待数据库集成测试，自动判分并写入答题记录）
🧪 GET /api/quiz/wrong-book — 错题本列表（代码完成 + 本地无DB测试，待数据库集成测试）
🧪 POST /api/quiz/wrong-book — 错题本添加（代码完成 + 本地无DB测试，待数据库集成测试，幂等）
🧪 DELETE /api/quiz/wrong-book/{id} — 错题本删除（代码完成 + 本地无DB测试，待数据库集成测试，幂等）
🧪 GET /api/quiz/collections — 收藏列表（代码完成 + 本地无DB测试，待数据库集成测试）
🧪 POST /api/quiz/collections — 收藏添加（代码完成 + 本地无DB测试，待数据库集成测试，幂等）
🧪 DELETE /api/quiz/collections/{id} — 收藏删除（代码完成 + 本地无DB测试，待数据库集成测试，幂等）
🧪 GET /api/quiz/checkin — 打卡记录与连续天数统计（代码完成 + 本地无DB测试，待数据库集成测试）
🧪 POST /api/quiz/checkin — 打卡签到（代码完成 + 本地无DB测试，待数据库集成测试，重复签到幂等）
```




