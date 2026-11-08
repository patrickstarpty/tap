# Tapper 真实演示失败后的交接

## 接手先读

**当前镜像已通过完整应用环境下的无模型启动核验，但用户要求的 FWD HK 询价流程尚未验收。** 下文保留原交接时未通过的失败记录；`humor` 场景的技术成功不能替代 FWD HK 目标，也不能据此反推旧失败的精确原因。

用户的目标是：Tapper 根据自然语言完成 FWD HK 询价流程，并展示 **自然语言 → 页面检查 → 生成并执行脚本 → 返回结果、截图和 trace**。用户已明确要求先交付核心闭环，复杂恢复和资产管理后补；一般问题自主处理，大问题商量。用户最初要求由统筹协调、GPT-6 Sol 实施，随后明确要求主 agent 亲自接手；Sol 已停止。

原交接时，用户要求先写交接文档，并从具体阻塞继续，不重走整套设计和评审。

## 最新状态：模型成功响应被内层超时丢弃（2026-11-08）

**FWD 指定资料询价尚未验收通过，正在以修复后的镜像继续。** 用户已明确授权“额度你随便用，但要小心使用”，后续按任务需要自主设置有限分批额度、记录实际费用，不再沿用旧的 20 次调用限制反复请求授权。

- 真实任务 `a6749382959a41a1a659d26302844676` 的容器中，至少 3 个模型响应已经返回 HTTP 200、`finish_reason=tool_calls`，输出分别 1649、1860、1676 tokens，却仍留在邮箱响应目录且没有读取确认。只读脱敏元数据 `/private/tmp/tapper-fwd-live8-unreceived-response-metadata.json`，工具与消息元数据确认执行代理没有收到这些完成的工具调用。没有读取或保存隐藏推理。
- 发现 `model-shim.mjs` 内仍固定等待 30 秒，而宿主转发层已允许按任务期限等待；较长响应完成时，内层已返回 502，造成重复生成与费用浪费。无付费本机 HTTP/邮箱测试在受控时钟第 31 秒重现 502，修复后返回 200 并写读取确认，第 61 秒仍按 60 秒任务期限拒绝。daemon 显式传入任务绝对期限；每次等待受剩余任务期限及邮箱最大 600 秒限制，不改变任务整体时限。
- 35 项 JavaScript 运行时测试、2 项镜像构建校验通过。新编码镜像 `sha256:5d27bf6e46fc0e0b591196fb9f54569355308e74152db2cffd4603bea88e09df`，浏览器镜像仍为 `60cbf673…`；日志 `/private/tmp/tapper-shim-deadline-red.log`、`/private/tmp/tapper-shim-deadline-green.log`、`/private/tmp/tapper-shim-deadline-build.log`。不能将修复验证表述成 FWD 业务已通过。
- 发现根因后通过正常任务 API 取消旧尝试，结算 15 次、USD 0.097568，其中最后一条未核实用量按最高预留 USD 0.0540672 保守计费。累计 277 次、USD 1.621862。证据目录 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-62dcf5a75cdb-evidence-4jnz0fhx`；结算 `/private/tmp/tapper-fwd-live8-settlement.json`。
- 本批自主额度仍设为 60 次 / USD 0.60，扣除上述运行后，下一次以剩余 45 次 / USD 0.502432 预留，ID `res_02f5d635ab27c174232681aa`。正在运行原始 FWD 自然语言验收，日志 `/private/tmp/tapper-fwd-official-refs-live9.log`，运行完成后必须结算与更新本节。验收启动器原固定 20 次上限已调整为明确传入且不超过 60 次，保留 USD 1.0 和 900 秒硬上限，25 项边界测试通过；此变更 `d9c64bfc` 已推送。

## 历史阶段：第二次重启后的 FWD 实测（2026-11-08）

**FWD 指定 30 岁资料的报价和零模型回放仍未验收通过。** 以下为该阶段历史状态。第二次 Docker 重启已获用户单独批准并已执行，不能将这次批准当作后续无限重启许可。

- 重启后按原始精确 ID 核验 14 个共享服务全部运行，有健康检查的服务均 healthy。无网络空容器启动耗时 0.94 秒。本次两个实测专属环境均已退出；最终共享服务与清理收据 `/private/tmp/tapper-fwd-second-restart-final.json`，重启前后收据同前缀 `-before.json`、`-after.json`、`-probe.json`。
- 第一项真实任务 `e7ba31d4bbb04bccbac0a72c17110d88` 自动进入 browser 路由，但模型没有进行任何浏览器动作便声称存在验证码/登录/地域阻塞。浏览器账本实际为 **0 个动作、0 个页面观察**，所以该说法没有证据，不能要求用户处理假设的验证码。此任务 8 次调用、USD 0.019570。代码 `a16f7bbb` 已整合推送到 `develop`：没有真实页面观察的外部阻塞会在原预算内纠正一次，再失败则报告内部执行问题；明确提供合法 BDD 工具示例。51 项定向测试、591 项核心回归及相关静态检查通过。
- 第二项任务 `052dacfdec824c75908bbdb2f028b3c9` 已保存 6 个 BDD 步骤，实际访问 FWD 产品列表和 MyTerm Plus 页面，收到真实 DOM、年龄默认值 25 及表单控件引用。产品列表导航超时仍保留可用 DOM，产品页导航成功。**尚未填写 30 岁等资料，也没有有效报价、冻结脚本或回放**。终态审计明确 `cutoff.cause=calls`，10 次编码调用加 1 次入口分流达到剩余 11 次上限；上层显示的 `OpenCode assistant reported an error` 是粗粒度错误，不能把它当作网站阻塞。
- 最新证据目录 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-33ce36780fc9-evidence-xyqldal1`，含审计、浏览器动作账本、用量和停止收据（`stopped=true`）。脱敏工具回执 `/private/tmp/tapper-fwd-live7-tools-private.json`。模型先写了未完成的脚本骨架，再创建计划并访问页面；骨架不构成已验收脚本。上一项零动作误报证据目录为 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-465457a94a66-evidence-o8l3z5h9`。
- 最新任务按 11 次、USD 0.0336968 核实结算；本轮合计 **20 次、USD 0.0539108**，原定 USD 0.60 尚余 USD 0.5460892，但本轮调用次数已耗尽。累计 **262 次、USD 1.524294**，当前累计调用上限 262；无待结算预留。账本 `/private/tmp/tap-opencode-task03-ledger.jsonl`，收据 `/private/tmp/tapper-fwd-second-restart-settlement.json`。后续付费运行须先取得增加调用次数的授权，不能因费用尚有余量而越过次数上限。
- 核账发现生产网关虽已持久化实际费用，内存仍累计每次最高预留费用，会妨碍后续在原费用上限内继续。已修复为只在真实用量已核实且持久化结算成功后释放差额；未知用量与失败结算保留原最高预留，无持久化回调的诊断探针仍维持原保守规则。未以此扩大任何费用或调用上限。新增本机 HTTP 失败复现先红后绿，86 项网关/运行时测试及 591 项核心回归通过，Ruff/mypy 通过；日志 `/private/tmp/tapper-budget-settlement-red.log`、`/private/tmp/tapper-budget-settlement-green.log`、`/private/tmp/tapper-budget-settlement-core.log`。

继续验收时沿用原始业务输入，通过 Tapper 自然语言入口运行；取得全部输入回读、非零实际 HKD 月保费、命名 JSON、原脚本/截图/trace 与零模型回放，才可标记通过。当前浏览器/编码镜像仍为下节 `60cbf673…` / `c56cac0a…`，新修复均为宿主 Python 逻辑。ADR-038 的未来 browser-use 路径未实现，Codex 侧栏渲染问题未确认修复。

## 历史阶段：公网依赖与真实入口修复（2026-11-08）

**FWD 指定资料询价和零模型回放仍未验收通过。** 下文金额、镜像和“尚未提交”描述均为分阶段历史；本节仅记录该阶段状态。代码 `4c485893` 已快进整合到 `develop`。

- 用户要求取消正常浏览的逐域名许可。新会话及冻结包默认 `public_web`；公开网站、API/CDN 依赖可访问，本机、私网、元数据地址、DNS 重绑定及 HTTPS authority 保护保留。旧包默认 `declared_origins`。迁移 `0008_browser_network_mode`，跨应用边界见 [ADR-039](../decisions/2026-11-08-adr-039-browser-public-web-policy.md)。浏览器镜像 `sha256:60cbf6735f945b534fb0b8aca25e6ee6f257fcb6ee051231c5f2812e5b3a8dce`；编码镜像 `sha256:c56cac0aa6951e9dc2ab9ecbf62c3ee349d6b283c231659bb4e96e806b12c8c5`。
- 真实 FWD 页面依赖 `https://ecapi.fwd.com.hk/data`。仅给起始域名的新策略无模型预检已加载保费：页面默认年龄 **25**，Standard 显示 HK$97.20 与优惠 HK$9.72/month。此数值只是网络依赖已加载的证据，**不是用户指定 30 岁的报价**。预检 `/private/tmp/tapper-fwd-public-web-preflight-20261108.json`，停止收据 true。
- 修复观测包装使模型就绪检查误报失败的问题；193 项启动测试通过。真实 UI 任务 `cd98f1b5f2ab477986ba369a1f4c9188` 随后消耗 1 次、USD 0.000644：模型实际返回 `kind=browser`，但直接返回路由对象，旧解析器只读外层 `route`，导致 `native answer is empty`。已兼容两种形状并明确提示格式，36 项图/调度测试通过。原输出在 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-ab0bd5671009-evidence-0go2zptk/cd98f1b5f2ab477986ba369a1f4c9188-audit.json`。没有生成业务脚本或报价。
- 公网策略与持久化 40 项、客户端 27 项、代理策略 6 项通过；真实 Chromium 隔离测试通过（90.73 秒）。`make check` 通过；路由最终变更另过 Ruff/mypy，最终 `make test` 590 项通过。日志 `/private/tmp/tapper-public-web-final-check.log`、`/private/tmp/tapper-public-web-final-core.log`、`/private/tmp/tapper-public-web-real-runtime.log`。
- 随后 owner `tap-browser-core-d4b2fafe0c84` 在 `compose-up` 300 秒超时，尚未迁移数据库、启动应用或提交模型任务。证据 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-d4b2fafe0c84-evidence-4ew_9r2q`。默认导出保守标记 accounting unconfirmed，因为数据库未启动；阶段证据确认此尝试零调用。Docker 报本轮旧容器 PID 为 zombie；无网络、无挂载的空容器也在 15 秒内不能启动，停在 Created。不能将这一环境失败称为 FWD 业务失败或精确推断 Docker 内部根因。此前单次 Docker 重启授权已使用，本轮未再次重启；14 个原共享服务仍运行。
- 用户“完成它”已承接上轮提出的 20 次 / USD 0.60 有界继续，调用总上限 262、费用总上限 USD 2.1496368。预留 `res_15ba2013b851bee46faf5d97` 已按 1 次、USD 0.000644 结算。累计 **243 次、USD 1.4710272**，本轮还可使用 **19 次、USD 0.599356**；无待结算预留。账本 `/private/tmp/tap-opencode-task03-ledger.jsonl`，收据 `/private/tmp/tapper-fwd-public-web-settlement.json`。再运行需先按剩余额度预留，不重新增加授权。

