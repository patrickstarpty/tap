# OpenCode 能力验证阶段设计

日期：2026-11-07。本文细化[整体设计](2026-11-06-agent-workflows-and-test-loop-design.md)的编码路径阶段 0，并按[能力验证计划](../plans/2026-11-06-opencode-capability-validation.md)实施。阶段 0 是独立验证工具，不是 Tapper 编码运行时交付；[真实能力记录](../../guides/2026-11-06-opencode-capability-validation.md)已有四项 supported、四项 unverified，阶段 2 门禁仍阻塞。

## 固定版本与隔离边界

使用 OpenCode V1 `1.19.5`、macOS arm64 官方[非预发布资产](https://github.com/anomalyco/opencode/releases/tag/v1.19.5)。`opencode-darwin-arm64.zip` 的 GitHub release SHA-256 与下载结果均为 `c1c9e62521192192ef4c3ed1355ada2144055bddb61682217e997b03c55203c1`；解压后的二进制 SHA-256 为 `feaafead8cd563af0c2dcb386b0757e4355e327ebb6bd1aaad5440fe2f83596c`。二进制已放在 worktree 和原仓库各自已忽略的 `.superpowers/tools/opencode-1.19.5/opencode`，不写全局配置，不自动升级。隔离 `HOME`、XDG 配置/数据/缓存目录及无租户数据工作区中，`--version` 返回 `1.19.5`，`run --help` 和 `serve --help` 成功；这本身不是模型调用证据。

配置按此版本的[主配置 schema](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/core/src/v1/config/config.ts)、[Agent schema](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/core/src/v1/config/agent.ts)和[权限 schema](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/core/src/v1/config/permission.ts)生成。V1 使用 `agent`、`permission`、`bash` 和 `task`；现行 OpenCode V2 文档的 `agents`、`permissions`、`shell` 和 `subagent` 不能套用。任务配置设 `autoupdate: false`、`default_agent: orchestrator`、`model` 与 `small_model` 均为 `litellm/qwen-plus`，并仅启用 `litellm` provider。自定义 provider 的连接参数与任务专属凭据将在后续探针进程中派生；不得写进 Agent 文件或报告。

`build`、`plan`、`general`、`explore` 四个可被委派的内置 Agent 通过 `agent.<name>.disable: true` 禁用。内部 `compaction`、`summary`、`title` 保留，并显式指定 `litellm/qwen-plus`，避免辅助请求隐式换路由。隔离 CLI 的 `agent list` 不再列出四个禁用项，`debug agent build` 返回 not found；`debug config` 和 `debug agent` 均解析生成配置，并显示主 Agent、QAE 和三个辅助 Agent 的预期模型。CLI 解析不证明模型连通、费用计数或会话行为。

## 冻结输入和权限

`ProbeAgent(agent_id, revision_id, description, instruction, tools, enabled)` 是阶段 0 的输入。内置业务 ID 为 `orchestrator/ba/sde/qae/sdet`；其他 ID 必须匹配 `[a-z0-9][a-z0-9_-]*`，且不能与 `build/plan/general/explore/compaction/summary/title` 的 OpenCode 内置 ID 冲突。拒绝重复、空描述、未知工具和路径逃逸。仅导出当前项目启用且任务允许的 Agent 到 `.opencode/agents/<id>.md`；`orchestrator` 是 primary，其余是 subagent。职责说明、固定模型、权限和指令正文都随修订写进字节稳定快照；结果映射是相对路径到 SHA-256。

工具权限先 `"*": deny`，再开放明确授权的 V1 工具。`write` 与 `patch` 只映射为 V1 `edit`，其他未知平台工具拒绝。允许普通读取时，随后对 `*.env` 与 `*.env.*` 明确 `deny`。QAE 不允许 `edit`、`bash` 或下级 `task`；专业 Agent 不可继续委派。根 Agent 的 `task` 规则先拒绝全部，再按冻结名册只允许获准专业 Agent。直接显式 `@` 仍须由 TAP 服务端校验，不能只依赖 OpenCode 的 task 规则。

导出前完成全部校验，在临时目录构造文件，再原子放入任务目录。已存在快照按文件集合和字节核对；一致时不重写，冲突时报错，调用方必须另建任务快照。V1 配置单独冻结为 `.opencode/opencode.json`，同样核对摘要且不重写。配置文件存在不表示 Agent 实例已经运行。

## 实验场景与判定

确定性测试只证明导出、拒绝和冻结行为。真实能力实验必须从版本固定的 OpenCode 与 LiteLLM 取得脱敏事件、工作区摘要和执行端回执；不能用旧 Copilot fixture、提示词或 CLI 帮助填充通过结果。

| 场景 | 需要的真实证据 |
| --- | --- |
| BYOK 全覆盖 | 主 Agent、专业 Agent 和辅助调用均关联 LiteLLM 请求/用量，且无越权 provider |
| 自动与显式委派 | 自动选择与指定 QAE 的真实子 session、Agent ID、父子关系和终态 |
| 简单问答 | 只出现主 Agent，无无关专业实例 |
| 并行 | 两个独立任务的实际执行区间重叠且日志可归属 |
| profile 权限 | QAE 尝试修改时执行端拒绝，工作区摘要不变 |
| 增补要求 | 接收与实际应用回执分离，至少证实安全边界交付 |
| 暂停与恢复 | 停止推进回执、工作区摘要和恢复后不重复产物 |
| worker/CLI 失联 | 旧会话及进程可对账，不能盲目重复启动 |
| 名册规模 | 5、25、100 个允许 Agent 的启动、用量可见性和正确委派 |
| 预算 | 全任务超时、调用数、费用上限在子任务与恢复间累计，超限后执行端停止请求 |
| 配置冻结 | 运行中修改不改变老任务；新任务可核对新快照 |

required 门禁固定为 `byok_all_calls`、`child_identity`、`parallel`、`permissions`、`control_receipt`、`resume_workspace`、`reconcile`、`budget_enforcement`。每项结果只可为 `supported`、`unsupported`、`unverified`；未知、缺可信计数、缺终态或缺引用均不能解锁阶段 2。当前 `child_identity`、`parallel`、`control_receipt`（安全回合边界）、`reconcile`（仅控制进程失联）有真实 supported 证据；其余四项 unverified。Task 0.4 的自动委派与恢复复现均保留为独立部分样本；后续一次有界恢复实验捕获首次动作计数，但未进入中断与续接，仍不能补足门禁。未复现不等于能力不支持。

真实模型实验必须同时设置 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 与 `TAP_RUN_OPENCODE_PROBE=1`，缺任一开关则 skip；双开关真实烟测已经通过。探针在任务自有、回环绑定的限额转发边界派生最小 BYOK 凭据，原仓库 `.env` 只由监督端读取，Agent 工具不获得上游密钥。主/子真实请求及用量可核对，但辅助子类型和未观察路径的全覆盖仍未证实。原生路径仍通过 ModelGateway → LiteLLM，编码路径才使用 OpenCode → LiteLLM。实测选择 V1 `serve` 会话与 SSE 加持久 message/children 核对作为阶段 2 候选；`run --format json` 仅给选择性根事件，不能独立满足子实例实时证据。HTTP 接收不是指令已应用或取消完成。SSE 无已验证重放游标，需按持久 session/message/part 标识对账；具体字段与限制见[记录](../../guides/2026-11-06-opencode-capability-validation.md)。
