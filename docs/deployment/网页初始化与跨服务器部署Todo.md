# 网页初始化与跨服务器部署 Todo

> 版本：2026-08-14 当前基线
>
> 设计依据：[网页初始化与跨服务器部署设计](./网页初始化与跨服务器部署设计.md)
>
> API 契约：[网页初始化 API 与错误码契约](./网页初始化API与错误码契约.md)
>
> 范围：Backend、Admin、部署运维和 UAT；不包含 Platform
>
> 维护规则：任务只有在验收标准和证据均满足后才能标记 `DONE`。

## 1. 交付目标

在一台已安装 Docker Compose、Git 且已配置 HTTPS 网关的新服务器上，运维只需运行宿主机入口脚本、建立 SSH 隧道并完成一次性网页表单，即可安全部署 Backend 和 Admin。安装过程必须可恢复、可追溯、不可覆盖已有业务库，正式服务不得拥有 Secret 写权限。

## 2. 冻结决策

| 维度 | 决策 |
|---|---|
| 初始化入口 | 独立一次性 `/setup`，仅 `127.0.0.1` 和 SSH 隧道访问 |
| Secret | 网页写入专用目录；正式服务只读 |
| 编排 | 宿主机脚本控制 Docker；Web 容器不挂 Docker Socket |
| 数据库 | 内置或外部 PostgreSQL/Redis；外部目标必须为空 |
| 源码 | Backend/Admin 各读取一次最新 `main` 后固定 Commit |
| 构建 | 目标服务器现场构建并记录镜像 ID |
| 质量 | Backend/Admin 全门禁和隔离迁移失败即终止 |
| 平台端 | 不处理 Platform |
| 网关 | 运维预先配置两个 HTTPS 域名；Admin 受 IP 白名单或 VPN 保护 |
| 恢复 | 完整 Secret 恢复包用恢复公钥加密并上传独立私有 OSS |
| 完成口径 | 部署后先为 `INSTALLED_PENDING_UAT`；真实闭环签署后才是 `PRODUCTION_ACCEPTED` |
| 失败处理 | 幂等续跑和重试；网页不删除数据库、卷或 Secret |

## 3. 状态含义

| 状态 | 含义 |
|---|---|
| `DONE` | 代码、自动化、生产等价部署和对应证据全部满足 |
| `🧪` | 代码与本地测试通过，但仍缺真实服务器、OSS、微信或支付证据 |
| `DOING` | 已有部分实现，尚未达到代码级验收 |
| `BLOCKED` | 外部环境或权限阻断，任务中写明解除条件 |
| `TODO` | 尚未开始 |

## 4. 阶段一：设计、契约和安全基础

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-00 | DONE | P0 | PO/OPS/BE | 冻结网页初始化范围和状态机 | 无 | 设计覆盖访谈确认的全部边界，明确不包含 Platform 和危险网页重置 |
| WEBI-01 | DONE | P0 | BE/OPS | 建立唯一专项设计和 Todo | WEBI-00 | 两份文档互相链接，状态、负责人和验收口径完整 |
| WEBI-02 | 🧪 | P0 | BE | 冻结 Bootstrap API/错误码和脱敏规范 | WEBI-00 | 已新增独立 API 契约并覆盖状态、配置、管理员、重试和正式验收；待干净服务器日志扫描确认不泄露 Secret |
| WEBI-03 | 🧪 | P0 | BE/OPS | 建立文件权限、路径和原子写入策略 | WEBI-00 | 已实现符号链接/越界拒绝、0700/0600、目录事务和原子替换并通过单测；待故障注入验证磁盘满/断电 |
| WEBI-04 | 🧪 | P0 | BE/OPS | 建立安装状态签名和完成锁 | WEBI-02 | HMAC 状态、文件锁、单向迁移、并发唯一赢家和 410 完成锁测试通过；待真实容器中断续跑 |

