# 网页初始化 API 与错误码契约

> 版本：2026-08-14
>
> 适用范围：一次性 Bootstrap 服务与正式 Backend 部署验收接口
>
> 安全原则：本契约不包含、也不允许响应任何 Secret、数据库 URL、PEM 原文、管理员密码或带签名 OSS URL。

## 1. 访问边界

- Bootstrap 仅映射 `127.0.0.1:18080`，通过 SSH 隧道访问。
- `/healthz`、`/setup` 和静态资源不要求 Token；所有 `/api/bootstrap/*` 必须使用 `Authorization: Bearer <一次性 Token>`。
- Token 从 URL Fragment 读取后立即清除；Fragment 不会发送给服务器。
- 安装进入 `INSTALLED_PENDING_UAT` 后，`/setup` 和 Bootstrap 业务 API 永久返回 HTTP 410。
- 正式验收接口位于 Backend `/admin/deployment-acceptance*`，只允许 `super_admin` 管理员 Token。

## 2. Bootstrap API

| 方法 | 路径 | 请求 | 成功响应 | 幂等和状态要求 |
|---|---|---|---|---|
| GET | `/healthz` | 无 | Bootstrap 组件和版本 | 只证明进程存活，不代表安装成功 |
| GET | `/api/bootstrap/status` | 无 | 脱敏 `BootstrapStatusResponse` | 完成前可重复读取 |
| POST | `/api/bootstrap/configure` | `BootstrapConfigureRequest` | 脱敏状态 | 只允许 `NEW`；相同安装在 `CONFIGURED` 可幂等返回 |
| POST | `/api/bootstrap/retry` | 无 | 清除当前脱敏失败后的状态 | 不回退阶段、不删除文件或数据库 |
| POST | `/api/bootstrap/admin` | `BootstrapAdminRequest` | 脱敏状态 | 只允许 `AWAITING_ADMIN`；同一管理员可幂等恢复 |

`BootstrapStatusResponse` 只包含：版本、安装 ID、阶段、时间、重试次数、配置指纹、Backend/Admin Commit、发布清单 SHA-256、恢复对象键、恢复包 SHA-256 和最后一次脱敏失败。不得新增凭据字段。

## 3. 正式 Backend 验收 API

| 方法 | 路径 | 请求 | 成功响应 | 权限 |
|---|---|---|---|---|
| GET | `/admin/deployment-acceptance` | 无 | 固定发布身份、状态含义、十项证据及缺失项 | 仅超级管理员 |
| POST | `/admin/deployment-acceptance/accept` | `confirmation=PRODUCTION_ACCEPTED`、当前发布清单 SHA-256 | 终态验收记录 | 仅超级管理员 |

签署接口必须同时满足：

1. 客户端确认的发布清单 SHA-256 与服务器记录一致；
2. 十项必需证据的最新事件均为 `passed`；
3. 当前状态为 `installed_pending_uat`。

正式 Backend 不提供新增、修改或删除验收证据的通用管理接口。证据只能由 Bootstrap 或绑定实际业务记录的系统 UAT 协调器写入；验收事件由 PostgreSQL 触发器保证不可更新、不可删除。

签署成功后，运维在服务器重新执行一次 `scripts/bootstrap_server.sh`，使宿主机 HMAC 状态从 `INSTALLED_PENDING_UAT` 同步为 `PRODUCTION_ACCEPTED`；同步过程只读查询正式数据库，不接受人工指定状态。

## 4. Bootstrap 稳定错误码

| HTTP | code/detail | 含义 | 是否可重试 |
|---:|---|---|---|
| 400 | `invalid_content_length` | Content-Length 非法 | 修正请求后可重试 |
| 401 | `bootstrap token is invalid` | Token 缺失或不匹配 | 使用正确 Token |
| 409 | `state_conflict` / `bootstrap phase conflict` | 阶段冲突或并发提交失败 | 刷新状态后判断 |
| 409 | `installation_commit_failed` | 原子提交安装目录失败 | 修复文件系统后点重试 |
| 409 | `admin_creation_failed` | 超级管理员创建失败 | 修复数据库问题后重试 |
| 410 | `bootstrap is closed` | 一次性初始化已永久关闭 | 不可重开 |
| 413 | `request_too_large` | 请求超过 2 MiB 默认限制 | 缩小 PEM/表单后重试 |
| 422 | `validation_failed` | 字段校验失败；只返回字段名 | 修正字段后重试 |
| 422 | `configuration_invalid` | 离线安全校验未通过 | 修正配置后重试 |
| 422 | `external_validation_failed` | 微信、支付或 OSS 探针失败 | 修复外部依赖后重试 |
| 500 | `internal_error` | 未预期错误；不返回异常文本 | 查脱敏日志后重试 |

正式 Backend 验收接口沿用项目统一错误码：`40100` 未登录、`40101` 非超级管理员、`40201` 发布摘要变化/缺证据/终态冲突、`40300` 尚无部署验收记录。

## 5. 脱敏和日志约束

- 请求校验错误仅返回字段路径，禁止回显被拒绝的输入。
- 日志只记录异常类型、阶段和固定错误码；禁止记录异常字符串可能携带的第三方响应正文。
- 证据摘要先按 PII/Secret 关键字递归脱敏，并限制为 16 KiB。
- 发布清单、数据库指纹、恢复包和证据均使用 SHA-256 绑定；数据库指纹不可逆。
- 状态响应统一 `Cache-Control: no-store`，页面禁止第三方脚本、框架嵌入、摄像头、麦克风和支付权限。