仍待：Docker 恢复后，用未改动的真实 FWD 业务输入从自然语言入口继续，核实 30 岁等全部回读、非零 HKD 月保费、命名 JSON、原脚本/截图/trace 和零调用回放。不得把主机手动询价、上述默认值预检或合成模型当作通过。ADR-038 的未来 browser-use 路径仍未实现；Codex 侧栏渲染问题仍未确认修复。

## FWD HK 询价：本轮诊断与当前尝试（2026-11-08）

用户明确以 FWD HK MyTerm Plus 公开询价为本次验收目标；产品输入与停止边界以[设计文档 §11.1](../superpowers/specs/2026-11-06-agent-workflows-and-test-loop-design.md)为准。真实 Tapper UI 必须从官网网上投保产品列表选择 MyTerm Plus，使用合成的 30 岁男性、不吸烟、本人、10 年续保、HKD 2,000,000、Standard、按月资料，读取非零实际 HKD 月保费并导出原生成脚本、截图、trace、零模型重放。流程止于公开报价，不创建投保、提交真实个人资料或付款。

第一次 FWD 真实 UI 任务 `19fa4c2b50cd46f48905af26248c86e6` 在 8 次模型调用、USD 0.015843 后返回 `awaiting_input`：浏览器导航官网列表时，`page.goto` 等待完整 `load` 事件到 15 秒而超时。独立浏览器和主机 HTTP 对照均能获取官网页面；因此不能把模型的“站点无法访问”当确证。受控慢子资源复现了已可读取 DOM 而 `load` 尚未完成的情形，浏览器检查器改为在原 15 秒边界等待 `DOMContentLoaded`，并保留真实主文档失败的错误。新镜像的无模型导航已读取官网列表和 MyTerm Plus 产品页。

