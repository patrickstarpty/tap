# Tapper OpenCode 原生工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复用 OpenCode 主循环完成现场浏览器任务，按真实工具反馈纠错，并保存独立回放资产。

**Architecture:** 复用现有 CodingRuntime、OpenCode server 会话、预算网关和 Browser API。原生插件将浏览器动作直接送入已有邮箱；TAP 不增加推理或压缩引擎。现场完成与后续零模型回放分别记录。

**Tech Stack:** OpenCode 1.19.5、Node.js ESM、Python/FastAPI、Playwright 1.62.1、现有 LiteLLM/qwen3.8-max。

**Spec:** [已确认设计](../specs/2026-11-08-tapper-agent-feedback-loop-design.md)

## Global Constraints

- 继续原隔离工作区，保留已有未提交改造，不读取备份凭据。
- 不新增包、不升级 OpenCode、不自建通用 Agent 循环或压缩器。
- 原生工具必须保留完整错误和当前页面事实；禁止未知副作用盲重试。
- BDD 冻结目标、输入和验收，定位与交互方法可调整；实际读值核验完成才记录现场成功。
- 任务预算、取消、项目隔离及冻结零模型回放保持有效。
- 完整跨进程恢复、直接框架对照及能力对标是独立验收项，不以接入成功冒充完成。

### Task 1: 原生工具和上下文配置

**Files:** `deploy/browser/browser-tools.mjs`（新）、`browserctl.mjs`、`model-shim.mjs`、`Dockerfile.coding`、`scripts/build_coding_image.py`、`opencode_profiles.py` 及对应测试。

**Interfaces:** 插件导出 `default async () => ({tool:{tap_browser}})`；工具参数 `{action}`，执行复用 `mailboxRequest(root, "browser", action, 110000, signal)`，输出 JSON 包含原观察。权限调用 `context.ask`，不让子 Agent 共享可写页面。

- [x] 添加并先运行工具集成测试：从真实临时邮箱收发含错误的长观察，断言错误、读值及尾部控件保留；非法 action 和已取消调用不得派发。
- [x] 最小实现：以锁定版本支持的 JSON Schema 注册工具，复用校验与邮箱；超时不自动重发。
- [x] 恢复原生 `todowrite`/`skill` 权限；显式启用原生自动压缩，输入窗口维持 129024 token；按用户补充要求将请求/邮箱/会话读取上限提高到 8/12/16 MiB，移除 100 条消息限制并识别原生压缩续接。
- [x] 将插件随固定镜像打包；验证真正 OpenCode 1.19.5 能加载 Schema，不调用付费模型。

```javascript
const plugin = await BrowserTools();
const receipt = JSON.parse(await plugin.tool.tap_browser.execute({action}, context));
assert.equal(receipt.error, 'strict mode violation');
assert.equal(receipt.fields.at(-1).selector, '#last-control');
```

### Task 2: 主会话和现场完成

**Files:** `opencode_runtime.py`、`browser_steps.py`、`tapper_runtime.py` 及对应单元测试。

**Interfaces:** `CodingConfig.live_browser` 区分现场完成与历史编码验证入口；worker 使用 `CodingRuntime` 的现场模式。`BrowserProgress` 保存最近有效 Then 读值证据；写动作使核验失效。完成后调用已有 `finish`，再使用已有 `/executions` 的 `live_session_id`/`finish_action_id` 保存实际结果，绝不调用 `browser.execute` 重做业务。

- [x] 添加失败测试：现场读值完成后保存 bundle 和 live receipt，无自动执行；缺核验时回到同一会话，写动作后旧核验无效；取消仍确认清理。
- [x] 改提示为业务任务与资产要求，操作协议由原生工具 Schema 提供，移除 shell JSON 指令。
- [x] 接通已存在的现场 finish/证据路径，保留原录像及失败录像。脚本生成失败不能冒充业务失败或触发第二次业务。
- [x] 恢复默认 OpenCode 入口和固定镜像校验；旧 browser-use 实现保留兼容但不再默认。

```python
assert result.status == "passed"
assert browser.executions == []
assert result.terminal_evidence["result"]["outputs"]
assert result.replay_model_calls == 0
```

### Task 3: 真实框架和完整链路验证

**Files:** 现有 `scripts/run-browser-core-e2e.py`、`deploy/browser/test/`、`apps/tap-ai-frontend/tests/e2e/browser-core-live.spec.ts`，仅按新工具协议调整合成模型脚本。

- [x] 运行 `node --test deploy/browser/test/*.test.mjs` 与受影响 Python 测试。
- [x] 用固定二进制和合成响应验证多轮工具调用、实际错误反馈、原会话继续；合成模型只验证接线，不记作智能验收。
- [x] 执行隔离浏览器 UI 完整链路及零模型回放，保留真实执行服务证据。
- [x] 本地门禁通过后有界运行 FWD/AIA，记录实际模型、调用数、费用和失败阶段；失败先诊断，禁止同配置盲跑。
- [x] 运行 `make test`、`make check`（必要跨模块门禁）；不重复已通过且代码未变化的检查。