## 5. 阶段二：一次性网页与 Secret

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-05 | 🧪 | P0 | BE | 实现独立 Bootstrap FastAPI 服务和静态网页 | WEBI-02 | 独立配置、页面、健康接口和一次性 Token 已实现并通过 HTTP 单测；待容器浏览器冒烟 |
| WEBI-06 | 🧪 | P0 | BE/OPS | 只绑定宿主机环回端口并提供 SSH 使用说明 | WEBI-05 | Compose 固定 `127.0.0.1:18080`，脚本输出 SSH 隧道命令，错误 Token 单测为 401；待真实端口扫描 |
| WEBI-07 | 🧪 | P0 | BE | 自动生成内部 Secret | WEBI-03、WEBI-05 | PostgreSQL、JWT、PII、Metrics Token 已分别用 CSPRNG 生成且不回显；待新服务器权限复核 |
| WEBI-08 | 🧪 | P0 | BE | 接收并验证微信、支付 V3 和三组 OSS 凭据 | WEBI-03、WEBI-05 | PEM/V3/ID/URL/大小校验与微信、支付签名、私有 OSS 探针已实现；当前仅模拟外部依赖通过，待真实凭据 |
| WEBI-09 | 🧪 | P0 | BE | 写入白名单 `runtime.env` | WEBI-03、WEBI-08 | 白名单 env 与独立 Secret 文件已实现，NUL/换行/未知键和 Secret 落 env 均有测试；待现场文件复核 |
| WEBI-10 | 🧪 | P1 | BE | 实现脱敏进度页和当前步骤重试 | WEBI-04、WEBI-05 | 已按签名状态恢复页面并只清除当前失败；待浏览器关闭、宿主重启演练 |

## 6. 阶段三：数据库、迁移和正式种子

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-11 | 🧪 | P0 | BE/OPS | 支持内置 PostgreSQL/Redis | WEBI-07、WEBI-09 | Compose 已提供 PostgreSQL 16/Redis 7 internal profile、内部网络和健康依赖；待 Docker 实启 |
| WEBI-12 | 🧪 | P0 | BE/OPS | 支持外部 PostgreSQL/Redis 和自定义端口 | WEBI-08、WEBI-09 | 外部模式和 PostgreSQL `3306` 单测通过，密码/Redis URL 仅进 Secret；待外部 Redis 实测 |
| WEBI-13 | 🧪 | P0 | BE | 实现空 PostgreSQL/Redis 拒绝门禁 | WEBI-11、WEBI-12 | PostgreSQL 表/Alembic 历史与 Redis `SCAN` 空目标门禁已覆盖空/非空测试；待真实外部目标演练 |
| WEBI-14 | 🧪 | P0 | BE/OPS | 独立 migration job | WEBI-13 | 独立 job、Web 禁迁移和唯一 head `deploy001` 已固化；隔离 PostgreSQL `upgrade -> downgrade -> upgrade` 已通过，待 Compose job 实跑 |
| WEBI-15 | 🧪 | P0 | BE | 新增版本化生产种子入口 | WEBI-14、WEBI-16 | 版本化、无测试数据/`--force`、冲突拒绝已实现；真实隔离 PostgreSQL 连跑两次为 `4/8 -> 0/0`，待正式镜像执行 |
| WEBI-16 | 🧪 | P0 | BE | 网页创建唯一超级管理员 | WEBI-14 | 12 位密码、PBKDF2、advisory lock、已有管理员拒绝已实现；真实隔离 PostgreSQL 并发同请求返回同一管理员 ID，待网页容器演练 |

## 7. 阶段四：源码、质量和现场构建

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-17 | 🧪 | P0 | OPS | 实现宿主机 preflight | WEBI-00 | 脚本已检查 Docker/Compose、Git、时钟、端口、路径、权限和资源告警；待干净服务器执行 |
| WEBI-18 | 🧪 | P0 | OPS | 固定 Backend/Admin 最新 main Commit | WEBI-17 | 每仓一次 fetch、脏树拒绝、detached SHA 和私有发布清单已实现；待真实远端演练 |
| WEBI-19 | 🧪 | P0 | BE/OPS | 临时容器运行 Backend 完整门禁 | WEBI-18 | 隔离 PostgreSQL/网络、Backend 门禁与 DB 集成命令已编排；当前宿主 Docker 权限阻断实跑 |
| WEBI-20 | 🧪 | P0 | Admin/OPS | 临时容器运行 Admin 测试和生产构建 | WEBI-18 | 同构 `/tmp` 隔离目录已完成 `npm ci`、50 项测试和生产构建；Docker 容器实跑仍被宿主权限阻断，npm audit 另报 9 个依赖漏洞 |
| WEBI-21 | 🧪 | P0 | OPS | 现场构建并记录 Backend/Admin 镜像 | WEBI-19、WEBI-20 | Commit 标签、镜像 ID和发布清单已实现并单测；待 Docker 现场构建 |