后两次 FWD 真实 UI 任务 `2d66f930ea5d4420b1b69de54b4d6f15` 和 `53f3a9c83a37416eae178fba53bce119` 均在模型调用前失败，审计均为零调用、无生成脚本；前者 `error_snapshot.reason` 为 `OpenCode serve readiness deadline`，后者为 `coding container command timed out (exec inspect)`。代码核对发现 host 原 20 秒窗口不足以覆盖 daemon 最多 10 秒版本检查加 15 秒 `/doc` 就绪，因此先改为受任务期限裁剪的 30 秒 host 窗口。随后在**完整应用和已激活的 Chromium 检查器并存**时，无模型精确 FWD 配置再次复现失败：daemon 容器运行约 17.639 秒后自行以 ExitCode 1 退出、`OOMKilled=false`，私有固定日志仅有 `OpenCode serve readiness deadline`；host 在 30.929 秒末尾只剩 0.054 秒的单次 inspect 余量，因而最终呈现 `exec inspect` 超时。这个分段证据定位了本次复现的 daemon 内层 15 秒期限，不能反推之前所有失败同因。

最小有界修复将 daemon `/doc` 就绪窗口从 15 秒调整到 30 秒，仍受任务绝对期限限制，单次探针也裁剪到窗口剩余时间；host 窗口从 30 秒调整到 45 秒，仍受任务剩余期限限制。单次 host inspect 最多 3 秒、取消与清理、安全隔离、模型和费用边界均保持原样。受控测试证明 20 秒才就绪可通过、超过绝对期限或窗口尾缘的响应会拒绝；host 测试证明 39 秒才就绪可通过且 8 秒任务期限仍生效。对应 red/green 日志为 `/private/tmp/tapper-fwd-daemon-readiness-behavior-red-20261108.log`、`/private/tmp/tapper-fwd-daemon-readiness-green-20261108.log`、`/private/tmp/tapper-fwd-host45-red-20261108.log`、`/private/tmp/tapper-fwd-host45-green-20261108.log`。最终核心 `make test` 通过（TAP AI Backend 445、AI Web 83、TAP Backend 48、TAP Web 9），日志 `/private/tmp/tapper-fwd-readiness45-make-test-20261108.log`。

