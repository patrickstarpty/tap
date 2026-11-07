# OpenCode 能力验证记录

本记录适用于固定的 OpenCode 1.19.5、内部根 profile `probe-0.3-r2`、本地 LiteLLM `qwen-plus` 组合。样本来自 2026-11-07 的真实本机调用；[脱敏 fixture](../../apps/tap-ai-backend/tests/fixtures/opencode/real/)只保存会话标识、事件/工具状态、配置与产物摘要、网关计数和用量，不保存密钥、提示词或模型回答。评估逻辑在 [`evaluate.py`](../../scripts/opencode_probe/evaluate.py)。八项 required 中四项 supported、四项 unverified；`ready_for_runtime` 为 **false**，阶段 2 编码运行仍阻塞。

| 必测能力 | 结论 | 真实证据与边界 |
| --- | --- | --- |
| BYOK 全调用 | unverified | 受控网关记录了主会话、SDE/QAE 子会话的请求和用量；标题等辅助调用只与根会话相关，缺可靠的逐消息/辅助子类型关联；不能证明所有未观察到的路径。见 `simple.json`、`auto.json`、`parallel.json`。 |
| 子实例身份 | supported | `auto.json` 的 SDE 和 `explicit.json` 的 QAE 均有真实 `parentID`、`task` 工具返回的子会话 ID、开始/完成时间、终态和子会话网关请求。最初的 `auto-initial-no-child.json` 未自动委派，后用职责明确的 profile revision 重试；最初 `explicit-initial.json` 缺完整生命周期时间。 |
| 并行 | supported | `parallel.json` 两个子任务区间重叠，SDE/SDET 各自的 `write` 工具归属 `one.py`/`two.py`，根会话终态完成。`parallel-cost-stop.json` 是旧 USD 0.50 保守预约触顶样本，未把它算作成功。 |
| profile 权限 | unverified | `permissions.json` 的 QAE 子会话没有写入 `denied.txt`，但模型没有真正调用受拒绝的写工具，缺执行端拒绝事件。`permissions-initial-root-write.json` 中根自己写入文件，也不能证明 QAE 权限。 |
| 运行中增补 | supported | `control.json` 在根会话忙碌时接收新用户消息 ID，后续 assistant `parentID` 指向该 ID，`second.txt` 出现。只证明安全回合边界的实际应用，不证明工具执行中的即时注入。 |
| 暂停与恢复 | unverified | `resume.json` 在 `first.txt` 写成且 task-owned `waiter.py` PID 存活时 abort；该 PID 随后消失、第二产物当时不存在、同一会话续接后出现 `second.txt`，但未记录首次写动作的独立运行计数。Task 0.4 的 `resume-task04-no-abort.json` 在 180 秒/8 次转发内产生两个文件，却未进入满足 PID、忙碌根会话与运行中 bash 的中断窗口。新样本 `resume-browser-continuation.json` 记录首次动作最终计数为 1，但未捕获同时满足中断前置条件的窗口；没有 abort、进程停止或同会话续接证据。三个样本不能拼接成一次通过，旧部分/错误样本仍保留。 |
| 控制进程失联核对 | supported | `reconcile.json` 的 task-owned controller 收到 `prompt_async` 回执后被 SIGKILL，OpenCode serve 仍存活；监督端找回原会话及最终产物，未新建会话。未覆盖 OpenCode serve 自身丢失。 |
| 执行端预算 | unverified | `budget.json` 在一次转发后，网关以 429 拒绝后续两次请求，未再向上游转发；`parallel-cost-stop.json` 证明单场景保守费用预约触顶。超时、子任务与恢复的全任务累计停止尚无完整实测证据。 |

额外场景：`simple.json` 的根会话回答 42，2 次根会话请求均有用量；它不单独证明辅助调用类型。`roster-5.json`、`roster-25.json`、`roster-100.json` 分别以 5、25、100 个允许的虚构 Agent 完成真实 SDE 委派，启动时间为 3.558、2.553、2.634 秒，观测输入 tokens 分别为 14580、15341、18180。第一次 25-Agent 预检因有效配置输出超过固定 512 行捕获上限而失败，0 次转发；`roster-25-preflight-failure.json` 保留该样本，随后仅将该预检的有界捕获扩大至 4096 行。`config-freeze.json` 在旧会话忙碌时把任务源 revision 从 r1 改为 r2：旧快照 SHA-256 未变，新任务会话载入不同 hash，两个会话都完成；不依赖热加载。

编码产物的语义缺口已由 `auto-task04-budget-stop.json` 补证：真实 SDE 子会话写入 `square.py`，受限 AST 通过，`python -I` 退出 0，任务自有合成 stdout 为 `The square of 2 is 4` 与 `The square of 3 is 9`，与原题语义一致。该次整条自动委派复现仍是 **incomplete**：7 次转发后第 8 次被本地网关以 429 拒绝，根会话没有可信终态。早期 `auto.json` 和 `auto-script-output-gap.json` 的旧格式失败/未捕获 stdout 仍是原样历史证据，不能追认为本次成功。

## 固定接入组合与可复用字段