## 8. 阶段五：恢复包和部署编排

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-22 | 🧪 | P0 | BE | 实现 RSA-OAEP + AES-256-GCM 恢复包 | WEBI-08、WEBI-18 | 混合加密、明文不落盘、RSA 3072+、错钥/篡改/覆盖拒绝测试通过；待生产密钥保管演练 |
| WEBI-23 | 🧪 | P0 | BE/OPS | 上传独立私有恢复 OSS 并校验 | WEBI-22 | 私有 ACL、独立凭据、对象元数据和 SHA-256 校验已实现并模拟测试；待真实版本化 Bucket |
| WEBI-24 | 🧪 | P0 | BE | 提供离线恢复包解密工具 | WEBI-22 | CLI 显式输出、拒绝覆盖/符号链接、0600 恢复测试通过；待离线主机演练 |
| WEBI-25 | 🧪 | P0 | OPS | 新增部署 Compose | WEBI-11、WEBI-12、WEBI-21 | Bootstrap 可写、正式 Secret 只读、两端环回且无 Docker Socket；`compose config` 通过，待容器实启 |
| WEBI-26 | 🧪 | P0 | OPS | 实现 `bootstrap_server.sh` 阶段编排 | WEBI-17 至 WEBI-25 | 阶段续跑、网页重试、启动/关闭及失败留阶段已实现并通过静态契约；待中断故障注入 |
| WEBI-27 | DOING | P0 | OPS/BE | 部署后健康、就绪和 Secret 泄露检查 | WEBI-26 | 已强制 `/ready`、Admin 可达、Worker 心跳和数据库指纹；尚缺真实日志泄露扫描和公网端口扫描 |

## 9. 阶段六：Admin 验收页和真实 UAT

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-28 | 🧪 | P0 | BE | 新增部署/UAT 状态、证据和签署模型/API | WEBI-04、WEBI-27 | `deploy001`、十项证据、超级管理员 GET/签署、发布摘要并发确认和 PostgreSQL 不可变触发器已实现；真实迁移往返通过，待全量 UAT 证据 |
| WEBI-29 | BLOCKED | P0 | Admin | 新增“系统部署与验收”页面 | WEBI-28 | 解除条件：当前会话取得 Admin 仓库写权限；Backend API 已就绪，页面不得查看或修改 Secret |
| WEBI-30 | TODO | P0 | BE/Admin/PO | 建立隔离 UAT 批次和数据标识 | WEBI-28、WEBI-29 | 不进入正式统计；0.01 元退款完成后归档证据 |
| WEBI-31 | BLOCKED | P0 | OPS/PO | 真实微信、支付、退款和 OSS UAT | WEBI-27、WEBI-30 | 解除条件：HTTPS 域名、正式小程序版本、测试用户、支付商户和三个私有 Bucket 就绪 |
| WEBI-32 | BLOCKED | P0 | OPS/PO | 干净服务器完整安装演练 | WEBI-26、WEBI-27 | 解除条件：提供新服务器/生产等价 VM、外部凭据和 HTTPS 网关；保存全量证据 |

## 10. 自动化与安全测试