同一完整应用及浏览器并存的无模型核验，最终编码镜像在 17.580 秒进入 ready；编码容器真实停止、浏览器取消和专属资源清理均获 `true` 收据。时间线与停止证据在 `/private/tmp/tapper-fwd-fullapp-coding-probe-readiness45-20261108.json` 和 `/private/tmp/tapper-fwd-fullapp-diagnostic-readiness45-20261108.json`。最终同树正式镜像为浏览器 `sha256:36ba59c850364ba782756373f86859d6e60ecfd673af756fb389a6033cf6e388`、编码 `sha256:3991cafd24b67f236410dda27b61e871b5ed2ce2af983c9b7d76721f2c32cdeb`，构建器基础镜像 pin 和[本地运行指南](2026-11-08-tapper-browser-automation-local.md)已同步。

**FWD 业务验收仍未通过。** 上述镜像的真实任务 `454d422a4e384183ae8d7b32960f1a84` 最终失败，节点原因 `timed out`，未生成脚本或业务结果。官网导航等候 `DOMContentLoaded` 超时后返回了空快照；第七次模型响应未核实用量。审计证据目录 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-1556a8a5f51a-evidence-58vl93le`。预留 `res_9d63afcf9978309c2a3410c7` 已按 7 次、USD 0.065321 结算（含未知用量保守计费）。当前累计 **230 次、USD 1.4387792**，上限 **237 次、USD 2.1496368**，剩余 **7 次、USD 0.7108576**，无待结算预留；收据 `/private/tmp/tapper-fwd-readiness45-settlement-20261108.json`。

主 agent 接手后，以受控测试确认并修复两处问题：导航超时仍返回实际已加载页面的标题、正文和表单字段，同时保留错误；模型转发原固定 30 秒超时改为遵守任务剩余期限，原取消与费用控制保留。慢 deferred script 的真实隔离浏览器测试先失败后通过；31 秒模型响应的本地转发测试先失败后通过，完整运行时文件 47 项通过。最终核心回归日志 `/private/tmp/tapper-root-final-make-test-20261108.log`，真实浏览器回归日志 `/private/tmp/tapper-partial-navigation-green-20261108.log`。

最新浏览器镜像 `sha256:e529cf30d07aac30aa8abd4c1aec3c83f0643ab5d7b3008d3b383bda99ccf7d2`，编码镜像 `sha256:0c6ac3c64abaa7f3ef7fae2b26c95065102ea34674d4ee9cc15e2fe63b99958d`，已同步构建器 pin 和本地运行指南。无模型 FWD 检查已读取官网列表（导航超时仍获得真实标题、4,905 字正文和 MyTerm Plus）及产品页（16,000 字受限正文），停止收据 `stopped=true`，见 `/private/tmp/tapper-fwd-root-preflight-20261108.json`。14 个原共享容器 ID 均仍运行，上次真实运行没有遗留运行容器，见 `/private/tmp/tapper-root-shared-check-20261108.json`。

用户随后明确同意把总调用上限从 237 提高到 250 次，总费用上限仍为 USD 2.1496368。已记录授权 `user-approved-root-fwd-250-20261108` 并预留 `res_177b0b367a0ac1172f7b6e56`，主 agent 亲自启动一次 20 次 / USD 0.70 / 600 秒的同业务输入运行；最终任务 `4ac5f78e045b41619751203fe923bcdc` 失败，审计为 12 次、USD 0.031604，预留已结算。当前累计 242/250 次、USD 1.4703832/2.1496368，剩余 8 次、USD 0.6792536，无待结算预留。收据 `/private/tmp/tapper-fwd-root-reservation-20261108.json`，运行日志 `/private/tmp/tapper-fwd-root-real-run-20261108.log`。只有真实页面非零保费、输入回读、原脚本、截图、trace 和零模型重放均核实，才能报告该目标验收通过。代码及本文尚未提交。

## 自动入口、BDD 进度及官方快照（已整合到 develop）

代码 `96f5d951` 已快进整合并推送到 `develop`。原失败阶段的“尚未提交”记录均为历史状态；FWD 业务验收仍未完成。最终自动路由取消 E2E 通过，任务 `47896c05187c41aca73bc4cc0e8dd5b0` 为 canceled 且浏览器 stopped=true。最终核验 14 个共享容器仍运行，本轮隔离资源无运行遗留：`/private/tmp/tapper-auto-bdd-shared-final.json`。

用户要求：默认自然语言自动选择能力，无需 Browser run 或 @Agent；先拆解并持久化 BDD，再执行并同步 Progress。外层按 Codex 的 Outputs / Subagents / Sources，Subagents 展开 Active / Done；每个业务步骤可展开动作目标、定位器、耗时、结果和错误。

实施工作树新增自动 browser 路由，路由与编码共享任务预算，网址限定为用户输入中出现的 URL；冻结意图作为追加事件持久化，快照、恢复与回放统一解析。UI 去掉必选勾选，手动覆盖收进可选项。服务端拒绝无 BDD 计划或无 step id 的浏览器动作。

最新 FWD 失败中实际观察到文本定位器多重匹配与不存在的 Age placeholder，但最终 assistant error 的精确原因仍未知。修复观察器后，错误保留页面状态；现在复用官方 Playwright CLI 在锁定 1.62.1 中的 AI 快照 API 和 `aria-ref` 引用。真实 Chromium 回归已通过引用填写、回读、失败现场、网络隔离、截图及 trace（`/private/tmp/tapper-official-refs-green.log`）。前端进度 37 项通过；工作流/运行时 154 项通过；自动路由到编码及预算定向 15 项通过。`make check` 与核心 `make test` 均通过（587 项）；后续强制生成脚本前已有 BDD 计划的闸门通过运行时 48 项，模拟网关 7 项通过。无费用自动入口、持久化计划、原脚本执行及零模型重放 E2E 已通过，owner `tap-browser-core-5f65106522e9`，证据目录 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-5f65106522e9-evidence-vpopno11`。截图已确认默认 Outputs / Subagents / Sources 同屏，Then 步骤由真实执行收据标为 Passed，原始执行明细默认收起。不能将这些结果称为 FWD 真实全链路通过。

