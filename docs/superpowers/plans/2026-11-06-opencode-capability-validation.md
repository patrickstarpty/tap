# OpenCode 能力验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 OpenCode CLI 和 LiteLLM 证明本设计依赖的委派、事件、模型路由、控制与恢复能力，产出可复现能力矩阵。

**Architecture:** 建立独立探针，不接入产品入口或共享演示数据库。探针先冻结输入与配置，再启动受限 CLI 进程，保存脱敏事件和证据；能力结论来自实测而非文档推断。

**Tech Stack:** Python 3.13.12、pytest、标准库 subprocess/asyncio/json、OpenCode CLI、现有 LiteLLM。

**Spec:** [整体设计](../specs/2026-11-06-agent-workflows-and-test-loop-design.md) §3–5、§12；[总计划](2026-11-06-agent-workflows-and-test-loop.md)。

## Global Constraints

- 沿用总计划全部约束；这是阶段 0，允许无产品 UI 的独立验收。
- 当前 shell PATH 未找到 OpenCode；Task 0.1 先在隔离工具目录安装并固定 CLI 版本，核对分发来源和依赖，不改全局配置。帮助成功不算模型调用成功。
- 使用无租户数据的临时工作区、隔离 OpenCode 配置目录和专用 BYOK 凭据；不读用户个人 Agent/历史/凭据文件，不自动升级 CLI。
- 运行真实模型需 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 与新开关 `TAP_RUN_OPENCODE_PROBE=1` 同时开启；缺少任意开关则 skip，不能显示 PASS。
- `.opencode/agents/*.md` 无 `tap-` 前缀；只导出当前项目已启用且获准的全部 Agent。代码与日志不得包含密钥，输出报告仅存模型别名和配置摘要。
- `scripts/opencode_probe/` 是验证工具，不作为后续生产运行时依赖；可复用验证样本与能力合同，生产实现由阶段 2 单独审查。

旧 Copilot 探针已实现的确定性工作仍属于旧 provider；本计划全部未执行。下列 OpenCode 文件、开关和命令均为待实现项，不能直接当作现有命令运行。

## 文件与交付边界

| 文件 | 职责 |
| --- | --- |
| 新 `scripts/opencode_probe/models.py` | 冻结配置、场景、证据与能力结果 |
| 新 `scripts/opencode_probe/profiles.py` | 从已授权配置生成目录与摘要 |
| 新 `scripts/opencode_probe/process.py` | 进程启动、事件采集、超时与清理 |
| 新 `scripts/opencode_probe/evaluate.py`、`cli.py` | 场景执行、能力判定、命令入口 |
| 新 `BT/unit/operations/test_opencode_probe.py` | 不使用模型的配置与协议测试 |
| 新 `BT/smoke/test_opencode_capabilities.py` | 显式 opt-in 的真实场景 |
| 新 `BT/fixtures/opencode/` | 脱敏的真实事件 fixture 及 provenance |
| 新 `docs/guides/2026-11-06-opencode-capability-validation.md` | 运行方法、矩阵和实际证据引用 |

### Task 0.1：冻结配置与生成 Agent 文件

**Files:** Create 上表 `models.py`、`profiles.py`、单测文件；Create `docs/superpowers/specs/2026-11-06-opencode-capability-validation-design.md`。

**Interfaces:** `ProbeAgent(agent_id: str, revision_id: str, description: str, instruction: str, tools: tuple[str, ...], enabled: bool)`；`export_profiles(root: Path, agents: tuple[ProbeAgent, ...], allowed_ids: frozenset[str]) -> dict[str, str]` 返回文件相对路径到 sha256 的映射。内置 ID 为 `orchestrator/ba/sde/qae/sdet`；其他 ID 仅允许 `[a-z0-9][a-z0-9_-]*`，重复、越界路径、无描述均抛 `ValueError`。根 orchestrator 使用 primary 模式，专业 Agent 使用 subagent 模式。先写临时目录，全部成功再原子替换；恢复时核对摘要，不重写旧快照。

- [ ] 先安装固定 OpenCode 版本到隔离工具目录，核对 run/serve 帮助和配置 schema；记录版本及来源，再写独立阶段设计，固定下列场景、能力判定和 opt-in，不增加正式产品能力。
- [ ] 写失败测试，另覆盖禁用、越权、重复 ID、换行/YAML 引号、同版本导出字节一致、生成失败不留下半份目录：

