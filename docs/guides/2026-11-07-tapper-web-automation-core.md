# Tapper Web 自动化核心闭环验收

Tapper 的 **Browser run** 从自然语言目标、起始 URL、输入资料和预期结果创建持久任务。编码 runtime 先用受限浏览器 inspector 读取实际页面，再由模型生成 `flow.spec.ts`；独立 Browser worker 用冻结脚本与输入启动新浏览器，回写结果、步骤、截图和 trace。**Replay frozen run** 创建新任务，沿用原 bundle 与输入，不再次调用模型。产品入口及配置见[本地运行指南](2026-11-08-tapper-browser-automation-local.md)，边界见[核心设计](../superpowers/specs/2026-11-07-tapper-web-automation-core-design.md)。

编码容器从已验证摘要的持久冻结输入接收同字节副本，存于仅该容器可读的 `/workspace/frozen-inputs.json`，供生成脚本时使用；正式 Browser 执行在另一容器从 `/opt/browser/frozen/inputs.json` 读取权威冻结输入。编码副本只在任务的临时文件系统内存在，不会替换 bundle 的原输入或其摘要。OpenCode 的 `glob`/`grep` 使用编码镜像内固定版本 ripgrep，无运行时网络下载。

## 确定性验收

从仓库根运行 `make browser-core-e2e`。该门禁为 opt-in，不属于日常 `make test`。监督器为每轮创建唯一 owner 的 MySQL、Redis、MinIO、Milvus、LiteLLM、合成模型和自有站点，分别迁移 TAP AI 与 Browser 数据库，完成后仅清理这些资源。它启动真实 Chat/API、原生 workflow worker、OpenCode coding runtime、Browser API/worker 与 Web；合成模型只替代外部付费模型，**不能视作真实模型验收**。

固定站点的订单码在每次站点启动时随机生成，目标和输入中均未知。测试模型先收到真实 inspector 页面观察，才写入场景脚本。门禁从 Tapper UI 覆盖订单查询提取、表单核对、多页导航；错误预期应失败，缺少 PIN 应等待输入。它校验持久结果、步骤、截图 PNG、trace ZIP、文件 SHA-256、刷新后显示、跨项目读取拒绝，以及从 UI 重跑的新 task/execution ID、原证据不变、bundle/input 摘要相同和零新模型调用。取消负例在浏览器已观察页面后从运行详情发出，并检查持久 `browser.stop_confirmed` 与 BrowserStore 停机收据。

| 必需结果 | 证据及边界 |
| --- | --- |
| 查询提取、表单核对、多页动作；错误预期、缺输入 | `make browser-core-e2e` 使用合成模型、真实 Chat/编码容器/Browser 执行，保存五个 UI 快照、冻结脚本、实际页面观察与运行证据。 |
| 取消实际停机 | 同一门禁从 UI 请求取消；审计含 `browser.stop_confirmed`，BrowserStore 有 `stopped: true` 收据，任务终态为 `canceled`。编码 CLI 子进程的取消不会单独算停机证明。 |
| 费用/调用/期限停止 | 合成全链路的费用截断样本加模型网关的定向门禁测试；预算占用与真实账单分开记录。期限与调用拒绝由网关和 runtime 定向测试覆盖。 |
| 不可达与被拦截网络 | `TAP_RUN_BROWSER_RUNTIME_INTEGRATION=1` 的真实 Chromium 测试使用生产 `BrowserRuntime` 访问 `example.invalid`，代理返回拒绝页且冻结脚本执行为 `failed`；同测试验证直接 IP、网关、跨任务代理、HTTPS Host/SNI 拒绝。它是浏览器层证据，不代替 Chat 全链路。 |
| 跨项目查读/证据/取消/重跑 | UI 门禁验证跨项目查读、证据和重跑拒绝；Browser API/service 的作用域测试验证跨项目取消拒绝。 |
| 冻结重跑 | UI 门禁验证新 task/execution ID、同一 bundle/input 摘要、旧证据不变、重跑用量与 Browser receipt 的模型调用均为零。 |

验收证据写入监督器打印的私有临时目录：原始冻结脚本与输入、页面观察、任务审计、模型调用统计、截图、trace 和 UI 快照。目录权限为 `0700`，文件在退出前统一设为 `0600`；成功和失败都在清理自有数据库之前导出。`budget_used.cost_micros` 是已计入任务预算的成本：有可验证 usage 时按该用量结算，未知用量按保守上界结算；它不总等于供应商最终账单。模型网关还按每次最大输入/输出 token 累计预留，预算上限须容纳计划调用的预留额。预算截断不是浏览器执行失败的替代证明。