当前构建镜像：浏览器 `sha256:44c27fecee6af8745ca843fe7b06701bfb845a585822b9a5cb6780d9534988fc`，编码 `sha256:2841fda29fbc68534c9257a69b510a2f24cec40eb071b57786e6fd0d335625d7`。剩余付费预算仍为 8 次、USD 0.6792536；尚未再次调用真实模型。真实 FWD 新快照的只读预检已读取年龄 combobox（值 25）及性别、吸烟状态、年期 radio，保存完整官方控件引用，停止收据 true：`/private/tmp/tapper-fwd-official-refs-preflight-20261108.json`。这不构成已完成询价。已询问用户是否把总调用上限从 250 增至 262（本轮最多 20 次、USD 0.60，总费用上限不变）；尚未收到答复前不增加额度。旧 `http://127.0.0.1:52787/` 是已结束验收的临时地址。Codex 侧栏渲染报错仍未确认修复，不应拿浮窗截图当修复证明。

## 本轮恢复与通用链路诊断（2026-11-08）

交接后复现了启动阻塞：当前镜像的生产 `DockerCodingContainer.start` 中，image inspect 完成，`docker run` 在 30 秒边界超时，未取得容器 ID；按唯一名称和 attempt 标签也未找到 Docker 对象。Daemon 同时报告旧专属容器无法停止、主进程为 zombie。用户明确批准本轮重启一次 Docker Desktop；重启后同一当前镜像的无模型生产启动在 12.790 秒内进入 ready，真实停止收据为 `stop_confirmed=true`。这一恢复支持环境故障判断，但不能证明 zombie 是超时的直接原因。重启前后原共享服务 14 个 running ID 均保留；其中三个未自动启动的原 ID 被按原状态恢复，未启动原本已停止的服务。

