# Tapper V0 文档解析隔离验收

评审日期：2026-09-06。结论：**Task 5A 实现与定向验收通过**，源码提交 `963c700`。独立初审两项 Important 与 fix1 引入的一项恢复记录问题均已修复，fix2 范围复审 Approved，最终 make check 退出 0。V0 完整出口继续待 Task 5B；RFC-009 保持 accepted，实施计划保持 active。

## 实现范围

应用通过 async DocumentParserPort/私有 Unix socket 调用独立宿主监督进程，Ingestion 继续使用既有 lease/heartbeat settlement。只有监督进程具有固定本机 Docker 控制能力；每次解析创建新的无网络容器，不在 API/Worker 内同步解析。已确认的 FWD 浅色 TapProductPrototype/Library 继续作为上传入口。

监督进程严格读取一个 Compose job service，映射全部固定设置到明确 attach 的 docker create，并在实际 CID 上再次核验。容器非 root、只读、无 host mount/端口/Docker socket/Secret；固定 1 CPU、512 MiB 内存与 swap、16 PID、32 MiB tmpfs、64 fd。PID1 独立寿命 35 秒、解析预算 25 秒；child CPU 8/10 秒、地址空间 384 MiB。成功、失败或取消都等待容器实际停止/删除及本地 CLI/pipe 回收；最多 15 秒无法确认清理则保留 unresolved 并拒绝新任务。同宿主用户和 Docker 管理员仍是可信边界。

`.tapper/parser-runtime/<validated-compose-project>` 持久保存非敏感 owner/project/image 关联，以排他 flock 防止并发启动。冷启动先按旧 owner/已验证 image 精确回收，随后才绑定当前镜像；相同关联保持 inode/mtime/字节不变，新建和重绑经过私有同目录临时文件、flush/fsync 和原子替换；替换前失败仍保留旧 owner/image。进程被杀可能留下未使用的私有临时文件，不作为恢复身份，也不声明硬件断电持久性。创建前使旧 cleanup 证明失效，清理后再次查询为空才记录成功。SIGKILL、非零退出和缺少 unresolved 文件都不是清理证明。实际 self-test、socket bind 后发布当前 PID readiness，旧 marker/socket 不能使新进程冒充就绪。短 UDS 路径使用可信 UID/状态路径派生的私有目录；socket-path/cleanup-only 仅是固定宿主启动工具。正常关闭保留小型关联记录；无名义上的前缀清扫。

专用 APIRoute 在 FastAPI File 读取前限制真实 multipart 流：文件 25 MiB、总 envelope 额外 64 KiB、单文件/零附加字段、头 8 KiB/16 项、接收 30 秒、每 API 最多两项并发。首个真实终止边界后拒绝 epilogue，保持精确 Origin、authority403/correlation，失败/取消关闭 spool。UDS 使用有界长度帧、封闭身份与摘要；正常半关闭不误判取消，截断/重复键/尾随字节拒绝。

PDF/DOCX 防护包括页数/xref/深度/解压后大小、ZIP 实际总量与压缩比、路径/重复项、宏/OLE/ActiveX/外部关系及编码感知 DTD/entity 拒绝。输出继续使用 canonical normalized codec，绑定源身份，并限制字符/块/heading/实际编码字节。普通 PDF/DOCX/MD/TXT 无 OCR 行为保持。

## 镜像与实际验证边界

实际 linux/arm64 image 为 `sha256:c76c60f9539202ee10caa3238f0a7d9e0f61f39baebbb21dae9f68fe57272cf2`，payload SHA-256 为 `02f9664b2deb9e4ff4cafb268ef58b685266dc7072301304b06eefbac985a99a`。固定 Python 3.13.12 slim-bookworm arm64 manifest `34b27ac66ecf318887b55ea3c71f0db9307895efe631210231a66cc3fa130cf9`，仅由 uv.lock 派生四个解析 wheel，以明确的小构建上下文及离线 hash 校验安装。主机获取构建输入与容器运行无网络是不同阶段。fix1 未改变镜像输入且重新验证了实际镜像；amd64 仅固定输入，未运行验收。

故障探针仅使用独立测试镜像的固定 child，沿用生产 PID1/协议/预算，不向生产请求增加故障开关。网络测试使用 loopback 和文档地址，验证实际 flags/route/connect；内核自带 DOWN tunnel 模板不等于外部连接能力。未修改宿主 sysctl。测试镜像清理使用 `--no-prune`，避免连带删除生产 parent。

## 验证记录

