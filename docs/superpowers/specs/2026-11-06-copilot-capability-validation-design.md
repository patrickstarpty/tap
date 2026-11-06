# Copilot 独立能力验证阶段设计

日期：2026-11-06。本文细化[整体设计](2026-11-06-agent-workflows-and-test-loop-design.md)的编码路径证据门禁与[阶段 0 计划](../plans/2026-11-06-copilot-capability-validation.md)。这是不接入产品入口或共享 Demo 数据的独立探针设计。当前完成 0.1 配置导出、0.2 确定性进程采集及 0.3 确定性能力矩阵；真实事件、模型和恢复能力均未实测，不能视为已交付。

## 1. 边界与冻结输入

探针在无租户数据的任务专属临时工作区运行，使用隔离 Copilot 配置目录和专用 BYOK 凭据，不读用户个人 Agent、历史或凭据文件；不自动升级本机候选 CLI `1.0.105`。生产编码运行时由阶段 2 单独设计与审查，不能直接依赖 `scripts/copilot_probe/`。真实模型须同时设置 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 和 `TAP_RUN_COPILOT_PROBE=1`；任一缺失只能 skip，不能标记 PASS。用户目前选择先完成确定性探针和阶段 1，真实验证待配置，因此阶段 2 保持阻塞。

`ProbeAgent` 冻结 ID、修订、职责描述、指令、工具组和启用状态。`export_profiles(root, agents, allowed_ids)` 在 `.github/agents/` 导出已启用且该任务获准使用的全部配置，返回相对路径到 SHA-256 的映射。内置 ID 为 `orchestrator`、`ba`、`sde`、`qae`、`sdet`；自定义 ID 仅接受 `[a-z0-9][a-z0-9_-]*`。重复 ID、无描述、未知工具或路径越界拒绝。导出文件名无 `tap-` 前缀。`orchestrator.agent.md` 是内部启动配置；**导出目录与摘要不是产品的可委派名册**，产品 Agents 目录、选择器和 @ 候选仍只能收录获准的专业 Agent。不能从导出文件集合直接构造用户可选名单。

Frontmatter 仅写 `name`、`description`、`tools`、`strict-tools-list`；修订写为 YAML 注释，正文写指令。描述和修订以带转义的双引号编码，正文保留换行。工具组只允许官方 Copilot CLI 自定义 Agent 文档中列出的 `read`、`grep`、`glob`、`edit`、`execute`；空列表必须写 `tools: []` 和 `strict-tools-list: true`，不能省略后落入全工具默认值。是否足以形成执行端权限边界仍由 0.3 实测，文件内容本身不证明权限生效。生成阶段先完整验证并写暂存目录，再原子放入尚不存在的快照目录；恢复遇已有目录必须逐文件核对内容及摘要，不重写、不覆盖。配置变更须建立新任务快照，不能依赖热加载。

## 2. 进程与事件证据合同（0.2，确定性部分已实现）

`ProbeRun(argv, cwd, env, timeout_seconds)` 使用显式参数和环境启动 CLI，不经 shell；`run_probe` 返回退出码、stdout/stderr 行与超时标志。两路输出并行读取并设每行上限；超时或取消停止整个进程组并等待回收。确定性采集的原始数据只保留在有界内存；真实采集若写入临时文件，必须使用受限临时目录，脱敏后才可保存证据。`classify_capture(exit_code, timed_out, terminal_seen)` 只有零退出且观察到协议终态才为 `complete`；EOF、非零退出、超时和不完整 JSON 均不能推断成功。

先用标准库 stub 覆盖交错输出、拆包 JSON、坏 JSON、超长行、非零退出、派生长活进程与取消清理。命令入口限定 `--scenario`、`--output`、`--max-seconds`、`--max-model-calls`，只提供预设无敏感样本，达到预算就停止后续调用。真正的事件字段、父子关联、终态及受控输入通道只能记录 CLI 实测值；日志 EOF、命令帮助和模拟 fixture 都不构成真实能力证据。目前 `process.py` 以独立进程组和有界内存捕获输出，`parse_jsonl` 只验证 JSON 对象语法，`classify_capture` 要求外部确认的终态。`cli.py` 已限定预设场景和参数；由于模型调用计数与终态 schema 未实测，它在 opt-in 下仍拒绝启动真实调用，输出 `unverified`，不把预算参数当作已执行的硬限制。真实采集与额度执行仍待 0.3。

## 3. 能力场景与判定（0.3 确定性门禁已实现，真实场景待实测）

| 能力 | 固定实验 | 支持所需证据 |
| --- | --- | --- |
| `byok_all_calls` | 主 Agent 问答、SDET 委派、辅助调用 | LiteLLM 请求与实例/用量关联，无未授权 provider |
| `child_identity` | 自动编码委派及显式 QAE 指定 | 实际子实例 ID、profile ID、开始和结束事件 |
| 简单任务 | 无工具问答 | 仅主 Agent，无无关专业实例 |
| `parallel` | 两个独立临时文件与有界子任务 | 两个子任务执行区间真实重叠，日志归属明确 |
| `permissions` | 只读 QAE 尝试修改文件 | 执行端拒绝，文件摘要不变 |
| `control_receipt` | 运行时与安全边界两种增补要求 | 已接收与已应用分别有证据；边界交付可接受但须注明限制 |
| `resume_workspace` | 首产物后暂停，恢复写第二产物 | 确认停止推进，旧产物不重复，工作区摘要一致 |
| `reconcile` | 控制进程失联、终态回执丢失 | 能核对旧会话/进程，不能盲目重启 |
| 名册规模 | 5、25、100 个虚构获准 Agent | 启动时延、可见的输入 tokens、委派准确性；缺用量标未知 |
| 配置冻结 | 运行中变更目录、另开新会话 | 老任务版本不变，新任务可核对；不依赖热加载 |

`CapabilityResult(name, status, evidence_refs, limitation)` 的状态只取 `supported`、`unsupported`、`unverified`。任何缺失证据、未知字段或未运行场景都为 `unverified`，不能写零用量或推测的通过结果。`ready_for_runtime` 仅当 `byok_all_calls`、`child_identity`、`parallel`、`permissions`、`control_receipt`、`resume_workspace`、`reconcile` 全部有真实支持证据才为真。脱敏 fixture 记录采集时间、CLI 版本、模型别名、配置摘要、字段来源和脱敏方法；不保存密钥或租户数据。确定性单测与真实模型结果分栏呈现。

## 4. 阶段交接（0.4，待证据）

固定组合须明确 CLI 版本、模型别名、BYOK 配置方式、启动模式、事件来源、续接方式、限制和可复现 fixture。优先使用 headless CLI；只有实际证据要求时才改变接口，不能临时引入 SDK/MCP 代理或在 UI 伪造子节点。复跑自动委派与恢复场景后，才将真实字段与能力限制映射进阶段 2 适配设计。外层 LangGraph 只持有父任务生命周期，不与 Copilot 同时规划其内部子任务。任一 required 能力未证实，阶段 2 不解除门禁；阶段 1 的原生通用任务工作可按独立计划推进。

官方格式依据：[GitHub Copilot CLI 自定义 Agent](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)。该文档证明字段形式和工具组示例，不替代本机 CLI 与 LiteLLM 的组合实测。
