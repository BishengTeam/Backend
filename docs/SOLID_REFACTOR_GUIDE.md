# SOLID 改造分支工作规范

## 分支结构

```
main                          ← 生产分支，不受影响
└── refactor/solid            ← SOLID 改造主分支
    ├── solid/points-mall     ← 子分支：拆分积分商城服务
    ├── solid/order-handlers  ← 子分支：商品类型注册表
    ├── solid/quiz-split      ← 子分支：拆分练习服务
    ├── solid/admin-quiz      ← 子分支：拆分后台题库服务
    └── solid/repository      ← 子分支：引入 Repository 层
```

## 分支命名规范

```
solid/<模块名>-<动作>
```

| 示例 | 说明 |
|---|---|
| `solid/points-mall-split` | 拆分积分商城服务 |
| `solid/order-handlers` | 商品类型注册表改造 |
| `solid/quiz-practice-split` | 拆分练习服务 |
| `solid/admin-quiz-split` | 拆分后台题库服务 |
| `solid/repository-layer` | 引入 Repository 层 |

## 提交规范

### 格式

```
refactor(<范围>): <简短描述>

<SOLID 原则说明>
- 违反了什么原则
- 如何修复
- 改动了哪些文件

Tests: <测试结果>
```

### 范围标签

| 标签 | 说明 |
|---|---|
| `points-mall` | 积分商城相关 |
| `order` | 订单相关 |
| `quiz` | 题库相关 |
| `payment` | 支付相关 |
| `auth` | 认证相关 |
| `repo` | Repository 层 |
| `infra` | 基础设施 |

### 提交类型

| 类型 | 说明 |
|---|---|
| `refactor` | 重构代码，不改变外部行为 |
| `test` | 添加或修改测试 |
| `docs` | 文档变更 |
| `chore` | 构建或工具变更 |

### 示例

```
refactor(points-mall): 拆分 PointsMallService 为三个职责单一的服务

SRP: 原 PointsMallService 混合了管理端 CRUD、用户端兑换、订单核销
- AdminPointsMallService: 券模板管理
- UserPointsMallService: 用户浏览/兑换/查看
- CouponCheckoutService: 订单支付前应用/移除优惠券

Files:
- app/services/points_mall_services/__init__.py (new)
- app/services/points_mall_services/shared.py (new)
- app/services/points_mall_services/admin_service.py (new)
- app/services/points_mall_services/user_service.py (new)
- app/services/points_mall_services/checkout_service.py (new)

Tests: 6/6 passed, 79/79 platform tests passed
```

## 工作流程

```
1. 从 refactor/solid 创建子分支
   git checkout refactor/solid
   git checkout -b solid/<模块名>-<动作>

2. 在子分支上开发和测试
   - 每个逻辑单元单独一个 commit
   - 必须通过全部单元测试
   - 必须通过语法检查

3. 测试通过后合并回 refactor/solid
   git checkout refactor/solid
   git merge solid/<模块名>-<动作>
   git branch -d solid/<模块名>-<动作>

4. 定期将 refactor/solid 同步 main 的更新
   git checkout refactor/solid
   git merge main
```

## 合并规则

| 规则 | 说明 |
|---|---|
| 子分支必须测试通过才能合并 | `python3 -m pytest tests/unit/ -x -q` |
| 不允许直接在 refactor/solid 上开发 | 必须通过子分支 |
| 合并使用 `--no-ff` 保留分支历史 | `git merge --no-ff` |
| 每个 Phase 完成后打 tag | `solid-phase-1`, `solid-phase-2` |

## 改造优先级

| Phase | 内容 | 涉及原则 |
|---|---|---|
| 1 | 拆分 PointsMallService | SRP |
| 2 | 拆分 OrderService 的优惠券逻辑 | SRP, DIP |
| 3 | 商品类型注册表 | OCP |
| 4 | 引入 Repository Protocol | DIP, ISP |
| 5 | 拆分 quiz_practice.py | SRP |
| 6 | 拆分 admin_quiz.py | SRP, ISP |

---

## 子 Agent 审核流程

### 流程图

```
主 Agent 编写代码
    ↓
提交到子分支
    ↓
┌─────────────────────────┐
│  子 Agent 代码审核        │
│  - SOLID 原则合规性      │
│  - 潜在 bug              │
│  - 行为是否改变          │
│  - 接口是否兼容          │
└─────────────────────────┘
    ↓
审核通过？
  ├── 否 → 主 Agent 修复 → 重新提交 → 重新审核
  └── 是 ↓
┌─────────────────────────┐
│  全量测试                │
│  - python3 -m py_compile │
│  - 全量单元测试           │
│  - 质量门               │
└─────────────────────────┘
    ↓
全部通过？
  ├── 否 → 主 Agent 修复 → 重新测试
  └── 是 ↓
┌─────────────────────────┐
│  合并到 refactor/solid   │
└─────────────────────────┘
```

### 审核标准

| 类别 | 检查项 | 不通过条件 |
|---|---|---|
| SOLID | 单一职责 | 一个类/模块承担多个不相关职责 |
| SOLID | 开闭原则 | 添加新类型需要修改现有代码 |
| SOLID | 接口隔离 | 接口暴露了调用方不需要的方法 |
| SOLID | 依赖反转 | 直接依赖具体实现而非抽象 |
| 行为 | 外部接口不变 | API 路由/参数/响应发生变化 |
| 行为 | 数据库操作不变 | SQL 逻辑或表结构变化 |
| 质量 | 无循环导入 | import 形成环 |
| 质量 | 无未使用代码 | 导入了但没用的模块 |
| 质量 | 类型标注完整 | 公开方法缺少类型标注 |

### 审核输出格式

```
VERDICT: PASS | FAIL
REASON: <一句话结论>
ISSUES:
- [CRITICAL] 描述
- [WARNING] 描述
- [INFO] 描述
```

### 测试标准

| 步骤 | 命令 | 通过标准 |
|---|---|---|
| 语法 | `python3 -m py_compile <files>` | 无错误 |
| 单元 | `python3 -m pytest tests/unit/ -q` | 全部通过 |
| 质量 | `scripts/quality_gate.sh backend` | passed |
