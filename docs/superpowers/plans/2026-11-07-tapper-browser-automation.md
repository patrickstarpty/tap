# Tapper 真实浏览器自动化续建计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Checkboxes record verified progress.

**Goal:** Tapper 根据自然语言目标检查真实网页，经 OpenCode 生成脚本并由隔离 Playwright 执行不同网站的自动化流程，回传结果与证据，并能再次执行。FWD HK 询价只是一个样例，通用性按整体 spec §11.2 验收。

**Architecture:** 沿用双路径 Agent 工作流与现有模块所有权，不新增独立旁路工具。2026-11-07 用户明确批准核心闭环优先：保留权限隔离、费用上限与取消，复杂恢复和完整测试资产管理后移；原阶段 0 八项门禁及完整阶段 2/3 验收仍保留为后续交付要求。核心首版范围以整体 spec §11.2 为准。

**Tech Stack:** 已固定 OpenCode 1.19.5、LiteLLM、Python、Playwright + TypeScript、Docker、MySQL、MinIO。

**Spec:** [整体设计 §11.1](../specs/2026-11-06-agent-workflows-and-test-loop-design.md#111-fwd-hk-真实询价验收2026-11-07-用户补充)、[阶段 0 设计](../specs/2026-11-06-opencode-capability-validation-design.md)。基线 `c492f3cc`，阶段 1 已交付，阶段 0 四项 supported/四项 unverified，阶段 2/3 未交付。

## Global Constraints

- 用户已授权继续实施并将一般工程决定交给控制器；重大范围、架构或实际投保行为仍需讨论。控制器统筹，GPT-6 Sol 实施。
- 阶段 0 所有 required 门禁保留原真实证据要求，未知仍 unverified；完整 runtime 的 ready 判定不放宽。用户批准的受限核心首版使用单独、明确的能力范围与真实验收，不能宣称完整 runtime ready。
- 正式代码沿用阶段 2/3 计划的领域所有权、不可变版本、人工发布、回环绑定及隔离执行；不复制一个旁路工具冒充 Tapper 产品能力。
- 保留全部旧真实 fixture，不追写旧样本中的缺失事实；新证据使用独立文件。
- `.env` 仅由监督端读取；模型工作区、制品、日志和报告不包含原 LiteLLM 密钥。无关 `.env.bak-20261015`、`apps/tap-ai-backend/.quality/` 不读取、不修改、不提交。
- 本轮额外真实模型预算最多 120 次 / USD 2.00，历史 117 次 / USD 0.1496368 不归零。新增额度要在同一账本中明确记录，确定性检查和独立审查通过后才启用。每个实验先规定调用/时间/金额上限；缺证据不自动重复。
- FWD 测试仅使用 spec §11.1 的合成询价资料，止于公开报价；不提交投保、真实个人资料或付款。默认测试不访问外部网站。

## Task 1：可审计续建额度与确定性恢复前置条件

**Files:** Modify `scripts/opencode_probe/ledger.py`、`scripts/opencode_probe/scenarios.py`；Modify `apps/tap-ai-backend/tests/unit/operations/test_opencode_probe.py`、`test_opencode_scenarios.py`；必要时新增小型 `scripts/opencode_probe/resume_fixture.py`，仅负责监督端生成的合成 waiter。不改产品 runtime、权限 profile 或旧 fixture。

**Interfaces:**

- `ProbeLedger.authorize_continuation(grant_id: str, additional_calls: int, additional_cost_usd: float) -> tuple[int, float]`：监督端显式执行；在同一文件排他锁内核对已结算总额和现有额度，追加零消耗的授权事件，返回新累计上限。无新授权时仍为旧 120/USD2 上限。相同 ID 与相同参数幂等，ID 重用但参数冲突拒绝。存在未结算 reservation 时拒绝扩额；不重写历史行。新上限为已结算消耗加本次额外额度，不暗中继承未用旧额度。
- `prepare_resume_fixture(workspace: Path) -> str`：在任务私有工作区排他写入有界的 `waiter.py`，返回 SHA-256。脚本一次启动在 `first-action.log` fsync 追加一条 `start`，生成 `first.txt` 与现有进程所有权校验所需 PID 标记，再等待 60 秒，最后生成 `second.txt`。不给它额外系统操作或网络访问。
- `resume-workspace` 模型任务仅启动这个脚本一次；监督端按已有 busy/bash/进程身份条件中断，确认进程停止后在同一会话要求只生成 `second.txt`。保留三时点动作计数与摘要判定；不能通过重新执行 waiter 补齐结果。记录各中断前置条件以解释失败，而非只在全部满足时才留证据。

- [x] 账本 RED：历史统计不变、旧上限生效、授权后最多增加指定额度、重放授权不再加额、冲突 ID/未结算预约/并发预约拒绝。代表性断言：

```python
ledger.append('prior', 117, 0.1496368, 0.0)
assert ledger.authorize_continuation('browser-e2e-2026-11-07', 120, 2.0) == (237, 2.1496368)
assert ledger.authorize_continuation('browser-e2e-2026-11-07', 120, 2.0) == (237, 2.1496368)
assert ledger.totals() == (117, 0.1496368)
```

- [x] 恢复 RED：预置脚本内容固定且不覆盖已有文件、独立局部执行能捕获一条 durable marker/PID/first；受控停止后 second 不存在，未知进程仍不授权续接。测试使用短等待或受控进程停止，不真的等待 60 秒；不能执行危险资源样本。
- [x] GREEN：仅实现上述接口和场景前置条件，保留现有 PID 身份复核、fail-closed unknown、源文件/输出/产物上限与网关关闭后结算。合成脚本预置不算 OpenCode 代码生成证据，只隔离恢复能力这一实验变量。
- [x] 运行相关 operations 窄测、Ruff/format、mypy、文档链接与 diff 检查，提交 `fix: make opencode continuation probes auditable`；报告确切命令和结果。此任务不读取 `.env`、不扩真实账本、不发模型请求。
- [x] 独立审查通过后控制器才在原持久账本执行一次本轮授权；记录总额、授权 ID 与上限。旧账本不存在或余额不符则先调查，不创建零余额替代。

## Task 2：一次有界真实恢复验证

**Files:** 新增 `apps/tap-ai-backend/tests/fixtures/opencode/real/resume-browser-continuation.json`；按实际结果更新能力验证 guide 与矩阵装载测试、阶段 0 spec 及 roadmap 中的能力计数。旧 fixture 不修改，生产代码和 evaluator 判定不修改；仅在新证据通过时允许修正过时的 limitation 文案。

**Preconditions:** Task 1 独立审查通过、原账本已记录 `browser-e2e-2026-11-07` 额度；使用固定 1.19.5 CLI、原工作区 `.env` 和同一 `/private/tmp/tap-opencode-task03-ledger.jsonl`。不输出或复制凭据。

- [x] 运行一次 `resume-workspace`，单次最多 10 次请求、180 秒、USD 0.60，输出到上述全新 fixture 路径；双 opt-in 开关启用。不得自动重试；失败按捕获的中断前置条件报告。
- [x] 验证真实 provenance、同一会话续接、三个时点动作计数都为 1、first 摘要稳定、second 在中断后缺失并在恢复后出现、已核实的 owned PID 停止。记录原账本实际结算与剩余额度。
- [x] 若监督器发送 SIGTERM，明确记录“监督器辅助停止”，不能宣称 OpenCode 原生 abort 已终止工具；后续 runtime 必须实现同等身份核实及确认停止。预置 waiter 不算模型生成脚本证据。
- [x] 若 evaluator 支持本次恢复证据，将新样本接入矩阵并保留旧不完整样本的回归；否则保持 unverified，只报告具体缺失。运行相关 fixture/evaluator 窄测与 diff/文档检查。不能因单项通过宣称阶段 0 或浏览器 E2E 完成。

Task 2 结果：`950de0fe` 已经独立审查，18 项 evaluator 测试通过；一次真实运行消耗 3 calls/USD0.003428，未中断、未恢复，门禁保持 unverified。原账本累计 120 calls/USD0.1530648，没有重试。

## Task 3：修复 macOS 进程身份读取与中断观察

**Diagnosis:** Task 2 的真实样本没有中断。离线已复现：同 PGID 的 `/usr/bin/python3 waiter.py` 仍被拒绝；组合 `ps` 输出截断 `comm`，真实 Xcode Python.app 的可执行名称为 `Python`，不能用小写 basename 正则推断身份。保存的最后一个条件快照无法解释此前窗口；历史 `any(bash running)` 会在工具完成后仍为 true。真实样本未保存 PGID；随后核对固定版本 shell.ts，非 Windows 的工具使用 detached 子进程，不能要求工具 PGID 等于 serve PID。改为下述严格的祖先链绑定，并以离线 detached 复现验证，不接受任意进程组。

**Files:** Modify `scripts/opencode_probe/scenarios.py`；允许最小修改 `process.py`、`legacy.py`，从监督端启动边界传递受信 serve 身份和存活检查；必要时新增小型 `scripts/opencode_probe/process_identity.py` 封装完整元数据与受信解释器预检；Modify `apps/tap-ai-backend/tests/unit/operations/test_opencode_scenarios.py` 及启动接口受影响的测试。保留 evaluator、权限、旧 fixture 和预算配置。

**Interfaces and invariants:**

- 监督端在固定子进程 PATH `/usr/bin:/bin:/usr/sbin:/sbin` 下启动自己持有 Popen 句柄的合成 Python 子进程，采集其完整可执行文件路径作为本次受信解释器身份。预检有短期限和 finally 清理，无网络，不读 `.env`；仅接受预检中该已知 PID 的完整可执行路径，不加入模糊 Python 名称白名单。macOS `sys.executable` 与进程实际 Python.app image 可能不同，应以已知进程的实际 image 为准。
- `_owned_marker_status` 用完整不截断的进程字段，核对受信解释器的完整路径、PID、实际工具 PGID、开始时间、精确 waiter.py 参数和 cwd。拆开读取元数据时确认开始身份仍相同，异常或身份变化仍 unknown。现有 `_post_abort_action` 与 `_signal_owned_waiter` 继续在发信号前复核同一身份；未证实停止不得续接。仅在报告保留身份摘要/安全枚举，不泄露主机完整路径。
- 所有权绑定：监督端必须在持有的 serve 子进程句柄仍存活时捕获启动身份；不能在绑定时将任意占据同一 PID 的进程当作受信根。在中断前，按有界的 PID/PPID/PGID/start 快照验证 waiter 的祖先链确实到达该受信 serve 身份，并再次检查句柄仍存活和开始身份匹配，工具 group leader 也在该链上；拒绝断链、循环、超过 16 层、变化的开始身份、其他根或未知进程。将已核实的 serve 身份、工具 PGID/leader 开始身份和 waiter 身份冻结在监督器内存；中断后允许已绑定 waiter 因父进程退出而被系统收养，但必须仍匹配原 PID/start/full executable/PGID/cwd/script，发信号前再次核对。没有中断前的祖先链绑定不能信号或续接，不能仅凭进程名/文件标记接管。进程观察有总期限和输出长度上限；不在证据中保存其他进程的命令或路径。
- 若新增内部 `TrustedInterpreter` 结构或 helper，其生产调用和所有相关测试一次更新；接口不对外发布。预检不属于 OpenCode 生成/执行证据，真实运行仍由 OpenCode bash 启动 waiter。
- root 状态继续按同一 session 的最新状态；bash 状态按同一根 session、part ID 折叠，只有最新状态为 running 才算当前执行。忽略其他 session，不把旧 running 事件当当前事实。缺 part ID 或状态不明确时保守为 false；此前已有 running 也必须失效，直到新的完整身份事件恢复可判断状态。
- 新增有界脱敏中断观察（最多 64 次状态变化，含相对时间、事件索引、root 状态、bash part 状态、marker PID/PGID 是否匹配和失败枚举）；同时保留现有最终 pre_abort_conditions。达到观察上限记录截断并保持未验证，不能静默丢掉关键条件。不要保存原始事件、命令输出、提示词或响应正文。

- [ ] RED：用实际 `/usr/bin/python3` 与受控自有进程组复现拒绝；再用受控 detached 子进程复现真实组结构，验证可绑定到自有根；同时测试未知解释器、无祖先关系的 PGID、变化的 PID/start/executable、错误 cwd/脚本均拒绝。所有活进程用 finally 清理，测试不等待 60 秒。
- [ ] RED：纯事件序列 `root busy → root bash running → 同 part completed → root idle` 的最终 active_bash 必须 false；其他 session running 不影响根，缺 ID 保守拒绝。测试观察轨迹上限和脱敏字段。
- [ ] GREEN：只修上述读取和观察问题；不能调整门禁、接受未经祖先链绑定的 PGID、增加模型输出或实验预算。
- [ ] 运行受影响 scenarios 测试，以及 import/调用签名受影响的 probe/evaluate 窄测；改动文件 Ruff/format/mypy 与 diff check。提交 `fix: verify complete macos waiter process identity`，报告确切命令和离线实际进程结果；实现者不执行真实模型实验。
- [ ] 独立审查后，控制器可另行授权一次新输出路径的同上限真实恢复复验，记录剩余额度和新的诊断证据。不是自动重试；若出现新的 PGID 或事件协议问题，先诊断，再决定，不通过松绑身份校验取得 supported。

Task 3 归档：实现与修复 `2509e23e` / `98bf9201` 已通过独立审查；最终一次真实验证仍因 `executable_mismatch` 未中断，保留 `resume-verified-identity.json`，不宣称恢复通过。用户批准复杂恢复后移，不再追加实验。

## Task 4：可核对的全任务预算停止证据

**用户批准后的本轮范围：** 先提交可复用的 BudgetGate 调用/费用/期限 cutoff、快照、接纳/转发/拒绝记录和单一结算的确定性实现与验证。下文 combined 子任务/暂停恢复场景、对应 evaluator 与真实实验后移；不把尚未实现或验证的完整预算门禁标为 supported。

**Files:** Modify `scripts/opencode_probe/gateway.py`、`scenarios.py`、`cli.py`、`evaluate.py`；必要时新增 `scripts/opencode_probe/budget_evidence.py` 承载检查点和判定，避免扩大场景主循环；Modify `apps/tap-ai-backend/tests/unit/operations/test_opencode_gateway.py`、`test_opencode_scenarios.py`、`test_opencode_evaluate.py`。不发真实调用、不改权限配置或旧 fixture。

**Interfaces and invariants:**

- `BudgetGate.snapshot() -> dict[str, object]` 在现有锁内返回脱敏快照：单个随机 `task_budget_id`、`limits`（`max_calls/max_seconds/max_cost_usd`）、`calls/reserved_cost_usd/elapsed_ms`、`forwarded`、`blocked_after_cutoff`、首次 `cutoff`。cutoff 包含 `cause`（`deadline/calls/cost`）、`elapsed_ms/calls_at_cutoff/reserved_cost_usd_at_cutoff/forwarded_at_cutoff`。
- 在同一 admission 锁下按 deadline、calls、cost 检查；首次 cutoff 一旦设置不可被之后原因覆盖，之后不再接纳或转发新请求。上游失败仍 fail closed，但不能伪装成预算 cutoff。保留每请求最坏费用预约；场景中不释放，只在网关关闭且所有 handler 结束后结算。
- 每个真实调用 record 增加 `admitted_elapsed_ms` 和 `reserved_cost_usd`，用于重算而非信任自报 totals；拒绝记录保留原因、相对时间和已脱敏 session ID。计数和转发数分开，成功证据中必须与完整 record 对齐。截止前接纳的请求可稍后完成，不得把 record 写入时间当成接纳时间。
- `_accounted_gate` 在最终 report 记录单一 ledger reservation/settlement（ID、实际 calls/charge、是否结算）及最终快照。历史 fixture 仍可读，但缺字段不能通过新预算判定。
- 新 CLI 场景 `budget-continuation`：先用真实 task 委派 SDE 写一个合成标记，再由根启动预置 waiter；按现有身份核对中断、确认停止，再同一根会话续接。全过程只用一个 BudgetGate 和一组 ledger reserve/settle。通过持久 child/parent/tool 与网关 child request 核实委派，通过 assistant parentID 核实续接。之后可发有限的简单合成续问直到同一调用预算拒绝；不可重启网关或刷新 deadline。
- 新场景 `budget-cost`、`budget-deadline`：分别通过保守预约触顶和短期限触发拒绝；deadline 使用预置受控等待，让模型调用不在到期时悬空。模型恢复后的请求须实际被拒绝或连接关闭，不能仅凭 runner timeout 判通过。保留 owned-process 清理，不能泄漏等待进程。
- `budget_trace` 的 `before_child/after_child/before_abort/after_abort/after_continuation/final` 检查点共享 budget ID/root ID，并含累计 calls/reserved cost/elapsed。检查点须单调；after_child 有真实 child request，after_continuation 有续接消息关联和之后接纳的调用。监督器辅助停止必须如实记录。
- 单独预算判定允许明确的预期 cutoff 导致 `terminal: incomplete`，不放宽 `verified_provenance`。要求固定真实 source/version/model/config、known sessions、完整可信 usage、限制与 cutoff 算术吻合、截止后至少一次阻止且 forward 不增加、单一结算；拒绝上游失败、未知用量、missing fields、reset counters/IDs、probe error。三个真实样本 key 为 `budget_count_continuation`、`budget_cost`、`budget_deadline`，全部满足才令 budget_enforcement supported。
- 保留全局账本与单实验 10 calls/180 秒/USD0.60/512 output tokens 上限。不同场景是独立任务；只有 combined 场景证明子任务/暂停恢复共用预算。Stage 0 不新增网关重启恢复机制，后续产品 runtime 再验证自己的持久恢复。

- [ ] RED：补网关三种 cutoff、并发接纳、不再转发、错误不能冒充预算的失败测试；补 trace ID/单调性/子调用/续接/结算缺失的拒绝测试。示意断言（对象由测试自有回环上游和真实网关产生）：

```python
before = gate.snapshot()
assert before["task_budget_id"] == after_resume["task_budget_id"]
assert before["calls"] <= after_resume["calls"]
assert final["forwarded"] == final["cutoff"]["forwarded_at_cutoff"]
assert final["blocked_after_cutoff"] > 0
```

- [ ] GREEN：实现以上最小 capture/场景/判定；单测的合成正例仅验证判定逻辑，不能产生 `opencode-real` 文件或提高真实能力矩阵。
- [ ] 验证上述三个受影响测试文件，及 cli/ledger 接口涉及的 `test_opencode_probe.py`；运行改动文件 Ruff/format/mypy、diff check。报告 RED/GREEN、确切命令和结果，提交 `feat: verify task-wide opencode budget cutoffs`，独立审查之后才执行真实场景。

Task 4 本轮收尾：`0a8f4249` / `62f04767` 完成上述收窄范围，独立审查通过；最终 33 项 gateway 测试与静态检查通过。完整累计恢复预算门禁仍未验证。后续当前实施入口为[核心闭环计划](2026-11-07-tapper-web-automation-core.md)。

## Task 5：三项有界真实预算实验

**交付顺序：** 按用户批准，完整累计恢复预算实验后移；本轮不启动本任务的三个真实实验。核心首版仍需对其实际使用的调用/费用/期限上限验证执行端截止。

**Files:** 新增 real fixture `budget-count-continuation.json`、`budget-cost-continuation.json`、`budget-deadline-continuation.json`；更新装载测试及能力 guide/spec/roadmap 的真实状态。不改旧样本，不放宽 Task 4 判定。

- [ ] Task 4 独立审查后，在同一原 ledger 顺序运行至多三个场景；每个结束先检查和结算，不盲目重试。调用数场景最多 10 calls/180 秒/USD0.60；费用场景最多 10 calls/180 秒/USD0.50；期限场景最多 10 calls/180 秒/USD0.50（网关短期限按确定性检查和模型首回合耗时选择并在报告记录）。总预约最多 30 calls/USD1.60；先核对剩余全局预算。
- [ ] 首个非预期失败即停止后续真实调用，依实际证据定位；不能提高 cap 让模型完成，不能拼接不同运行补齐 combined trace。
- [ ] 三项真实证据全部满足新 predicate 才更新能力状态，否则保持 unverified。运行 fixture/evaluator 窄测、文档链接与 diff 检查，提交后独立审查。

## 后续执行顺序与阶段退出

**2026-11-07 用户批准的优先顺序取代下列原顺序作为当前执行指令：** 收尾已经启动的一次恢复验证并归档，不追加恢复实验；Task 4 仅交付预算执行基础；随后实施通用 Web 核心闭环及独立权限隔离/费用上限/取消/跨站点验收。复杂恢复、完整八门禁和测试资产管理按下列原计划后续补齐。

1. 根据固定版本源代码和真实协议可达性，把 BYOK、权限和累计预算的缺口落实为有限实验与精确判定。源协议无法满足既定要求时提出具体重大取舍，不暗改要求；避免重复同一无效提示词。恢复只做一次完整实验，失败先依前置条件证据诊断。
2. 所有阶段 0 required 能力实测通过并经独立审查后，逐任务执行[阶段 2](2026-11-06-opencode-workflow-runtime.md) 2.1–2.4；每阶段保持原合入门禁，阶段 2 不宣称浏览器业务 E2E 已完成。
3. 逐任务执行[阶段 3](2026-11-06-versioned-test-automation-loop.md) 3.1–3.7，包括单写迁移、制品、人工发布、隔离执行和证据回写。FWD 已追加到 3.7；自有 Web 示例保留为可重复的确定性回归。
4. 最终按整体 spec §11.2 从 Tapper 入口验证至少两个站点、三类流程，含实现后选定的站点/任务组合；新增任务无需修改产品代码。FWD 按 §11.1 作为询价样例，返回本次结果、截图、trace、版本和 Run，并能重跑。只读网页调查、单独脚本或直接浏览器操作不算验收。外部阻塞如实记录，不写业务完成。