### Task 4: 文档与交付

**Files:** `docs/architecture.md`、ADR-038、产品路线图、本计划。

- [x] 更新默认职责、现场完成/回放边界和真实验证状态；V1 范围保持不变。
- [x] 自审 diff，运行 `git diff --check`，检查文档链接和 Markdown。
- [ ] 仅在实际验收达标后提交集成；未达标明确保留阻塞及证据，不能把本地测试当作 FWD/AIA 通过。

## 验证记录（2026-11-08）

- 固定 OpenCode 1.19.5 实际加载 `tap_browser` 成功；只读配置避免离线安装等待，不增加依赖。
- Node 工具与会话测试 42 项通过；核心回归 `make test` 607 项通过；`make check` 通过。随后补充业务/脚本结果分离保护，运行时 54 项通过，受影响文件 Ruff 与 mypy 通过。
- 查询场景使用真实 OpenCode、真实浏览器及合成模型通过；实际定位失败反馈后续接成功，单独回放成功且模型调用为零。完整六场景曾在表单任务启动阶段被本地 API 5 秒超时阻塞；修复后表单、查询及查询零模型回放已通过，多页面、错误预期、缺输入与取消也全部通过。当时 FWD/AIA 尚未重跑，启动曾被自动审批拦截；用户后续已明确授权，见下方实跑记录。不把合成模型结果计作模型能力验收。
- 请求 8 MiB、base64 邮箱 12 MiB、会话读取 16 MiB 是 TAP 的传输保护值，并非 OpenCode 官方统一限额；保留 129024 token 模型输入窗口及原生压缩。8 MiB 边界、超限拒绝、241 条消息历史已经覆盖。

- 连续任务诊断复现本地 OpenCode API 5 秒超时。默认本地 API 等待调整至 30 秒，任务总体截止仍生效，不自动重发创建/提示请求；真实延迟 HTTP 测试覆盖成功与短截止终止。更新固定镜像后六场景通过。

- 完整合成模型验收证据：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-5d6dd86415e0-evidence-zhtuuyw5`；镜像 `sha256:e004a712df1dd3bc0629a7aed204c496351fbfd53cf1401d1417bb7645b25cf0`。
- FWD/AIA 付费启动被自动审批拒绝：要求明确授权主工作区凭据、外部模型与网站数据目的地及预算。本次未创建费用预约、未启动临时模型网关、未新增模型费用。代码未提交，真实验收保持未通过。

## 已授权的真实验收参数

- 使用主工作区 `.env` 中已有 DashScope 凭据，经临时 LiteLLM 网关调用 `qwen3.8-max`；凭据不进入编码容器或验收文档，结束删除临时凭据与网关。
- FWD：从 `https://www.fwd.com.hk/online-insurance/en/` 选择 MyTerm Plus。合成输入为年龄 30 岁、男性、不吸烟、本人询价、10 年续保、HKD 2,000,000、Standard、月付。
- AIA：从 `https://www.aia.com.hk/en/products/health/cancer-care-essence` 获取 Cancer Care Essence 报价。合成输入为出生日期 08-11-1996、男性、不吸烟、HKD 500,000、优惠码留空。
- 任务文字及页面工具观察将发送给模型供应商，报价参数提交至上述官网。仅获取公开报价，遇到登录、验证码、真实资料或正式投保则停止。
- 每站最多 60 次模型调用、$1；总计最多 120 次、$2，逐站结算后继续。成功后验证冻结脚本回放，回放不调用模型。

## 真实验收首轮与兼容修复

用户已明确授权“启动真实验收”。FWD20、AIA06 均完成有界实跑但未通过：分别 3 次调用、$0.0232644 与 $0.0235000，全部费用已结算，临时模型网关与凭据已清理。入口路由沿已有 `qwen-plus`，OpenCode 主会话使用 `qwen3.8-max`。

AIA 的原始 OpenCode 错误为本地模型网关 HTTP 400，发生在成功 `read`、`todowrite` 后的下一轮请求。使用固定二进制与本地合成响应复现：OpenCode 历史 assistant 消息包含 `reasoning_content`，TAP 校验器不允许该字段。最小修复仅允许 assistant 的文本 reasoning_content 并完整转发；角色、类型、模型身份及预算限制不变。原生记录请求通过，HTTP 回归先失败后通过，定向 11 项与既有网关/探针 66 项通过，Ruff/mypy 通过。

FWD21/AIA07 在原授权剩余额度内继续，每站最多 57 次调用、$0.97；连同首轮不超过每站 60 次/$1。真实业务和回放尚待最终回执，不将兼容修复测试视为真实验收通过。


## 真实验收第二轮与运行镜像校验（2026-11-09）