| ID | 状态 | 优先级 | 负责人 | 任务 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| WEBI-33 | 🧪 | P0 | BE | 单元测试 Secret、状态机和认证 | WEBI-03 至 WEBI-10 | 原子写入、权限、并发、重入、坏 Token、脱敏和路径攻击已覆盖；待容器/浏览器层补证 |
| WEBI-34 | 🧪 | P0 | BE | 测试空库门禁、迁移、种子和管理员 | WEBI-13 至 WEBI-16 | 空库门禁、`deploy001` 往返、管理员并发幂等及生产种子两次执行均在隔离 PostgreSQL 通过；待 Docker 内完整串联 |
| WEBI-35 | 🧪 | P0 | BE | 测试恢复包加解密和 OSS 失败路径 | WEBI-22 至 WEBI-24 | 错钥、篡改、私有 ACL、元数据不符、覆盖和路径攻击已覆盖；真实 OSS 中断重试待验 |
| WEBI-36 | DOING | P0 | OPS | Shell/Compose 静态与故障注入测试 | WEBI-25、WEBI-26 | `bash -n`、部署静态契约和两份 `compose config` 已通过；尚缺 ShellCheck、磁盘满、中断和容器失败注入 |
| WEBI-37 | BLOCKED | P0 | Admin | Admin 页面组件和真实 Backend E2E | WEBI-29 | 解除条件：Admin 仓库可写并完成 WEBI-29；随后验证权限、缺证据阻断、签署确认和错误恢复 |

## 11. 发布硬门禁

- [ ] Bootstrap 只能通过环回地址和一次性 Token 访问。
- [ ] Secret 原子写入、权限、脱敏和完成锁测试通过。
- [ ] 外部空库/Redis 门禁以及内置模式均通过。
- [ ] Backend/Admin 固定 Commit、隔离门禁和现场构建可复现。
- [ ] 独立 migration job、生产种子和唯一超级管理员闭合。
- [ ] 恢复包可离线解密，并已上传独立私有 OSS、校验 SHA-256。
- [ ] 正式 Backend/Admin 只绑定环回地址，Secret 只读且无 Docker Socket。
- [ ] Admin 验收页和不可跳过签署闭合。
- [ ] 干净服务器安装演练、真实微信登录、0.01 元支付退款和 OSS UAT 通过。
- [ ] 所有 P0 为 `DONE`，阻断缺陷为 0，OPS/BE/Admin/PO 完成签字。

## 12. 当前外部阻断

- 当前工作区存在大量尚未提交的 Backend 修改；现场部署脚本必须在干净发布工作区验收，不能直接以当前目录作为成功证据。
- 当前会话只允许写 Backend 工作区，Admin 页面任务需要取得 Admin 仓库写权限后实施。
- 尚无新服务器、正式 HTTPS 域名、恢复 OSS、隔离测试账号和真实支付回调环境，因此 WEBI-31/32 只能保持 `BLOCKED`。
- Admin `npm ci` 当前全依赖审计报告 5 个中危、3 个高危、1 个严重漏洞；`--omit=dev` 后生产依赖仍有 3 个中危、1 个高危。生产发布前需在 Admin 仓库评估并升级，不能用本次“测试/构建通过”代替供应链安全验收。

## 13. 当前自动化证据

2026-08-14 已执行：

- Backend 单元测试：`530 passed`（包含部署验收与题库答案不可提前暴露回归测试）。
- Backend 质量门禁：人社契约 `31/31`、Alembic 唯一 head `deploy001`、55 个 revision、离线 SQL、Bootstrap 静态契约和题库契约均通过。
- 隔离 PostgreSQL `3306`：完整 DB 集成套件 `156 passed`；随机测试数据库均在结束后删除。
- `deploy001`：真实 `upgrade -> downgrade quiz007 -> upgrade` 通过；事件 UPDATE/DELETE 和验收状态回退均被数据库触发器拒绝。
- 初始化管理员/种子：并发创建得到同一超级管理员；生产种子首跑创建 4 个认证和 8 个价格，次跑创建量均为 0。
- Admin 隔离验证：12 个测试文件、50 个测试通过，生产构建通过；仅保留大 chunk 警告，测试临时目录已删除。
- 当前不能执行：Docker 镜像构建/Compose 启动（当前用户无 Docker daemon 权限）、ShellCheck（宿主未安装）、Admin 验收页（Admin 仓库不可写）、真实微信/支付/OSS UAT（外部条件未就绪）。
