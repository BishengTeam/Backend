# 课程视频与 PDF 章节

课程不增加课程类型。每个章节绑定一个私有资源，类型由服务端解析，视频保持 MP4/MOV/MKV、5GB 上限；PDF 上限50MB，必须有可读的有效页面。Office 文件请先转换为 PDF。Docker 运行镜像增加 ffmpeg（提供 ffprobe），PDF 复用现有 pdfium。Windows 本地运行也需安装 ffprobe 并加入 PATH。

## 数据与上传

共享表由 Backend 的 `crs006` 维护：历史章节回填为 video，资源键取原 video_storage_key，修订号1；document 的 duration/video_storage_key 为空，page_count 为正数。新通用字段 resource_storage_key/resource_type/resource_version 不用于课程分类。size_bytes 改用 bigint，保留原有5GB视频上限。

管理端继续调用 `/admin/course-uploads`，新任务使用 `kind=chapter_file`，不提交人工填写的时长或页数。每次选择按原顺序串行建立任务，服务端在课程行锁下预留排序，再并行传输。完成请求同步合并文件并解析，期间状态为 validating，页面可查询任务；验证通过后在同一事务中直接绑定章节，最终为 bound。失败保留任务与具体原因，重试复用原任务ID，重复完成请求不会重复建章。旧 chapter_video 的未完成／已完成任务仍可恢复或确认生成章节。

替换时在创建上传任务中提交 chapter_id 与 expected_resource_version。文件验证后同时切换类型、地址、大小、MIME、时长和页数，递增修订号；保留章节ID、名称和顺序。行锁及修订号检查防止两个管理员覆盖。失败时旧资源继续可用。旧上传记录进入 retired，24小时后由原清理任务回收；没有上传记录的历史资源保守保留。清理忽略正在验证、已绑定或仍被章节引用的对象。

发布和恢复发布均检查至少一章、免费预览数量、未完成任务。验证／失败任务应先完成或取消。排序按完整章节名单提交，服务端两阶段更新以避免唯一约束冲突。免费预览按照当前章节排名计算，不受取消上传产生的排序空号影响。删除有活跃上传／待清理资源的草稿被阻止。

## 访问与进度

管理端预览：`GET /admin/courses/{course_id}/chapters/{chapter_id}/resource`，需 course:read。

用户通用地址：`POST /api/courses/{course_id}/chapters/{chapter_id}/resource-url`。继续验证本人账号、课程与章节归属、已发布状态及有效学习权益或免费预览。不能用价格0绕过授权。旧 playback-url 只签发视频，文档请求会被拒绝。

Backend 进度使用 last_position_seconds、last_page、resource_version；写入必须携带当前修订号，文档仅允许合法页码，完成由用户主动标记。视频秒数与文档页码不能互用。读取时过滤旧修订号，新增章节自然改变完成比例。

学习网站读取同一共享章节表，自己的 vw_chapter_progress/vw_chapter_note 由网站的 `python -m app.migrations` 升级，不合并两套进度。该程序区分PostgreSQL和SQLite；原 `scripts/003_document_progress.sql` 仅适用于PostgreSQL。网站资源接口为 `/api/courses/{course_id}/chapters/{chapter_id}/resource`，也沿用网站原权益验证，旧 playback 只处理视频。笔记保留原内容并记录资源修订号，内容更新时展示提示。旧版本延迟进度在章节行锁释放后仍须通过版本检查。

## 配套上线

1. 备份数据库与私有存储，执行 Backend `alembic upgrade head`（crs006）。
2. 网站使用新版部署脚本执行 `python -m app.migrations`，只升级 vw_* 表；已存在网站无需重跑001。
3. 更新 Backend 镜像及网站服务端，再更新 Admin、网站前端、小程序。
4. OSS 沿用私有权限，确认网站来源允许 GET/HEAD、Range 请求及暴露 Content-Length/Content-Range/Accept-Ranges。PDF.js 和 worker 随网站构建产物发布，不依赖公共CDN。
5. PDF上传在Settings、两套Compose和.env.example中统一默认开启。需要暂停时显式设置 `COURSE_DOCUMENT_UPLOADS_ENABLED=false`；按真实OSS验证视频/PDF上传、签名续签、旧视频、积分兑换、兑换码、赠送题库及撤销权益。

如果创建课程上传PDF时提示“PDF 上传暂未开放，请联系管理员”，检查 **Backend实际部署使用的runtime.env／.env**。旧环境若显式保留 `COURSE_DOCUMENT_UPLOADS_ENABLED=false`，更新代码不会覆盖该值，需要维护人员改为 `true`，并用原项目名、环境文件和Compose文件重新创建后端app容器。只刷新Admin、重启未读取新环境的旧容器或更新学习网站不能使该配置生效。

可在实际后端app容器内只输出该开关进行核对：`python -c "from app.port.config import settings; print(settings.COURSE_DOCUMENT_UPLOADS_ENABLED)"`。应输出 `True`。开关开启后仍保留PDF大小、有效页面及加密／损坏文件校验。

回滚优先关闭PDF上传入口，保留数据、文件和可识别PDF的服务版本。已有文档后禁止降级删字段，迁移 downgrade 明确拒绝；旧视频接口继续保留。本次不部署、不操作线上收款／权益数据。

## 本地验证

Backend：章节管理／文件校验单测，独立PostgreSQL的上传、发布、并发替换、进度与清理测试；从 r60002 的历史视频样本升级核对回填。网站：原有流程、文档权限／进度／笔记测试及 PostgreSQL 旧请求与资源替换并发测试（显式提供 VIDEOWEB_TEST_DATABASE_URL）。Admin与网站使用本地API夹具浏览器测试，覆盖混合上传部分失败重试、PDF续签／分页／恢复／标记已读、桌面与窄屏。

本地夹具验证不能代替线上 OSS CORS、真实大文件网络及生产数据迁移验收。
验证任务还使用 PostgreSQL 事务级 advisory lease：存活的验证进程禁止重入；服务中断后锁自动释放，管理员可通过“检查／恢复”续接原任务。OSS 合并请求响应丢失时，仅在实际对象存在且大小一致后恢复，不重复建章。