FWD21、AIA07 分别 16 次/$0.3387644、12 次/$0.2576864，均在首次官网导航后返回 unknown，人工通过正式取消接口停止，费用全部结算。连同 FWD20/AIA06，本轮累计 34 次/$0.6432152；未取得合格报价，业务和零模型回放均未通过。两个临时模型网关与凭据已清理。

零模型独立浏览器探针复现 `inspector timeout`：实际固定镜像 bb908b9c 中 ctl.mjs 仍是 20 秒超时，当前源码已是 90 秒。重建浏览器镜像为 `sha256:53f540356af50e0b8a27c779e8054a73235c85567d447e79eb68efc4798df3a6` 后，AIA 起始页返回实际页面观察且 error=null。OpenCode 镜像同步重建为 `sha256:aaf4783000b66c800b1377a293e41f2288c46369e5b198d375a7716a5beb1a87`。新启动门禁逐文件比对镜像内浏览器脚本和当前源码；真实旧镜像拒绝、新镜像通过，相关 4 项测试通过。

FWD22 在启动前模块导入阶段失败，尚未启动任何模型任务，按零调用/零费用结算；入口改用绝对文件加载同一检查模块。FWD23/AIA08 继续原授权剩余额度，分别最多 41 次/$0.63、45 次/$0.71。启动页零模型探针只验证运行环境，不代替完整报价验收。


## 本次真实验收结论（2026-11-09）

**FWD、AIA 均未验收通过，代码未提交。** 本次用户授权后的累计为 43 次模型调用、$0.9944464：FWD 28 次/$0.7132600，AIA 15 次/$0.2811864，均未超过每站 60 次/$1。所有模型调用已结算；FWD22 与 AIA08 在启动前失败，没有模型调用，按有据可查的零费用释放预约。

- FWD23：9 次/$0.3512312。新版镜像成功导航到官网，读取 MyTerm Plus，首次点击因目标区域校验失败；OpenCode 在同一会话修正定位后进入产品页。随后本地网关 HTTP 429，未完成报价或脚本回放。单次实际输入从 1,737 增至 54,837 token，最后一次调用 $0.112344。原始审计中的 browser.coding_result.usage 已保存 cutoff.cause=cost、upstream_failure=false，确认是本地费用准入：每次以最大 129,024 输入 token 和 4,096 输出 token 预留 $0.282624，而该次剩余额度为 $0.278768。此前“未保存直接原因”的总结有误；截断后另有 6 次请求被本地拦截，未转发供应商。
- AIA08：Docker 镜像源码核对在 30 秒内未完成，尚未启动应用/模型任务，费用为零。此前 AIA07 的导航 unknown 已由零模型探针定位为旧镜像问题；新版镜像已能打开 AIA 官网，但没有完整真实模型报价通过证据。
- 新增镜像/入口定向测试 5 项通过，旧合成测试适配真实原生工具协议和必需输入，保留探针不消耗业务步骤的断言。Ruff 与 diff whitespace 检查通过；没有把此前 607 核心回归计作本次最新树复验。
- 本轮临时模型网关和凭据已删除。FWD23 的独立 compose 清理遇到 Docker zombie；补充清理已移除 Milvus、MySQL、etcd，剩余 `tap-browser-core-7b26cf53d4f5-milvus-minio-1` 被 Docker 拒绝移除。未重启共享 Docker，遗留数据卷保留。
- 原始证据：FWD23 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-7b26cf53d4f5-evidence-5zfgfrid`；AIA08 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-72fba9791052-evidence-97m2a9ul`；本轮分项账单 `/private/tmp/tapper-native-real-acceptance-final.json`。官网导航探针不代替完整业务验收。


## 定向修复与节约验收（2026-11-09）

用户授权自主修复、Docker 重启和定向清理、必要真实模型调用；本次执行先设新增费用合计 $1 上限，逐站结算，未结算不得继续。

- [x] 浏览器工具保留服务端完整证据，并在任务工作区保存私有、可按行读取的完整 JSON；发给模型的视图省略重复控件表，页面快照最多 6,000 字符，成功读值仅带实际值和证据引用。错误、动作身份与观察版本保留；磁盘失败返回完整原回执，不能把已完成动作变成可疑失败。
- [x] 本地预算拒绝返回安全的结构化原因；模型适配层把不可重试的任务预算拒绝映射为 402，供应商 429 原样保留；原生终止回执和工作流事件保留 budget_cost 等原因。
- [x] 镜像校验容器使用唯一名称，CLI 超时后定向清理；重启 Docker，清理 83 个历史独立验收容器，恢复重启前 14 个共享运行容器，保留全部数据卷。
- [x] 原始 FWD23 四次回执离线对照：模型视图由 44,521/46,812/46,793/47,567 bytes 降至 6,806/640/6,844/6,799 bytes；完整存盘内容逐条相等。该指标是工具输出字节，不冒充模型 token 或实际费用。
- [x] 固定 OpenCode 1.19.5 合成多轮 reasoning_content 续接完成；预算拒绝探针直接得到 budget_cost 终态，容器停止确认，未调用付费模型。
- [x] 六个完整合成场景及查询场景独立零模型回放通过；新增崩溃处理后的最终镜像也再次通过查询闭环及独立零模型回放，结果、截图、trace、录像完整。
- [x] FWD24/AIA09 已有界实跑并全部结算，合计 27 次调用、$0.7293028，低于本次 $1 上限；临时网关和凭据已清理。
- [x] 真实空白页强制 renderer crash 复现“成功但空快照”，修复为明确崩溃错误；运行时将结果标记 unknown，停止后续模型回合，不重做动作。崩溃时录像仍保存，正常 finish 不查询已经关闭的页面。生命周期审查的三个问题均以失败回归复现后修复。
- [ ] FWD/AIA 完整报价及对应独立零模型回放通过；当前仍未取得报价，不提交、不合入。

