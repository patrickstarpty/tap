# Tapper 自然语言 Web 自动化核心闭环

日期：2026-11-07。用户已明确批准优先交付核心闭环，复杂恢复和完整测试资产管理后补。本设计是[整体设计 §11.2](2026-11-06-agent-workflows-and-test-loop-design.md)的受限首版，不代表完整 OpenCode runtime 或正式测试资产闭环已完成。

## 交付行为

用户在 Tapper 提供自然语言目标、起始网址和必要数据。OpenCode 在任务隔离环境内通过受控工具检查真实页面、操作页面并读取反馈，生成 Playwright + TypeScript 脚本。TAP 在新的隔离浏览器环境执行冻结脚本，返回结构化结果、真实截图、trace 与失败原因。重跑使用相同冻结脚本及输入，产生新的执行记录且不调用模型。网页查询、筛选提取、表单核对和多页面操作使用同一产品链路；FWD 仅是例子。

首版属于工作流浏览器任务/调试执行，不创建已发布测试计划、用例或正式测试 Run。运行时仅开放取消；暂停、恢复、节点重试和实时 steer 均由服务端拒绝且界面不展示。必要信息不全时等待输入；验证码或外部限制明确记录阻塞。新输入或修复产生新冻结版本，旧证据保留。

## 模块所有权

TAP AI 的现有 workflow admission、worker、任务/节点/事件和 Chat 投影继续持有任务事实与模型预算。OpenCode 是该任务唯一编码 Orchestrator，外层不增加第二个模型规划器。

TAP 非 AI 后端新增 `browser_automation`，持有浏览器会话、不可变脚本包、执行回执和证据对象，提供项目隔离 HTTP API。它只执行确定性浏览器操作，不持有模型密钥。TAP AI 只保存执行 ID、摘要和引用，不另建脚本或证据主写存储。对象沿用本机文件对象存储适配模式；内容寻址、配额和作用域验证必须保留，后续可换 MinIO 而不改变引用合同。完整资产管理后续消费这些不可变制品，不直接把调试结果当正式通过。

## 进程与工具连接

1. OpenCode 1.19.5 Linux arm64 在任务容器运行，`--network=none`、非 root、cap-drop ALL、no-new-privileges，只有任务工作目录和收发目录。保持 `--pure`、禁用默认插件和个人配置。初版不增加 `chat.headers` 插件；辅助调用的细分归属仍是完整八门禁的未验证项。
2. 固定 Node HTTP shim 只在容器内 127.0.0.1 接收 `/v1/chat/completions`。生产请求正文上限131072字节（128KiB），响应正文上限1048576字节（1MiB）；私有邮箱信封另计base64与元数据余量。它用唯一请求 ID 将有界请求原子写入私有请求目录；宿主监督器经同一任务 BudgetGate 转发到 LiteLLM，将状态、Content-Type 和原始响应字节写入只读响应目录。允许批量返回已验证的 SSE；前端不承诺逐 token 展示。模型原始正文仅存在临时私有传输中，结束立即清理，不进入日志或制品。并发 ID、过期响应、重复请求、符号链接和跨目录路径都必须拒绝。实测默认工具请求32313字节、必要工具裁减后24827字节，真实页面观察后请求13910字节；旧32768字节上限对后续上下文不足，故统一128KiB，且保留有限正数费用、调用及期限上限。
3. OpenCode 通过固定 `browserctl` Node helper 的 bash 调用请求页面工具；同样走任务邮箱，宿主调用 TAP 浏览器 API。支持 navigate、snapshot、click、fill、select、press、wait、screenshot；只接受结构化参数，不提供任意 eval、shell 或宿主路径。工具返回有界真实 DOM/可访问性信息、当前 URL、标签/选项、截图引用和错误。脚本生成使用这些真实观察；站点定位器仅写入本次制品。
4. 宿主通过固定 `docker exec -i` 客户端调用容器内认证的 OpenCode serve，采集持久 session/messages/children 与真实工具事件。宿主不开放新的外部监听端口。
5. 浏览器 inspector 维持一个任务上下文供探索。冻结生成文件后，在全新 execution 容器运行 `@playwright/test` 1.62.1；始终产生受信 reporter、截图和 trace。脚本可以是普通业务流程，用户无需编写测试计划。全部跳过、零测试、缺结果/证据、超时或断言失败均不能显示成功。重跑不复用探索上下文。

## 浏览器隔离