```python
from scripts.opencode_probe.models import ProbeAgent
from scripts.opencode_probe.profiles import export_profiles

def test_profiles_include_all_allowed_agents_without_prefix(tmp_path):
    agents = tuple(ProbeAgent(x, "r1", "负责审查", "核对证据", ("read",), True)
                   for x in ("orchestrator", "qae", "sdet"))
    files = export_profiles(tmp_path, agents, frozenset({"orchestrator", "qae"}))
    assert set(files) == {".opencode/agents/orchestrator.md",
                          ".opencode/agents/qae.md"}
```

- [ ] 执行 `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/operations/test_opencode_probe.py -q`，预期先因缺模块/函数失败。
- [ ] 实现 OpenCode frontmatter（description/mode/model/permission）与正文编码；权限默认拒绝再按授权放行，未知平台工具映射拒绝。只读 QAE 同时限制 edit、bash 与其他可写工具；主 Agent 的 task 规则只允许获准专业 Agent，禁用未授权内置 Agent。TAP 显式 @ 请求另做服务端授权，不能只依赖 task 规则。
- [ ] 同命令通过后，提交 `test: add frozen opencode probe profiles`。

### Task 0.2：进程、事件与协议发现

OpenCode run 的进程终止与 serve 的 session 终态分开判定；serve 是长活 HTTP 服务，不能等待其退出才算任务结束。只绑定回环并使用任务专属服务认证；先核对安装版本的 OpenAPI，再验证输入与取消语义。HTTP 接收不等于要求已应用，abort 请求返回不等于工具/子进程全部停止。

**Files:** Create `process.py`、`cli.py`；Modify 单测；Create `BT/fixtures/opencode/stub_cli.py`。

**Interfaces:** `ProbeRun(argv: tuple[str, ...], cwd: Path, env: dict[str, str], timeout_seconds: int)`；`run_probe(spec: ProbeRun) -> ProbeCapture`（async），`ProbeCapture` 含 `exit_code: int | None`、`stdout_lines: tuple[str, ...]`、`stderr_lines: tuple[str, ...]`、`timed_out: bool`。调用使用 argv 和显式 env，不使用 shell。stdout/stderr 并行读取，按行限额，超时先停止进程组再等待回收；原始数据仅在有界内存或临时受限目录，脱敏后才写证据。

- [ ] 写失败测试：stub 分别输出交错 stdout/stderr、拆包 JSON、无效 JSON、超长行、非零退出及派生长活进程；确认不死锁、不把 EOF 视为成功、取消后无存活子进程。代表性分类函数定义为 `classify_capture(exit_code: int | None, timed_out: bool, terminal_seen: bool) -> str`：

```python
from scripts.opencode_probe.process import classify_capture

def test_zero_exit_without_terminal_is_incomplete():
    assert classify_capture(0, False, False) == "incomplete"
    assert classify_capture(None, True, False) == "timed_out"
    assert classify_capture(0, False, True) == "complete"
```

- [ ] 运行 0.1 单测命令，确认新增断言失败；实现进程封装，stub 用标准库生成这些行为。
- [ ] CLI 实现 `--scenario`、`--output`、`--max-seconds`、`--max-model-calls`；每个场景只使用预定义无敏感样本；额度达到后停止后续调用并记录预算超限。没有可信模型调用计数和执行端限额时拒绝真实运行，不能把 token 统计当作调用次数。
- [ ] 帮助输出与单测通过。仅在 opt-in 下采集一个真实任务的 JSONL/可用监控事件，记录启动模式、CLI 版本、事件名称、字段及父子关联来源，不先假定 JSONL 含完整子 Agent 生命周期。当前未安装、未采集；真实字段与关联保持 unverified。
- [ ] 提交 `test: capture opencode lifecycle evidence`。

### Task 0.3：能力矩阵与真实场景

自定义 provider 指向现有 LiteLLM；按实际模型协议核对 Chat Completions / Responses 适配方式。显式固定主/子模型和 small_model，以及标题、摘要、压缩等辅助调用的路由；禁用未授权 provider、插件与配置合并来源。所需机密只通过运行环境注入，快照仅含配置引用与摘要。

**Files:** Create `evaluate.py`、`BT/smoke/test_opencode_capabilities.py`；Modify `cli.py`、`.env.example`；Create 上述 guide。