费用预留仍以已验证的最大模型输入窗口保守计算，未用未经验证的 token 估算降低预算保护。优先减少重复输入；跨进程恢复、长上下文压缩和直接框架对照仍不冒充完成。


本次真实验收明细：

| 场景 | 调用数（含路由） | 已结算费用 | 实际推进与终止原因 |
| --- | --- | --- | --- |
| FWD24 | 16 | $0.5115672 | 进入产品列表页并纠正产品标题定位，未成功进入目标产品详情页；点击受营销弹层遮挡期间 renderer crash。旧反馈把后续快照误报成功，模型继续尝试后触及本地费用预留边界；本次随后修复崩溃误报与继续调用。未取得报价。 |
| AIA09 | 11 | $0.2177356 | Docker 启动成功并进入 Cancer Care Essence 页；读取 `h1` 因 10 个匹配失败，未完成有效纠错即触及本地费用预留边界。未取得报价。 |

两次 audit 均为 `cutoff.cause=cost`、`upstream_failure=false`，预算截止后的拒绝各 1 次，不再被原生会话当供应商限流连续重试。保留最大窗口预留意味着剩余余额不足以预留下一次最坏费用时会提前停止，不能把剩余余额当作可继续执行的额度。FWD 崩溃检查未发现容器 OOM 计数，尚不能确认 renderer 自身崩溃根因；已修的是崩溃识别、停止及证据保留，不能声称修复了网站崩溃本身。

验证与本机证据：

- `make test`：611 项通过（456 AI Python、83 AI Web、63 Backend、9 Web）；`make check` 通过。最终 Node 工具回归 49 项通过；崩溃停止相关 Python 定向回归 62 项通过。
- 最终浏览器真实崩溃与录像验证通过：`TAP_RUN_BROWSER_RUNTIME_INTEGRATION=1` 下运行 `test_crashed_inspector_never_reports_successful_empty_snapshot`，无外部网站和付费模型调用。
- 六场景合成证据：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-573949fc6552-evidence-qqgdw2n6`。
- FWD24：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-81dc28823c73-evidence-4wfs3c78`；AIA09：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-4da8cefdb76c-evidence-p3aa3c5e`。两者预留事件全部结算，无未结算模型动作。
- 最终 browser image：`sha256:7b8133ca22e829241c4d405c4146ae6b000150bf9b1e61bdd462b05c4a812117`；coding image：`sha256:5bb5886eeed626409c57ac826115e3a28f2fd4cf31b8cf54c6c4c635e7ff25a0`。

- 最终镜像查询闭环及独立零模型回放通过：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-5a3cd7c144fe-evidence-2z3fkp9c`；验收独立容器已清理。


## Mac 本地浏览器对照（2026-11-09）

用户要求暂停 Docker 路径，先在 Mac local 跑通流程。本轮由 Codex Browser Use 直接操作 Mac 上的 Codex 内置浏览器，使用既有虚构测试输入，仅到公开报价结果；没有提交投保或付款，没有启动 Docker 验收，也没有调用此前 DashScope/Qwen 网关。

| 网站 | 测试输入 | 可见报价结果 | 本地操作路径 |
| --- | --- | --- | --- |
| FWD MyTerm Plus | 本人、男性、不吸烟、30 岁、10 年续保、HK$2,000,000、Standard 月付 | 标准价 HK$106.20/月；网站自动应用 GOODGA，首年优惠 HK$10.62/月 | 从产品列表点击 Product details & Quote，进入产品页；年龄是只读下拉控件，展开后选择 30，等待报价更新；其余条件核对已有选中值。 |
| AIA Cancer Care Essence | 出生日期 08-11-1996、男性、不吸烟、HK$500,000、无优惠码 | Monthly Premium HK$45.00 | 产品页 Buy Now 进入报价表单；日期控件选择 1996 年、11 月 8 日；选择 Male、No，填写保额；Get a quote 到 product-breakdown 结果页。 |

