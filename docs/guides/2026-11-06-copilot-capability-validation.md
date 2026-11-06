# Copilot 能力验证指南与矩阵

阶段 0.3 当前只完成可重跑的确定性门禁。真实 Copilot CLI、LiteLLM、模型调用和子实例事件均未运行；本页所有真实能力保持 `unverified`。这不阻止阶段 1 的原生工作流开发，但阶段 2 的 Copilot runtime 门禁仍关闭。

## 运行确定性检查

在仓库根目录运行：

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/operations/test_copilot_probe.py apps/tap-ai-backend/tests/unit/operations/test_copilot_capability_matrix.py -q
uv run --project apps/tap-ai-backend python -m scripts.copilot_probe.cli --scenario matrix --output /tmp/copilot-capability-matrix.json
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_copilot_capabilities.py -v
```

矩阵 CLI 只输出全部场景的 `unverified` 占位结果和 `ready_for_runtime: false`，不访问 provider。单场景 CLI 在缺任一开关时报告 `execution: skipped`；即使两个开关均为 `1`，可信模型调用计数和终态事件 schema 尚未确立时也报告 `execution: blocked` 并返回非零。退出码为 0 的 skip 只代表命令成功写入诊断，不代表能力通过。默认 smoke 逐场景 skip；双开关 smoke 若仍被阻断会明确失败，不能把 `unverified` 比较为 `unverified` 算作成功。

## 真实验证准备与证据

当前没有可用的隔离 BYOK 探针配置。准备时应使用无租户数据的任务专属临时工作区、隔离 `COPILOT_HOME` 和专用 BYOK 凭据，核对 CLI 版本、模型别名、LiteLLM 路由与可执行预算；不要读取个人配置、历史或凭据文件。必须先证实模型调用计数和终态事件的字段及执行端限额，才能解锁真实场景。届时显式设置两个开关，运行：

```sh
TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 TAP_RUN_COPILOT_PROBE=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_copilot_capabilities.py -v
```

真实记录须先脱敏，再保存为 fixture；同时记录来源场景、采集时间、CLI 版本、模型别名、配置摘要、原始事件字段来源和脱敏方法。证据引用必须指向可核查的真实记录；确定性构造的 `supported` 单测样本和官方文档均不算运行证据。未知用量记录为未知，不能写零。`control_receipt` 若只证实边界交付，可记为 `supported`，但必须在 `limitation` 和本页明确写出实时应用尚未证实。

## 当前接入结论与阶段交接

截至 2026-11-06，阶段 0.1–0.3 已提供冻结 profile 导出、受控进程捕获和确定性矩阵判定；最近一次窄测为 `36 passed, 10 skipped in 2.49s`，无警告。`1.0.105` 仅为已检查的 CLI 候选版本，不是通过验证的固定组合。真实 runner、执行端模型调用硬计数、provider 终态及子实例字段、会话/工作区恢复和 BYOK 路由均未实现或未核实；因此没有真实 fixture，也没有可填写为通过的 CLI/模型/BYOK/事件/续接组合。阶段 1 可按独立计划推进；阶段 2 继续受阻。

| 交接项 | 已知事实 | 解锁阶段 2 前所需证据 |
| --- | --- | --- |
| CLI、模型与 BYOK | CLI `1.0.105` 是候选；模型别名、隔离 BYOK 配置方式及 LiteLLM 全调用覆盖未知 | 记录实测版本、模型别名、配置摘要，并将主/子/辅助请求与 LiteLLM 使用记录关联 |
| 启动与额度 | 确定性探针通过显式 argv 捕获；真实 headless 启动器和可信模型调用计数未具备 | 在隔离进程模式验证硬额度、停止行为及真实调用来源 |
| 事件与委派 | JSONL 解析只确认语法；provider 终态、子实例身份、profile、父子关联、开始/结束及源游标未知 | 保存脱敏原始事件与字段来源，验证自动委派、显式 QAE、并行和真实终态 |
| 控制与续接 | 暂停、接收/应用回执、旧会话核对和工作区续接未实测 | 验证执行端边界、旧会话/进程定位、产物摘要及恢复后不重复执行 |
| fixture 与复跑 | 无真实 fixture；固定组合的可复现性尚未验证 | 同一固定组合复跑自动委派与恢复场景，保存脱敏 fixture、provenance 和报告引用 |

阶段 2 的 adapter 只能按这些真实字段及来源建立映射。外层 LangGraph 只管理父任务生命周期与交接；单次编码运行的内部子任务仅由 Copilot 规划。当前严格工具白名单未包含 CLI 文档中的 `task`，内部 root profile 也尚未实测其不可委派边界；两项都须纳入隔离实测，不能由配置文本推断。参见[阶段设计](../superpowers/specs/2026-11-06-copilot-capability-validation-design.md)与[阶段 2 计划](../superpowers/plans/2026-11-06-copilot-workflow-runtime.md)。

## 能力矩阵

| 场景 | 状态 | 真实证据 | 待验证内容与原因 |
| --- | --- | --- | --- |
| `byok_all_calls` | `unverified` | 无 | 主 Agent、SDET 子实例和辅助调用与 LiteLLM 请求/用量逐项关联；隔离 BYOK 配置待提供。 |
| `child_identity` | `unverified` | 无 | 自动委派及显式 QAE 的子实例 ID、profile ID、开始/结束事件；真实事件 schema 未核对。 |
| `simple` | `unverified` | 无 | 无工具问答只启动主 Agent；真实运行待配置。 |
| `parallel` | `unverified` | 无 | 两个独立临时文件任务的执行区间重叠和日志归属；真实运行待配置。 |
| `permissions` | `unverified` | 无 | 只读 QAE 修改文件须由执行端拒绝且文件摘要不变；仅 profile 文本不构成证据。 |
| `control_receipt` | `unverified` | 无 | 运行时增补和安全边界续接须区分接收与实际应用；实时或边界交付均未证实。 |
| `resume_workspace` | `unverified` | 无 | 首产物后暂停、恢复后第二产物、旧产物不重复及工作区摘要；真实续接待配置。 |
| `reconcile` | `unverified` | 无 | 控制进程失联、终态回执丢失后的旧会话核对，不盲目重启；真实恢复待配置。 |
| `roster_scale` | `unverified` | 无 | 5、25、100 个虚构获准 Agent 的启动时延、可见输入 tokens 和委派准确性；未知用量记未知。 |
| `config_freeze` | `unverified` | 无 | 运行中变更目录后旧任务版本不变，新会话重新载入；不能依赖热加载。 |

固定 runtime 门禁为 `byok_all_calls`、`child_identity`、`parallel`、`permissions`、`control_receipt`、`resume_workspace`、`reconcile`。缺结果、`unsupported`、`unverified`、空证据引用或重复冲突均不通过，传入空 `required` 不能绕过固定七项。其他三个场景也必须逐项记录，不能借固定门禁省略实验。

## 文档预检：委派工具与配置边界

[Copilot CLI 命令参考](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)列出 `task`（运行子 Agent）、`list_agents`、`read_agent` 和 `write_agent`。当前 `scripts/copilot_probe/profiles.py` 严格白名单只有 `read`、`grep`、`glob`、`edit`、`execute`，未含 `task`；真实自动与显式委派前必须在隔离场景验证工具组映射并按实测结果扩展。该参考也将 `infer` 描述为控制主 Agent 自动委派，因此 `infer: false` 不能证明根 Agent 文件无法被显式选作子实例。需在真实场景核对 root profile 的可见性与显式委派边界。当前不因文档推断修改 profile 规则。

[自定义 Agent 指南](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)说明 `.agent.md` 格式和委派用法；两份官方文档只支持格式预检，不替代本机 CLI、模型及 LiteLLM 组合的运行证据。