第一次恢复后的真实 UI 任务 `d3177fa7ced144e787c722ebdd7d0a34` 仍在约 39 秒后失败，零模型调用、无脚本；专属数据库中的 `error_snapshot.reason` 为空，不能确定具体命令阶段。为补足这一取证缺口，实施工作树只修改 `DockerCodingContainer._command` 的超时错误：用受控白名单标记 image inspect、docker run、各 exec 阶段和清理命令，排除参数、配置与密钥；保持原 timeout 数值、异常类型和进程清理。相关单测 43 项通过，Ruff 检查、格式检查和 `git diff --check` 通过；默认 `make test` 核心回归也通过（TAP AI Backend 442、TAP AI Web 83、TAP Backend 48、TAP Web 9 项）。随后当前镜像的无费用合成 `lookup` 场景及零模型重放成功。合成场景的临时取证 wrapper 在最终清理处遇到导入路径错误，已按精确 owner 清理专属资源；这不是产品任务失败，证据保留。

通用 `humor` 诊断使用当前编码镜像 `sha256:b1f5c4df0c79e4e46ab1ac15de56e9bcff9cc2b0d505f5206be9149442e2e811`、原中文自然语言输入和 `14 次 / USD 0.76 / 240 秒` 上限。真实 Tapper UI 任务 `7bcd010033784a36bafda2d1a98ff144` 为 `succeeded`，Browser result 为 `Passed`：实际 URL 为 `https://quotes.toscrape.com/tag/humor/`，页面前两条引文及作者（Jane Austen、Steve Martin）进入命名 JSON 结果。原生成 `flow.spec.ts`、执行截图、trace、UI 截图和结果均已导出；重放任务 `75f6e6cb2ad9440a97994bdcab41fa94` 为 `succeeded` 且没有新增模型调用。统筹已在本机 trace viewer 打开原 trace，核对原脚本、humor 页 DOM、导航/提取/截图步骤，Errors 页无错误；脚本 SHA256 与审计相符。原 runner exit 0，专属 Compose、站点和编码容器均已停净；展示用 UI 在正常清理后关闭。这是通用能力取证，不是 FWD HK 询价验收。

- 本次真实证据目录：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-6d2a8b541c6b-evidence-9hi9tcdg`；重点是两个 task 的 `*-audit.json`、原生成 `*-flow.spec.ts`、`live-result-*`、`live-screenshot-*`、`live-trace-*`、`live-replay.json`、`usage-manifest.json` 和 `node-error-snapshot-private.json`。
- 无模型 ready/stop 收据：`/private/tmp/startup-repro-b1f5c4df0c79.json`；重启前失败收据：`/private/tmp/startup-repro-b1f5c4df0c79-pre-restart.json`；共享服务最终逐 ID 收据：`/private/tmp/tapper-shared-post-real-20261108.json`。
- 费用账本 `/private/tmp/tap-opencode-task03-ledger.jsonl`：本次预留 `res_91aa579665bf178d57612e98` 已按 12 次、USD 0.034756 结算；累计 215 次、USD 1.3576152，上限仍为 237 次、USD 2.1496368，无待结算预留。累计金额含历史保守未知用量，不等于供应商账单。结算收据：`/private/tmp/tapper-real-settlement-20261108.json`。
- 旧失败的精确原因仍未知；通用 `humor` 诊断阶段没有提高超时。后续 FWD 诊断取得完整应用负载下的分段证据后，才作上述受任务期限限制的就绪窗口修复；未撤销安全边界、扩大费用上限或删除旧诊断记录。该阶段两次真实运行均先预留、后结算，分别为 0 次和 12 次调用。改动尚未提交、同步到 `develop` 或推送。

## 原交接时的代码、配置与推送（历史）

- 主目录：`/Users/patrick/Desktop/Workspace/ai-native/tap`，分支 `develop`。
- 实施工作树：`/Users/patrick/.codex/worktrees/tapper-agent-workflows/tap`，分支 `feat/tapper-agent-workflows`。
- 两处 HEAD 均为 `93fb8650d479928669ca0178f6542eebb0e6e45e`。已执行 `git push origin develop` 成功，主目录状态为 `develop...origin/develop`，无领先或落后。没有创建 PR。
- 主目录原有未跟踪文件 `.env.bak-20261015`、`apps/tap-ai-backend/.quality/` 必须保留，不读取、不修改、不暂存。本文是本轮新增、尚未提交的交接文档。
- 用户授权从主目录 `.env` 读取 LiteLLM 配置。不得输出、复制或提交密钥，不修改 `.env`。
- 当前共享应用的浏览器能力默认关闭。演示使用独立资源、真实 Tapper UI/API/worker；没有自动启用共享应用。
- 运行方法见[本地运行指南](2026-11-08-tapper-browser-automation-local.md)，历史证据及七项裁决见[核心验收指南](2026-11-07-tapper-web-automation-core.md)。原交接时，这些文档中的历史成功不能覆盖当时的失败。

原交接时的镜像（历史）：

| 用途 | 精确镜像 ID |
| --- | --- |
| 当前编码镜像 | `sha256:b1f5c4df0c79e4e46ab1ac15de56e9bcff9cc2b0d505f5206be9149442e2e811` |
| 历史成功编码镜像 | `sha256:fae707f8bfa133c1f838d89109a6eb752f53d98aecef781b7a1771233e6021b3` |
| 浏览器镜像 | `sha256:dd59168b4e82204c55f28ba1ac5c269600a0c4e28e4f21faa639485630a1feb1` |

## 原交接时失败的真实演示（历史）

通过 `scripts/run-browser-core-e2e.py --mode real --site public` 启动隔离环境，由真实 UI 提交中文自然语言需求：打开 `https://quotes.toscrape.com/`，找到 `humor` 标签页，提取前两条引文、作者和实际 URL，保存命名 JSON 结果。输入仅含标签和数量，没有提供脚本或选择器。上限为 14 次模型调用、USD 0.76、240 秒。