FWD 结果页：https://www.fwd.com.hk/online-insurance/term-life/en/ 。AIA 结果页：https://www.aia.com.hk/en/products/health/cancer-care-essence/buy-online/product-breakdown 。报价是本次页面显示的参考价格，FWD 页面说明另收保费征费。两个结果标签页均保留供用户核对。

本地直接操作两站均取得报价，未遇到先前 renderer crash 或定位纠错循环。这证明当前网站流程可通过本地浏览器完成；本轮同时改变了执行环境、模型和工具反馈方式，尚不能单独认定 Docker 是根因，也不能计为 Tapper/OpenCode 本地运行或生成脚本回放验收通过。后续执行方向遵循用户要求，先以 Mac 本地流程为基线，暂停 Docker 实跑。


## Tapper 本地执行适配（2026-11-09）

用户澄清目标为开发 Tapper 自身的 E2E 自动化能力，要求先在 Mac 本地调试 Tapper；Codex 手动浏览器结果仅作网站可达性对照。新增显式 local 适配，保留任务调度、冻结输入、原生工具、模型预算、结果证据和独立回放合同，不另建 Agent 引擎。

- [x] 固定 OpenCode 1.19.5 本机进程适配、私有配置与工作区、受限 macOS 沙箱和同一工具邮箱；本地就绪零模型探针通过。只读配置避免启动时自动安装依赖。
- [x] Browser worker 本机 Playwright 现场执行、公开网络代理、证据收集及冻结脚本独立零模型回放；真实 Chromium 自有站集成探针通过。
- [x] Tapper 配置与完整 UI 验收入口接入 local 开关；默认 Docker 不变，验收数据服务仍用已有隔离 Docker 服务。开发模式不承诺 Docker CPU/内存/PID 隔离。
- [x] 完整 Tapper UI → 调度 → 本机 OpenCode/浏览器 → 证据 → 独立回放合成验收。
- [ ] 两站真实模型报价及独立零模型回放；使用现有较低费率 qwen-plus，每站先限 40 次/$0.50，逐站结算，不重复收费调试已知环境故障。

本机模式配置与原运行时定向回归 251 项通过；两条新增配置回归先失败后通过。浏览器适配真实运行、进程取消与期限探针通过，具体完整验收结果在后续记录，不以适配器探针代替。

本机完整查询验收与独立零模型回放通过：`/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-2358cdda4097-evidence-9hgtijyf`。实际查询结果为 `Ada / west / ORDER-F58AD982`；冻结脚本在另一任务回放成功，模型计数未增加，结果/截图/trace/独立录像均保存。本轮使用合成模型，不计为 FWD/AIA 真实报价验收。

首轮本机 UI 已成功操作页面和读值，但 Mac 默认 zsh 的 heredoc 尝试写全局临时目录被任务沙箱拒绝，生成空脚本，收集失败。两轮原生工具最小探针复现后，私有环境固定 `SHELL=/bin/sh`，未放宽宿主写权限；真实固定版本探针确认写出 904 字节脚本、收集成功及停止。首轮证据 `tap-browser-core-8f4a991540df-evidence-wuvofo3w` 保留，其 7 个独立已停止容器已移除，状态目录保留。

仓库核心 `make test` 611 项及 `make check` 通过；后续本机取消/读取边界的增补另作定向验证。早期全局拒读沙箱诊断产生的三个 Node 进程（PID 7411、15845、24553）处于 macOS 内核 UEs 状态，SIGKILL 后仍不能回收；均为零模型探针，不是已通过适配器的任务进程，不隐瞒此清理限制，也未擅自重启 Mac。

### 本机真实任务暴露的定位契约与引用缺陷

- FWD-mac01 正式取消：16 次模型调用，$0.1078784，全部结算。实际进入产品页，但模型把快照 `[ref=...]` 拼成 DOM CSS（例如 `combobox[ref=...]`）。零模型实证 ref 不是 DOM 属性，正确语法为 `aria-ref=...`；原工具 schema 未说明此区别。仅补 selector 描述和 live 提示词，不重写选择器，不增加网站规则；7 个工具变体携带说明，真实主页面点击与 iframe 填写通过。证据：`tap-browser-core-7c756480f058-evidence-feqvm_vn`。
- FWD-mac02 已结算：24 次，$0.2095396，达到剩余调用次数上限；仍未取得完整报价。正确 `aria-ref=e98` 点击产品页成功，但局部快照后连续旧引用失败，之后虽自行恢复并操作年龄控件，仍未完成。两次本机 FWD 累计 40 次/$0.317418，未超过初设每站 $0.50；证据：`tap-browser-core-dd7883c8ff15-evidence-wlcyqvpb`。
- 独立零模型探针证明：Playwright AI 局部快照会覆盖所在 frame 的 ref 查询映射，令同一未变化 DOM 的区域外 ref 失效；原 inspector 在 scoped 错误时又返回空快照。最小修复只修改观察阶段：成功 scoped 仍返回局部文本，随后恢复全页 ref 映射；scoped 失败保留原错误并返回全页上下文。不重复写动作、不改选择器。主页面与 iframe 4 项真实回归先失败后通过，加既有生命周期共 8 项通过。AIA 在创建任务前暂停，修复冻结后恢复，首次 session 加载新 JS。
- 本机 OpenCode 取消路径另以 12 项定向测试验证就绪前后卡住进程的确认归属、限时终止和退出确认，Ruff/mypy 与独立复核通过；跨 `tapper-*`/`tap-browser-*` 任务目录读取被拒绝。