阶段 0 使用已隔离安装的 OpenCode V1 `1.19.5` 二进制 `.superpowers/tools/opencode-1.19.5/opencode`（文件 SHA-256 `feaafead8cd563af0c2dcb386b0757e4355e327ebb6bd1aaad5440fe2f83596c`；发行 ZIP 摘要另见[阶段设计](../superpowers/specs/2026-11-06-opencode-capability-validation-design.md)）。主/子、`small_model`、`title`、`summary`、`compaction` 均显式指向 `litellm/qwen-plus`；OpenCode V1 自定义 `@ai-sdk/openai-compatible` provider 只连任务自有回环限额网关，由监督进程保管原 LiteLLM 凭据并给子进程短期随机令牌。有效配置预检、任务名册快照和摘要由探针生成，不使用个人 OpenCode 配置或全局 PATH。运行前监督端读取 `/v1/model/info` 的实际价格/上下文上限；网关按最大输入和 512 输出 tokens 逐次保守预约，场景运行期间不因成功响应释放这笔保守预约。每次响应的 usage 单独记录；网关关闭后，持久累计 ledger 才按可信用量结算，未知用量保留最坏费用。此组合只在本机合成工作区验证，容器内必须重新跑核心门禁。

已验证的交互候选是 `opencode serve` 的认证回环 HTTP：`POST /session` 创建根会话，`POST /session/{sessionID}/prompt_async` 返回接收回执，`POST /session/{sessionID}/abort` 只表示取消请求，`GET /session/{sessionID}/message` 与 `/children` 提供持久核对；`GET /event` 提供实时 SSE。`opencode run --format json` 只选择根事件，不能提供本次实测所需的完整子会话/恢复观察；阶段 2 若最终解锁应从 serve 合同出发，不引入额外 SDK/MCP 代理。SSE JSON 有 `type` 与 `properties`，本次见到 `session.created/status/idle/error`、`message.updated`、`message.part.updated/delta` 等；状态读取用 `properties.sessionID`、`properties.status.type`，消息用 `properties.info.id/sessionID/role/parentID/finish/error`，工具 part 用 `properties.part.type/tool/state.status`。SSE 事件本身没有已验证的重放游标；断线后应读持久 session/message/children，用已观察的 message ID 对账，并在阶段 2 验证 part 标识，不能把到达顺序或自然语言当稳定事件键。

子 Agent 的可关联身份来自 `/children` 的 `id/parentID/agent/time` 与根 assistant 的 `task` 工具 `state.metadata.sessionId`；工具归属来自各子 session 的持久 message/part，不能只凭根自然语言。`prompt_async` 的 user message ID 是接收标识；后续 assistant `parentID` 和工作区产物证明安全边界应用。模型转发记录中的 `gate_request_id` 是本地尝试 ID，`upstream_request_id` 仅在上游返回时存在，`x-opencode-session-id` 是模型请求携带的会话标识；三者不是同一个消息 ID。标题等辅助请求只可可靠关联到根会话，具体辅助子类型与全部未观察调用路径仍未知。即时工具中插话、真正执行端权限拒绝、无重复动作的恢复、跨子任务/恢复累计时间费用限额均未获完整证据。

阶段 2 可用的根会话终态完成样本为 `simple.json`、`auto.json`、`explicit.json`、`parallel.json`、`control.json`、`resume.json`、`reconcile.json`；其中 `resume.json` 仍缺首次动作未重复的独立计数，不能算暂停恢复能力通过。还须保留失败/部分样本做截断与限额回放。`auto-task04-budget-stop.json`、`resume-task04-no-abort.json` 和 `resume-browser-continuation.json` 是单独的部分证据；不得把它们接成一个完整运行。外层 LangGraph 只拥有编码父任务生命周期和交接权，OpenCode 内部 Orchestrator 独占该次编码运行的模型规划与专业 Agent 委派。

真实烟测默认跳过。2026-11-07 双开关实测 1 passed，2 次模型转发。运行前设置受批准的本地 LiteLLM env 文件和同一持久 ledger 的路径，并同时启用两个开关：

```bash
export TAP_OPENCODE_GATEWAY_ENV_FILE=/path/to/approved/local/.env
export TAP_OPENCODE_LEDGER=/path/to/shared/opencode-ledger.jsonl
TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 TAP_RUN_OPENCODE_PROBE=1 \
  uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_opencode_capabilities.py -v
```

烟测最多预约 3 次模型尝试、90 秒、USD 0.20；每次运行前写入持久预约，进程异常丢失时仍计入总额。完整场景 CLI 另有每场景最多 10 次、180 秒的硬界；全任务 ledger 含失败、辅助与子会话请求。Task 0.4 完成时原账本为 117 次、估算 USD 0.1496368；同一账本随后记录 `browser-e2e-2026-11-07` 显式续建授权，上限为 237 次、USD 2.1496368。此次唯一恢复实验结算 3 次、USD 0.0034280，累计 **120/237 次、估算 USD 0.1530648/2.1496368**；剩余 117 次、USD 1.9965720。不得无声重置或跨上限重试。不要复制或提交本地 `.env`。运行阶段 2 前必须重新评估全部八项并取得真实 supported 证据。

## 2026-11-07 核心优先交付前的恢复归档

在已审查 `98bf9201` 的隔离源码快照上进行最后一次有界恢复验证，新增 `resume-verified-identity.json`，真实调用 3 次。观察到根 busy、bash running、首次动作存在且第二动作尚不存在，但执行镜像身份校验返回 `executable_mismatch`，未授权中断或续接；门禁保持 unverified。原账本累计 123 calls/USD0.1564932，余 114 calls/USD1.9931436，无自动重试。

用户随后明确批准[通用 Web 核心闭环优先](../superpowers/plans/2026-11-07-tapper-web-automation-core.md)，复杂恢复与完整测试资产管理后补，保留隔离、预算和取消。旧八门禁状态不改为通过；核心首版使用独立必需验收，当前尚未交付。