- 实验 owner：`tap-browser-core-be9bde1dc086`。
- task：`cc5e1616189a4949ae8b52fd65ae0320`；conversation：`fed872839b48be185da77df677f5c238`。
- 节点在 `2026-11-08T02:08:18Z` 左右启动，约 33 秒后失败。实际 UI 显示 `Workflow failed`。
- 审计 `budget_used={}`，模型 action 事件为空，无 bundle、脚本或 Browser execution。没有业务结果、截图或 trace，不能声称本次演示成功。
- Inspector session `bb20cd00b10b4c4d8bf99015952e0df3` 已取消，停机收据 `stopped=true`，浏览器动作数为 0。
- 原演示进程已退出，原临时 UI 已关闭；不应继续使用原 `http://127.0.0.1:51744/` 地址。

保留的本地证据：

```text
运行日志：
/private/tmp/tapper-user-live-demo-20261108.log

证据目录：
/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-be9bde1dc086-evidence-iaul6ijb

环境状态目录：
/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-be9bde1dc086-state-iq29cj54
```

证据目录中重点查看 `live-unexpected-snapshot.json`、`cc5e1616189a4949ae8b52fd65ae0320-audit.json`、`usage-manifest.json`、`browser-session-ledger.json`。日志中的 Docker 清理异常是另一个已观察到的问题，不能直接当作编码启动失败的根因。

## 原交接时的启动阻塞（历史）

GPT-6 Sol 已用相同无模型配置调用生产 `DockerCodingContainer.start` 对比当前及历史镜像，两者分别约 30.065 秒、30.063 秒超时，均未取得 container ID，未到达可确认的 OpenCode ready 状态。事后按两次唯一名称查找，没有找到对应容器；`stop_confirmed=false`，没有伪造停机证明。

**精度限制：首轮记录未区分超时发生在 image inspect 还是 docker run。** 直接 image inspect 很快只能提供线索，不能代替实际命令分段记录。实施者曾准备追加一次分段探针，随后会话被用户中断；交接时未取得它的完成结果。不要把现有 `startup-diagnosis.md` 中“已定位 docker run”或“排除镜像回归”的过强措辞当结论。

诊断工作在实施工作树内，未修改产品代码：

```text
.superpowers/sdd/2026-11-08-tapper-live-demo/startup-diagnosis.md
.superpowers/sdd/2026-11-08-tapper-live-demo/startup-repro-b1f5c4df0c79.json
.superpowers/sdd/2026-11-08-tapper-live-demo/startup-repro-fae707f8bfa1.json
.superpowers/sdd/2026-11-08-tapper-live-demo/startup_repro.py
```

原实施 agent 为 `/root/browser_core_final_fixes`，交接时状态为 `interrupted`，没有重新启动它。统筹只读进程核对未发现 `startup_repro.py` 仍在运行。新会话不能假定旧 agent 可继续使用；以上文件应作为事实来源，并先确认专属资源状态。