**Interfaces:** `CapabilityResult(name: str, status: Literal["supported", "unsupported", "unverified"], evidence_refs: tuple[str, ...], limitation: str | None)`；`ready_for_runtime(results: tuple[CapabilityResult, ...], required: frozenset[str]) -> bool`。未知与缺证据一律不通过。

| 必测能力 | 输入和实验 | 通过证据 |
| --- | --- | --- |
| BYOK 全覆盖 | 主 Agent 问答、委派 SDET、辅助调用 | LiteLLM 请求/用量与实例关联；无未授权 provider |
| 自动与显式委派 | 自动化编码任务；明确指定 QAE | 实际 provider 子 session ID、Agent ID、父子关联与开始/结束证据 |
| 简单任务 | 一个无需工具的问题 | 仅主 Agent，无无关专业实例 |
| 并行 | 两个独立临时文件，两个 bounded 子任务 | 两个子任务执行区间真实重叠，日志归属明确 |
| profile 约束 | 只读 QAE 尝试修改文件 | 执行端拒绝，文件摘要未变 |
| 增补要求 | 正在运行时提交变更；另测安全边界续接 | 接收与实际应用能区分；至少支持边界交付 |
| 暂停/恢复 | 写第一个产物后暂停，恢复写第二个 | 停止推进被确认，已写产物不重复，工作区摘要一致 |
| worker/CLI 失联 | 杀控制进程、丢失最终回执 | 找到或明确无法找到旧会话/进程；不盲目重复启动 |
| 名册规模 | 5、25、100 个允许的虚构 Agent | 启动时间、输入 tokens 可见则记录、委派正确性；未知用量标未知 |
| 执行端预算 | 超时、调用数和费用超限，含子任务与恢复 | 全任务累计不重置，执行端停止；超限不再发起模型请求 |
| 配置冻结 | 运行中改目录；新会话重新载入 | 老任务版本不变，新任务版本可核对；不依赖热加载 |

- [ ] 写门禁测试并先失败：

```python
from scripts.opencode_probe.evaluate import ready_for_runtime
from scripts.opencode_probe.models import CapabilityResult

def test_unverified_event_identity_blocks_runtime():
    result = CapabilityResult("child_identity", "unverified", (), "未见子实例事件")
    assert not ready_for_runtime((result,), frozenset({"child_identity"}))
```

- [ ] 实现确定性矩阵评估、provider provenance 校验及双 opt-in；缺可信计数与终态时阻断，拒绝用旧 Copilot fixture 解锁。required 固定为 `byok_all_calls/child_identity/parallel/permissions/control_receipt/resume_workspace/reconcile/budget_enforcement`；`control_receipt` 允许已证实的边界交付，报告必须显示该限制。
- [ ] 执行确定性单测通过，再运行：

```bash
TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 TAP_RUN_OPENCODE_PROBE=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_opencode_capabilities.py -v
```

- [ ] 将原始记录脱敏并存入 fixture；记录样本来源、CLI/模型/配置摘要、采集时间、脱敏方法。guide 中逐项填真实证据；无法验证填写 unverified 和原因，不能写推测的通过结果。
- [ ] 提交 `test: verify opencode workflow capabilities`。

### Task 0.4：确定接入方式与阶段交接

**Files:** Modify 阶段设计、guide、[阶段 2 计划](2026-11-06-opencode-workflow-runtime.md)、产品路线图。

**Interfaces:** 交付一个固定组合：CLI 版本、模型别名、BYOK 配置方式、进程模式、事件来源、续接方式、已知限制，以及 phase-2 使用的 fixture。最小基线使用 opencode run JSON，实时交互候选使用 opencode serve 的回环 HTTP/SSE；按真实控制、事件与恢复证据选择接口，不额外引入 SDK/MCP 代理。

- [ ] 对照所有 required 结果；八项均无真实证据，维持阻塞，不修改 UI 造事件。
- [ ] 将阶段 2 adapter 所需的实际字段类别和证据来源写入计划，并确认单次运行不同时由外层 LangGraph 和 OpenCode 规划子任务。
- [ ] 根据真实脱敏 fixture 将观察到的字段名和事件 schema 映射进阶段 2 adapter；当前无 OpenCode 真实记录，不能预填。
- [ ] 复跑一条自动委派与一条恢复场景，确认固定组合可复现；保存报告引用。
- [ ] 对变更文档进行链接、Markdown 与差异检查；路线图记为“能力验证存在具体缺口”，未标记编码运行已交付。
- [ ] 提交 `docs: record opencode integration capability gate`，满足门禁后进入阶段 2。