- AIA-mac01 已正式取消并结算：22 次调用，记账 $0.2353776。已进入公开报价表单，但模型反复使用 `textbox[ref='f6e53']`。真实固定 OpenCode 零模型探针确认两次模型请求的 7 个 selector 字段均保留描述，因此问题不是 schema 被丢弃。取消中的最后一次响应没有完整用量，按最大预留 $0.0565248 保守结算，不能当作已核验供应商实账。证据：`tap-browser-core-07080cb6efc7-evidence-ouhbalsz`。
- 工具增加明确公开的 live 别名语法：仅精确的已知 ARIA `role[ref='...']` 转换为 `aria-ref=...`，普通 CSS、显式 `css=`、复合选择器及非法引用保持原样；单次派发，不重试写动作，不用于冻结回放。12 项工具测试通过；真实主页面点击及 iframe 填写各执行一次的集成测试通过。
- 三轮合计 62 次、保守记账 $0.5527956，预留全部结算。后续复验仍限制每站累计 $0.50、总计 $1；每站累计调用上限调整至 60，扣除已用调用与费用后才分配剩余额度。FWD-mac03 与 AIA-mac02 使用上述两项修复；此前结果不冒充新版通过。

- FWD-mac03：16 次调用、$0.1262088，全部按回执用量结算；`cutoff.cause=cost`、`upstream_failure=false`。已成功进入产品页并操作性别、吸烟状态和年龄下拉，但先尝试无效 `text[ref=...]`、填写只读输入和宽泛 `text=30`，随后改用键盘；余额不足以预留下一次最大窗口费用，未完成报价，未生成独立回放。FWD 累计 56 次/$0.4436268。证据：`tap-browser-core-d2071a6a0054-evidence-ukfj259r`。该轮已使用引用映射恢复和精确 live 别名兼容，不能再把失败笼统归因于 Docker 或原引用错误。

- AIA-mac02：22 次调用、$0.2106684，全部按回执用量结算；`cutoff.cause=cost`、`upstream_failure=false`。成功进入公开报价表单、成功解析日期字段引用及局部快照，但填写只读日期失败后转为多次按键，未完成有效日期选择、报价或独立回放。证据：`tap-browser-core-5f0a4078104e-evidence-exuk7bm0`。本轮使用精确 live 别名和局部引用修复，尚未使用随后开发的相关控件事实反馈。
- Mac 正式模型验收总计 100 次、保守记账 $0.8896728（其中 AIA-mac01 的 $0.0565248 为未取得用量响应的最大预留结算）；所有模型动作与外层预留均结算，无在途模型调用。FWD 累计 $0.4436268，AIA 累计 $0.4460460；两站剩余预算均不足以预留下次最大窗口请求，停止付费复跑。真实报价及对应零模型回放仍未通过，不提交、不合入。

### 无模型观察反馈修复（真实报价复验后）

长页 fixture 复现：inspector 的前 16,000 字符观察和工具的前 6,000 字符视图可能遗漏末尾弹层，工具此前又去掉全部控件事实。保留既有有界快照和 API 协议，观察器优先收集当前目标、焦点、展开控件、关联可见弹层；工具在原 6,000 字符内容额度内分配最多 2,200 字符给相关事实，包含 readonly、值和可定位 selector。没有 ARIA 关系的补充控件明确标为 visible_control，不声称已关联。iframe selector 包含逐层真实 frame 边界；所有采集只读，不修改 ref 映射、不再次执行写动作。证据文件保存原始有界回执，不声称保存无限完整 DOM。

5 项新增真实 Playwright 回归先红后绿，覆盖主页面/iframe 长页末尾弹层、其他展开控件干扰、失败 selector 的原错误保留、无 ARIA 日期年月下拉。独立审查通过；父级冻结后组合验证 22 项通过（工具、相关反馈、scope、别名及 page-actions），无模型调用。已有核心 611 项与 make check 证据仍来自这些最终 JS 增补之前，未把旧全量验证冒充最终全量重跑。最新观察修复尚未进行 FWD/AIA 付费复验。

已知既有限制：shadow DOM 字段的稳定 CSS 唯一性不在本轮修复范围；普通 DOM 与 iframe 已验证，不宣称覆盖所有控件类型。

### 最终资源核验