## 已固定的边界与代价

- HTTPS 目标保持冻结 origin 校验，并在任务局部终止 TLS 以核对加密请求的 Host/authority。仅看 CONNECT 目标不足以完成这项校验；代价是任务局部证书、信任配置及 HTTPS fixture 验证，不改系统信任。
- 受信 reporter 接受有界、命名的 JSON 业务结果附件，使页面提取值能进入产品结果。代价是小幅扩展 reporter 合同、重建固定镜像并核对输出上限；不增加站点专属 schema。
- 生产模型请求上限为 131072 字节、响应上限仍为 1048576 字节。实测 OpenCode 工具上下文接近原 32768 字节上限，真实页面观察还会增长；代价是单次传输内存上界提高至四倍，且保守 token 预留可能变大，任务总调用/费用/期限仍有明确上限。
- 自有站点的私网地址只由验收监督器验证容器 ID、名称、owner label 与 bridge 地址后注入 `BrowserRuntime.test_target`。普通站点使用正式 worker 与生产网络策略；代价是自有站点门禁经过测试组合点。隔离运行使用 `TAP_BROWSER_SERVICE_MODE=external`，让真实 Chat/API 启动链路连接单个自有 Browser API/worker，避免双 worker 竞争；这是生命周期配置，不是私网放行开关。
- 仅验收监督器 CLI 的费用上界从 USD 0.50 调整为 USD 1.00；下一轮自有站实验单独预留 16 次调用、USD 0.90、240 秒。此前真实页面检查和交互已发生，但八次调用上限在生成完成前截断，原 USD 0.50 是监督器自定值而非产品或设计上限。代价是单次实验保守预留额增加；产品 BudgetGate、默认配置与累计授权额度不变，仍须每次明确预留，不授权盲目重试。
- Browser 执行证据的默认上限从 1,000,000 字节提高到 5,000,000 字节，保留调用方显式上限和原有 10,000,000 字节硬上限。一次有效公共站登录/退出脚本产生 1,297,553 字节证据，旧默认值在持久化前拒绝了 trace；代价是单次执行的默认有界证据存储和传输上界提高五倍。超限明确返回 `evidence_limit`；只有 `execution_failed` 且确有断言或测试失败时才允许模型修复脚本，资源或 runtime 错误不以空结果触发修复。模型预算不随此上限改变。

## 真实模型实验

真实模型仅在明确预留调用数、美元上限、截止时间与目标后运行。通用命令接受新任务，不需要修改产品代码或预写业务脚本：

```sh
uv run --project apps/tap-ai-backend python scripts/run-browser-core-e2e.py \
  --mode real --site public \
  --goal 'Describe the new browser task' \
  --start-url 'https://example.org/' \
  --input-notes 'Only the data authorized for this task' \
  --expected 'The observable result required for acceptance' \
  --output-pattern 'Expected result pattern' \
  --max-model-calls 16 --max-cost-usd 0.90 --deadline-seconds 240 \
  --env-file /path/to/private/.env
```

`--site owned` 只用于监督器验证过容器 ID、名称、owner label 和 bridge 地址的合成站；该固定 `test_target` 注入只存在于测试组合层。普通公共站点运行构造生产 `BrowserRuntime`，沿用公开网络策略。真实模式从显式给定的本机私有 `.env` 读取 LiteLLM 地址与密钥，不打印或复制密钥，且只按给定预算运行；启动前需核对旧用量与剩余额度。示例上限是一次明确授权的实验边界，不保证任意任务都能在相同调用数或时间内完成。输出脚本为模型原始生成内容，验收不能事后手改。FWD 询价只是可选例子，不能代替跨站点和多流程验收，也不触发投保或付款。