浏览器容器无模型 key、编码邮箱或 Docker socket；只挂载冻结输入只读、证据目录可写。使用独立内部 Docker 网络和任务站点代理。代理只允许冻结目标 origin，解析并固定目的 IP，拒绝非 HTTP(S)、私网/回环/链路本地/metadata 和未授权 origin；所有连接都经过相同校验。自有隔离验收站点使用监督器注入的固定测试目标，不允许请求字段开启私网例外。

仅靠 `--internal` 不足以排除宿主网关可达性。运行容器保持无 capability；短命、任务自有、仅 NET_ADMIN 的设置容器进入该受控网络命名空间，安装只放行代理 IP:port 的 IPv4/IPv6 出站规则，然后退出。不得使用 privileged、host network 或宿主规则修改。规则安装和否定性连通检查通过后才释放浏览器启动；失败则不执行。所有资源按任务 ID/attempt 标签管理，只删除自身资源。

CPU、内存、PID 数、文件数/字节和总时限均有界。浏览器内部 watchdog 与宿主取消共同生效；取消收到请求后先停止关联编码/浏览器容器，确认停机后才投影 canceled。失联且无法确认停止时保留未知/失败状态，不自动重跑副作用。

## 最小合同

`BrowserTaskInput`: goal、start_url、allowed_origins、input_data、expected_result、limits；全部冻结到任务摘要。URL 不含凭据，输入不包含模型 key。首版默认不提供真实账号会话恢复；需要授权时等待用户。

`BrowserObservation`: session_id、sequence、url、title、bounded_snapshot、fields、screenshot_ref、error；每次与请求 ID/摘要绑定。

`BrowserBundle`: bundle_id、project_id、sha256、script_bytes、input_sha256、allowed_origins、created_at。只接受固定入口 `flow.spec.ts` 及结构化 manifest；额外 helper 如有必要必须一同冻结并计入摘要。禁止 symlink/绝对路径/路径穿越，限制体积。不能只靠代码扫描代替容器隔离。

`BrowserExecution`: execution_id、project_id、task_id、bundle_id、input_sha256、status、started_at、finished_at、result、steps、evidence、failure_code。证据有类型、字节数、SHA-256 和作用域对象引用，浏览器与脚本输出无权任意指定宿主文件。成功取决于实际执行和完整证据；模型文字不是回执。

同项目重复 idempotency key 返回原资源；相同 key 不同正文冲突；跨项目查读/取消/证据/重跑拒绝。重跑创建新 execution ID 和任务 lineage，保持原 bundle/input 摘要。HTTP 暴露项目作用域的 sessions/actions、bundles、executions、cancel、evidence；workflow 只投影引用。

## 首版门禁与验证

- 容器与邮箱身份/路径隔离；编码容器无法直接访问网络，所有真实模型调用通过同一限额网关。记录真实任务总计和 usage；未知用量保守收费，不作零费用。
- 调用数、费用、绝对期限覆盖生成与修复的全任务；达到上限立即拒绝新调用。取消和期限结束能停止实际浏览器容器；重跑零模型调用。
- 真实页面观察 → 模型生成新代码 → 独立浏览器执行 → 持久结果与证据 → 产品重跑，全链路从 Tapper 发起。
- 至少两个站点、三类流程，其中一个站点/任务组合在实现完成后选择；不修改产品代码。默认测试仅使用隔离自有站点，第三方真实验收单独 opt-in。
- 缺输入、错误预期、不可达目标、取消、预算耗尽、跨项目和被拦截网络均有明确非成功结果。

完整八项 OpenCode 矩阵保持真实状态，不复用首版验收宣称复杂恢复已支持。本机回环、不扩大 LAN/生产范围。

## 已核对构建输入与已知限制

Node 22.22.0、`@playwright/test` 1.62.1 与仓库锁文件一致。首版只装 Chromium；官方多浏览器镜像在当前 Docker 磁盘上两次空间不足，改用固定摘要 Node slim 基础镜像。构建时下载依赖，固定 npm integrity/浏览器 revision 并记录最终镜像摘要；任务运行禁止下载依赖。

OpenCode 官方 [v1.19.5 release](https://github.com/anomalyco/opencode/releases/tag/v1.19.5) 的 `opencode-linux-arm64.tar.gz` 为 78513091 bytes，SHA-256 `fae5be262d693b7c5e562856cc473e59e741772936db0c0600ac0d5ed5bd088b`；已验证解包 binary SHA-256 `dab85b46f4ea4813da5367997f99a60aab64a2118d58ebf552980e792747436c`。Linux 容器 `--version`、浏览器 sandbox 和出站规则仍须实测，不能复用 macOS 证明。