AIA 数据服务清理遇到 Docker zombie，4 个独立验收容器在已授权重启 Docker 后全部定向移除，对应空网络已移除。恢复时发现 Docker 数据盘 100% 已满，导致共享数据库无法写入；清理未使用的可重建构建缓存及 53 个入口确认为 Tapper browser/OpenCode 的无标签旧镜像，未删除业务数据卷或当前标记镜像。数据盘恢复 12.4 GiB 可用空间；14 个重启前运行服务已恢复，MySQL、PostgreSQL、ClickHouse 均 healthy，Langfuse Web 正常 running。两个最终验收 state 目录中未残留临时 env/secret 文件；沿用宿主网关，没有另建模型网关或复制宿主凭据。

早期零模型沙箱诊断的三个 macOS 内核 UEs 进程仍在（7411、15845、24553，各约 48 KiB RSS），不冒充清理完成；未重启 Mac。代码未提交，最终 `git diff --check` 通过。


### 持续调整：跨页输入证据与结果来源（2026-11-09）

用户继续要求调整。重启后先前临时目录及未完成实跑证据已不可恢复；中断的 FWD 实跑按完整 60 次/$1 预留保守记账，不声称其已成功或零费用。本轮验收账本、运行状态及证据副本保存到 gitignored `.superpowers/feedback-continuation/`；沿用本机 OpenCode/浏览器、隔离 Docker 数据服务和宿主模型网关。

- [x] 工具计划区分输入型 Then 与默认结果型 Then，输入只接受精确回读，至少一个结果型 Then 才可完成。
- [x] 输入证据保留来源身份、回执和实际值；离开填写页后可保留，修改、返回原页或不确定动作后作废。当前页输入及最终结果在结束前只读复核。
- [x] 输出的每个字段保留实际读取位置和时序；失败回读先清除旧值及来源，不再把填写页输入统一归到结束页。
- [ ] 最终组合回归、独立复核及 FWD/AIA 真实模型报价与对应零模型回放。

以上修复针对已接受反馈闭环设计的证据保鲜与来源要求；没有增加网站专用选择器或固定报价。


最终证据规则组合验证：39 项浏览器测试、20 项 BrowserProgress 与 58 项运行时测试通过；独立复核修正同名输出覆盖、读取中跳页和 finish 后页身份漂移三处边界。`make check` 与核心 `make test` 614 项通过。

本轮 FWD 真实任务 `42bccb28e95e41e7ba74d104d840ffd4` 未通过：31 次调用、保守持久账本费用 $0.361562，全部模型动作已结算。实际进入 MyTerm Plus，但年龄仍为 25，未取得有效报价或回放；模型把只读年龄控件交互失败申报为 `external_block`。没有供应商失败或费用截止。证据持久副本：`.superpowers/feedback-continuation/fwd-20261109-210256/evidence/`。该结果不计为成功，也不据此向用户索取不存在的缺失资料。

后续调整针对本轮实际错误：工具 schema 未解释 `read.attribute=value/checked/text` 的区别及 `select` 已支持的控件类型，导致读取输入的空文本、读取标签代替选中值；通用选择辅助函数再次点击已展开控件还可能关闭选项。下一轮只在这些缺陷有针对性测试后复验，继续共享本轮剩余费用与调用上限。


工具语义修复已完成：明确 readonly、select、read 的支持范围；自定义下拉已展开时不再次关闭，只选择唯一可见的精确匹配项，拒绝两个可见匹配；冻结回放辅助函数采用相同语义。先红后绿及独立审查通过，最终 48 项组合浏览器回归通过；此前全量 `make check` / 614 项核心验证未在这项最终 JS 增补后重复。

FWD 第二轮 `a7bdcbd2ad484772801adc7055f85160` 失败：40 次调用/$0.515922，模型动作全部结算。年龄仍为 25，精确回读 30 失败；读到的 HK$97.20/HK$9.72 月费属于错误输入，不算验收成功。原生工具的完整说明确实进入模型请求，但模型仍填写只读控件并使用不存在的 div[ref]；后续点击 Standard 卡片打开的遮挡层未出现在当前相关控件反馈里，需要补齐观察。证据：`.superpowers/feedback-continuation/fwd-20261109-212626/evidence/`。

后续有界对照沿用现有网关的 qwen-max，先核验费率（输入 $1.6/M、输出 $6.4/M），不更改产品默认模型或凭据。与 qwen-plus 的对照用来区分工具信息缺失和模型决策问题，不把换模型本身当成修复成功；本轮剩余总额度仍为 49 次/$1.122516。网关申报 qwen-max 输入上限 30,720，当前 OpenCode profile 固定声明 129,024；本次有限对照记录此已知局限，若触及实际窗口则不把供应商拒绝误判为网页能力问题。

### 2026-11-09 Codex 实操对照与观察投影修复

用户要求直接用 Codex 自身操作并完整记录，详见 [对照报告](../../guides/2026-11-09-codex-browser-reference.md)。已导出 47 次调用、原始返回、3 张截图与逐步依据，失败和不确定结果保留；工具内部说明、私人窗口内容和误采集脚本明确删减。