已完成的自有站真实模型闭环：原任务 `37785783cea44e28bf90d0cef8a0fa5d` 从只含 Ada/west 的自然语言目标和输入出发，模型检查实际页面后生成冻结脚本，从 DOM 读出目标与输入中未知的 `ORDER-3AA83FC4`；新 Browser 执行持久化结果、PNG 和 trace。UI 重跑任务 `064279bfc1834a9d9e4f76f13716cdcf` 使用同 bundle/输入摘要与新 execution，零新模型调用。该次 10 个可验证调用，USD 0.0210692，私有证据目录为 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-fb1e09947404-evidence-pzs76vbb`。生成脚本写入了已供应的 Ada/west 值，只对未知订单码做 DOM 提取；它不是可任意换参的资产。

实现后独立选择的公共站真实模型尝试保留为失败证据。Selenium 表单任务 `c668c9f3f7dd417cbb1dc417bbabe2dc` 用 5 个可验证调用、USD 0.00837，因正式代理把公共 A+AAAA 双栈 DNS 整体拒绝而未生成 bundle；修复后仅查询并校验全部 A，仍拒绝私网、保留和非法地址。The Internet 登录/退出任务 `1ad380e6aef9444fadd80c2c928d0e85` 用 16 个可验证调用、USD 0.0414744；首个原始脚本的正确表单操作在默认 1 MB 证据限额下产生 1,297,553 字节而在证据持久化前失败，空 `{}` 反馈引出了错误的修复脚本，最终页面仍在登录页。证据上限和修复条件按上文裁决收窄修改；同份原脚本 SHA-256 `6d0fed9671a0c06d9889c51e1d861f6a26b4a351012590c3301a0d911e05b418`、同冻结输入在正式生产网络策略的 Browser runtime 下无模型重放，以新默认 5 MB 通过，结果 1 passed/0 failed、PNG 44,057 字节、trace 1,263,258 字节，并确认任务容器和网络停止。证据目录分别为 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-d86a267f3db1-evidence-ubey9yhy` 和 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-dd23e439f2f0-evidence-3at151xe`。

之后独立选择的 Playwright to-do 任务 `e2cb2f4ae199420dbea64db2d5fa04a3` 在正式公共 worker 上用 8 个可验证调用、USD 0.0164392，终态 `failed`，安全失败分类为编码工具 `read/tool_error`，没有导出脚本、bundle 或 Browser execution；浏览器 inspector 停机收据为 `stopped=true`。代码核对发现旧 OpenCode client 把历史工具报错直接当作整个 assistant 的失败，尚未等待最终回答；定向 Node 测试先失败后通过，现只把该工具错误视作已结算，仍要求当前用户的最终 assistant stop、idle 与子任务完成，实际 assistant 错误继续失败。原始工具报错内容未保留，不能声称该模型必能自行纠正。私有证据目录为 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-ed9cb78eb825-evidence-rrx83qsu`。这三次公共站尝试均不算跨站真实模型通过；每次产品修改后重新独立选择目标并单独预留。

最终冻结后独立选择的第四次公共站任务在 `https://quotes.toscrape.com/` **通过**：原任务 `37e99871215844d49e647e4b3dd9e155` 使用 12 个可验证模型调用、USD 0.0384572，生成的原始脚本 SHA-256 为 `26bc6d06ac7e7ee7732f985093df5682a1d8e74329ec1ae762e0a0a628a4e9c3`，新 Browser 执行从真实页面 DOM 读出 `https://quotes.toscrape.com/tag/humor/` 及两条非空引文和作者（Jane Austen、Steve Martin），持久化 JSON 结果、PNG 和 trace。UI 重跑任务 `70a2ef7088294203b82ef310ac81121e` 使用同 bundle/输入/脚本和新 execution，`budgetUsed={}`、`replayModelCalls=0`；BrowserStore 停机收据为 `stopped=true`，exact owner 资源已清理。证据目录 `/var/folders/zn/dg1zfbv96p19qstvcpgsjvyr0000gn/T/tap-browser-core-d5b402bf14c9-evidence-ijg69jtb` 保存原脚本、输入、页面/任务审计、用量、结果、截图、trace 和 UI 快照，目录 `0700`、文件 `0600`。脚本把本次冻结输入写为常量，直达从 tag 构造的过滤 URL，再核对标签并抽取实际 DOM；它不在执行期动态读取 JSON，不能视作可换参资产。两站点真实模型核心链路和三类确定性流程已验收；这不保证任意新站都成功。

代码已具备上述核心能力，但当前默认配置仍关闭 `TAP_WORKFLOW_ENABLED` 和 `TAP_BROWSER_CODING_ENABLED`，共享应用没有因本次验收自动启用；按[本地运行指南](2026-11-08-tapper-browser-automation-local.md)配置服务、镜像、项目作用域与开关后才能使用。首版范围止于这一核心闭环；复杂暂停/恢复、多轮补料、完整测试资产管理和生产网络/身份加固另行设计。当前 Demo 保持本机回环、无认证，不对局域网或生产安全作承诺。