| 验证 | 实际结果与限制 |
| --- | --- |
| 初始 literal 与格式 RED | 缺模块/契约等 11 failed/2 passed；格式规则 10 failed/8 passed；保留实际失败 |
| 初始定向与真实容器 | 428 passed/72.16 秒、6 条既有 Alembic 警告；真实隔离 14 passed/207.96 秒，含普通/恶意格式、实际限制/native OOM、取消和监督进程死亡 |
| fix1 multipart | 实际 FastAPI Route RED 4 failed/5 passed；完整 HTTP 安全模块 GREEN 34 passed/1.23 秒 |
| fix1 状态与旧启动器 | 状态 RED 2 failed→2 passed；精确旧启动器 RED 丢失部署状态，旧 helper RED 未在 SIGKILL 后抛错；初始 harness 导入错误另行保留 |
| fix1 受影响模块 | HTTP/parser/demo-command 完整三模块 162 passed/69.04 秒；初始 sandbox UDS/cache 权限失败保留，授权环境复跑通过 |
| fix1 真实生命周期 | 8 passed/147.66 秒，定向覆盖 timeout/native OOM/broker 与 UDS 取消/半关闭/截断/helper 和实际 launcher 冷恢复；最后增强本地 PID 消失断言的 launcher 1 passed/20.91 秒 |
| 架构回归修正 | 完整 Backend 唯一失败来自旧 native-process allowlist；窄 RED 1 failed/6 passed，完整架构文件 GREEN 80 passed/2.53 秒，保持精确两个能力入口和应用禁止导入监督进程/Docker 的约束 |
| 原始完整 Backend | 2764 passed、1 failed、9 skipped、6 条既有警告，1234.72 秒；在 fix1 前启动，子进程可能读取后续源码，不冒称最终冻结源码全套通过。失败由上行最终架构验证覆盖 |
| fix2 关联写入 | 原 RED 3 failed（1 项真实截断注入、2 项缺 seam）；fsync/replace 前失败及相同内容不写的 GREEN 3 passed；完整契约 38 passed/0.29 秒，真实 helper/launcher 冷恢复 2 passed/22.66 秒 |
| 完整 Web | 294 passed/17 文件/24.26 秒；fix1 无 Web 源码变化 |
| 最终静态与构建 | 变更 Python Ruff/format/type、shell syntax、diff 已通过；Root fix2 最终 make check 退出 0，含 Web build/brand |
| E2E | fix1 最终四阶段 2/1/1/28 通过；每阶段零 fail/skip/flaky/retry，cleanup complete，Root 核对必需原生 spec/终端断言及摘要；28 项持久检查 3.68 秒、2 条既有警告 |

真实 launcher 用生产启动脚本、监督进程、Docker/image/job/self-test/回收；API/Relay/worker/Web 和其 readiness 是明确的 command doubles。完整 E2E 另用真实独占 MySQL/MinIO/Milvus 与确定性 fake models，不代表真实模型质量、企业 Azure ACL、真实 Milvus operator rebuild 或生产/LAN 就绪。

完整 Backend 的 9 个 skip 是四项独立 Milvus gate、Entra、企业 Azure ACL、独立 E2E 持久验证、Codex capability 和真实模型 smoke。没有通过删除这些用例制造全仓零 skip；V0 的必需零 skip 证据由 Task 5B 显式选取。Root broad wrapper 已确认清理，独立精确标签的 container/network/volume 九项查询均无遗留。

## 审查修正与保留限制

独立初审两个 Important：同 chunk 内重复终止边界可绕过 epilogue 拒绝；实际 launcher/helper 在监督进程死亡后丢失 owner，旧对象重用测试无法证明冷恢复。fix1 关闭 multipart 和普通冷恢复问题；范围复审发现关联 JSON 原地截断重写可能在进程异常终止时丢失旧 image。fix2 要求保持相同记录不变，新建/重绑以私有临时文件原子替换，并验证替换前失败仍保留旧 owner/image。修复与有界故障注入、真实冷恢复已通过，最终范围复审 Approved，无剩余审查阻塞。

早期真实测试还发现 rlimit 未覆盖统一 child spawn、库异常未映射安全 400、UDS 取消时容器与本地 attach CLI 未收敛。修正为固定 PID1 spawn 限制、关闭 spool 的封闭错误、owned CLI process group 终止与有界 pipe 排空/reap；保留原 RED/GREEN 和时序。未增加 sleep 或放宽安全预算来通过测试。

保留生产镜像和 ignored build receipt；不提交本地 Secret、原生业务内容或私有 E2E state。最终 32 个源码 hash 与提交一致；fix2 只变更宿主关联元数据写入及契约测试，使用定向契约/真实冷恢复验证，不冒称 fix1 完整 E2E 是 fix2 的逐字节重跑。Task 5B 通过前不进入 V1。


相关记录：[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)、[前置对象存储验收](2026-09-06-tapper-v0-object-storage-review.md)。本项不改变 V1–P1 的交付边界。