本次 FWD 弹窗经可见 Close dialog 关闭，年龄 readonly 下拉选择 30，最终页面 Standard 为原价 HK$106.20、GOODGA 自动优惠后 HK$10.62/月，所需输入已回读。本次 AIA 表单输入正确，但结果页出现 Code 903；刷新和重新填写后月保费仍空，记为失败，不沿用此前 HK$45/月结果。

Ruling: 下一轮先保持 qwen-plus，仅改变观察投影后做 FWD 单变量复验；暂缓 qwen-max 及其上下文配置调整。依据是 Codex 实测确认有标签 popup 的文本被 related_fields 丢失且长列表后续选项不在压缩前缀；此时同时更换模型无法分辨修复效果。成本仍计入已授权剩余额度 49 次/$1.122516，不追加授权或假定成功。

- [x] 完成 Codex 实操与可审计记录。
- [x] 长页弹窗文本、模态关闭控件优先反馈的先红后绿回归与复核。
- [ ] 修复后由 Tapper 自己执行 FWD，检查准确输入、本次报价及独立回放。

原生 Chrome 的错误尝试曾绑定用户 Netflix 窗口并被中断。用户报告白屏后停止个人窗口操作；不声称已定位或修复 Netflix 原因。后续仅使用任务专用浏览器。

最终观察补丁 50 项组合回归通过（`popup-combined-final.log`），包含长文本/长定位器预算边界；独立复核通过（`popup-review.md`）。仅 page-actions.mjs、browser-tools.mjs 与 inspector-context.browser.mjs 发生本轮实现变动。父页面无语义遮罩盖 iframe 仍是已知限制。

已启动 qwen-plus 的 FWD 单变量复验：`.superpowers/feedback-continuation/fwd-20261109-222516/`，单次最多 49 次/$1.00。首次以 $1.10 启动在脚本参数校验退出，未预留或调用；随后采用脚本自身更严格的 $1.00 上限。

FWD 第三轮 `fff62fb1943d4f78ab2dd66d0d183d0b` 未通过：29 次调用、$0.342736，操作已结算后取消，证据 `.superpowers/feedback-continuation/fwd-20261109-222516/evidence/`。未完成年龄等准确输入回读；模型在 select 失败后又 fill readonly，并构造 `div[ref=...] >> text=30`、重复 `button[ref=...] >> text=Back` 等无效定位器。为避免无效循环耗尽预算，主动停止本次测试。未产生有效结果或回放。不能从回归通过推导模型行为已改善。

剩余授权为 20 次/$0.779780。下一步在该额度内恢复原先计划的 qwen-max 对照，先把已有 max_input_tokens 配置接到 OpenCode profile；这不是产品默认模型调整。验收监督脚本将只复制本任务临时目录中的浏览器观察 JSON，以避免任务清理后丢失工具观察。

第三轮证据补充：第12次浏览器动作 `div[id='combobox::R639cv93im::content'] >> text=30` 点击成功，说明模型后来利用弹窗定位恢复过一次；不能断言年龄始终25。现有持久事件摘要只有2000字符，无法据其确认最终年龄值；随后返回按钮复合ref定位连续失败，完整输入/结果验证及回放均未完成。下一轮保存完整浏览器观察JSON以弥补该诊断缺口。


输入窗口配置已修复并独立复核：OpenCode profile 使用现有 max_input_tokens，同时写入 input/context，默认 129024 不变；qwen-max 对照由网关实际申报值配置为 30720。5 项定向测试通过，相关 Python lint/格式检查通过。此次修复不改变产品默认模型。

已启动最后一轮有界 FWD 对照 `.superpowers/feedback-continuation/fwd-20261109-224838/`：qwen-max，最多20次调用/$0.75，沿用准确输入、结果提取和独立回放标准。其调用上限与上一轮不同，不用单次结果宣称统计意义的模型优劣。


qwen-max 对照最终主动取消：任务 da45e39f79d34a71a5269387abe0942a，12次/$0.208871，全部结算。前5份计划将 verification_kind=result 放在 And 上，只收到泛化 invalid BDD plan step；后来改为单Given计划被接受并启动导航，取消前无导航完成回执。未完成报价或回放，不能判定网站失败或模型优劣。保存13条原生工具记录及最终事件，独立容器已清理。总账272次/$3.3187638（含历史保守预留），剩8次/$0.570909，本轮不继续收费重试。接下来仅为本次实际错误补具体字段反馈，不放宽计划校验、不自动修改模型计划。


字段级计划错误反馈已完成TDD：browserctl.mjs与browser_steps.py同时返回步骤索引/ID、Then-only约束及允许值；不改写模型计划、不放宽原校验。JS7项、Python21项相关回归通过，格式与差异检查通过。未再调用付费模型，真实两站报价及独立回放仍未通过，不提交、不合入。