Docker 清理的明确异常：daemon 报告容器主进程为 zombie、无法停止。最后观察到以下三个本次演示专属容器仍被 daemon 标记 running；先重新核对状态，不要重复盲目 stop：

| 容器 | 精确 ID |
| --- | --- |
| `tap-browser-core-be9bde1dc086-mysql-1` | `d1277a2ce42e6f1347d2014ef5aa8dd350d5a3ffb5909f834e8a95d7e810bab6` |
| `tap-browser-core-be9bde1dc086-milvus-1` | `667b0184b9e64f3195a79314dd80683b943cfcc650d0fc4175581bf922e673aa` |
| `tap-browser-core-be9bde1dc086-milvus-etcd-1` | `54c8cf43af786cfd3fd79744e200ad6fbae7b2d8bae2a804ff6e9ae9fe862787` |

该项目的 Milvus MinIO 容器 `041c711a829fb192ddfbbe42c1008a7cab6f106c327f2d47c2e4a0fa7d3e9d70` 已由统筹成功停止；其余三个服务先前已被监督器移除。没有重启 Docker、清理共享镜像或删除共享数据。若需重启 Docker，必须说明会中断其他共享服务；此前为磁盘扩容的一次重启许可，不应默认为本轮无限重启许可。

## 原交接时的费用与历史证据边界

- 本次预留 `res_74c7854b0b03640cd0e4bd7d` 已依据完整零调用审计结算为 **0 次调用、USD 0**。
- 累计账本 `/private/tmp/tap-opencode-task03-ledger.jsonl`：已计 203 次、USD 1.3228592，上限 237 次、USD 2.1496368，剩余 34 次、USD 0.8267776，当前无待结算预留。累计数含历史保守未知用量，不等于供应商账单。
- 不要在未证明当前镜像真实 ready 前继续付费尝试。新实验仍须先预留、后按证据结算，不得悄悄扩大额度。
- 历史公共站成功用的是 `fae707…` 编码镜像，12 次调用、USD 0.0384572，并有零模型重跑。当前 `b1f5…` 镜像此前只验证了 20 秒独立到期退出（实际约 20.318 秒）和定向测试，**没有完成当前镜像的成功全链路验收**。
- 最后已提交修复为 `c08af943`（资源创建/取消/期限）和 `1aef6c18`（早期取消停机证明投影）。已有相关测试通过，无需无目的重跑整套 `make check`、`make test` 或重复整分支评审。
- 旧计划临时目录 `.superpowers/sdd/2026-11-07-tapper-web-automation-core/` 的删除被自动审批拒绝，原因是包含不可轻易恢复的未提交审查/验收记录。目录已保留；不要换手段删除。

## 原交接时提出的执行顺序（历史）

1. 确认当前 Git HEAD、镜像 ID、上面诊断的最终分段结果和专属资源状态。不要重读整晚所有记录。
2. 先用无模型生产启动路径确认具体阻塞。若是环境问题，处理环境；若证实代码问题，由 GPT-6 Sol 做最小修复，保留失败复现与对应回归。不要先猜修复、加大超时或反复付费重试。
3. **当前最终镜像必须实际进入 ready。** 同时保留原有限时退出与真实停机要求，不能通过撤掉边界换取成功。
4. 再从 Tapper 真实入口提交上述自然语言任务。展示当前运行的 UI、模型原始生成脚本、真实 DOM 结果、实际截图和可打开的 trace。统筹手动操作目标网站或预写业务脚本不能替代 Tapper 执行。
5. 只有该次当前版本完整运行成功，才报告演示通过；若仍失败，给出准确失败阶段及证据，不引用旧成功掩盖。必要的新修复提交、验证后同步到 `develop` 并推送，遵守用户已授权的推送要求。

原交接时可复制到新会话的提示（历史，不再执行）：

> 请读取 `docs/guides/2026-11-08-tapper-live-demo-handoff.md`。用户验收尚未通过：已推送的当前版本真实演示在模型调用前失败。你统筹，GPT-6 Sol 实施。先核对现有无模型分段诊断，定位并处理启动阻塞；当前镜像真正 ready 后，再通过 Tapper 实际入口演示“自然语言 → 页面检查 → 生成并执行脚本 → 返回结果、截图和 trace”。不要重复整套设计/评审，不要盲目付费重试，不要用历史版本成功或单元测试代替本次验收。带入主目录尚未提交的本交接文档，保留原有 `.env.bak-20261015` 和 `.quality/`。
