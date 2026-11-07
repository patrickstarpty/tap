# OpenCode 能力验证记录

本记录适用于固定的 OpenCode 1.19.5、内部根 profile `probe-0.3-r2`、本地 LiteLLM `qwen-plus` 组合。样本来自 2026-11-07 的真实本机调用；[脱敏 fixture](../../apps/tap-ai-backend/tests/fixtures/opencode/real/)只保存会话标识、事件/工具状态、配置与产物摘要、网关计数和用量，不保存密钥、提示词或模型回答。评估逻辑在 [`evaluate.py`](../../scripts/opencode_probe/evaluate.py)。`ready_for_runtime` 仍为 **false**；阶段 2 不得据此解锁。

| 必测能力 | 结论 | 真实证据与边界 |
| --- | --- | --- |
| BYOK 全调用 | unverified | 受控网关记录了主会话、SDE/QAE 子会话的请求和用量；标题等辅助调用只与根会话相关，缺可靠的逐消息/辅助子类型关联；不能证明所有未观察到的路径。见 `simple.json`、`auto.json`、`parallel.json`。 |
| 子实例身份 | supported | `auto.json` 的 SDE 和 `explicit.json` 的 QAE 均有真实 `parentID`、`task` 工具返回的子会话 ID、开始/完成时间、终态和子会话网关请求。最初的 `auto-initial-no-child.json` 未自动委派，后用职责明确的 profile revision 重试；最初 `explicit-initial.json` 缺完整生命周期时间。 |
| 并行 | supported | `parallel.json` 两个子任务区间重叠，SDE/SDET 各自的 `write` 工具归属 `one.py`/`two.py`，根会话终态完成。`parallel-cost-stop.json` 是旧 USD 0.50 保守预约触顶样本，未把它算作成功。 |
| profile 权限 | unverified | `permissions.json` 的 QAE 子会话没有写入 `denied.txt`，但模型没有真正调用受拒绝的写工具，缺执行端拒绝事件。`permissions-initial-root-write.json` 中根自己写入文件，也不能证明 QAE 权限。 |
| 运行中增补 | supported | `control.json` 在根会话忙碌时接收新用户消息 ID，后续 assistant `parentID` 指向该 ID，`second.txt` 出现。只证明安全回合边界的实际应用，不证明工具执行中的即时注入。 |
| 暂停与恢复 | unverified | `resume.json` 在 `first.txt` 写成且 task-owned `waiter.py` PID 存活时 abort；该 PID 随后消失、第二产物当时不存在、同一会话续接后出现 `second.txt`。没有记录首次写动作的独立运行计数，不能证明它从未重做。`resume-same-session-partial.json` 和 `resume-abort-initial-error.json` 保留较早部分/错误样本。 |
| 控制进程失联核对 | supported | `reconcile.json` 的 task-owned controller 收到 `prompt_async` 回执后被 SIGKILL，OpenCode serve 仍存活；监督端找回原会话及最终产物，未新建会话。未覆盖 OpenCode serve 自身丢失。 |
| 执行端预算 | unverified | `budget.json` 在一次转发后，网关以 429 拒绝后续两次请求，未再向上游转发；`parallel-cost-stop.json` 证明单场景保守费用预约触顶。超时、子任务与恢复的全任务累计停止尚无完整实测证据。 |

额外场景：`simple.json` 的根会话回答 42，2 次根会话请求均有用量；它不单独证明辅助调用类型。`roster-5.json`、`roster-25.json`、`roster-100.json` 分别以 5、25、100 个允许的虚构 Agent 完成真实 SDE 委派，启动时间为 3.558、2.553、2.634 秒，观测输入 tokens 分别为 14580、15341、18180。第一次 25-Agent 预检因有效配置输出超过固定 512 行捕获上限而失败，0 次转发；`roster-25-preflight-failure.json` 保留该样本，随后仅将该预检的有界捕获扩大至 4096 行。`config-freeze.json` 在旧会话忙碌时把任务源 revision 从 r1 改为 r2：旧快照 SHA-256 未变，新任务会话载入不同 hash，两个会话都完成；不依赖热加载。

编码产物另有独立缺口：`auto.json` 的 SDE 产出 `square.py`，受限 AST 验证通过，`python -I` 退出码为 0；旧检查器强制输出恰好为空白分隔的 `4 9`，失败。题目只要求打印 2 和 3 的平方，未要求该格式。`auto-script-output-gap.json` 再次显示非纯数字输出，但没有保存完整 stdout，因此不能判定语义对错。后续检查器已改为接受 `4 9` 或标注的 `2→4, 3→9` 数值对，保留最多 256 字节的合成 stdout；本次不再追加真实调用，待后续既定复现验证。

真实烟测默认跳过。运行前设置受批准的本地 LiteLLM env 文件和同一持久 ledger 的路径，并同时启用两个开关：

```bash
export TAP_OPENCODE_GATEWAY_ENV_FILE=/path/to/approved/local/.env
export TAP_OPENCODE_LEDGER=/path/to/shared/opencode-ledger.jsonl
TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 TAP_RUN_OPENCODE_PROBE=1 \
  uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/smoke/test_opencode_capabilities.py -v
```

烟测最多预约 3 次模型尝试、90 秒、USD 0.20；每次运行前写入持久预约，进程异常丢失时仍计入总额。完整场景 CLI 另有每场景最多 10 次、180 秒的硬界；全任务 ledger 上限为 120 次、USD 2.00，含失败、辅助与子会话请求。不要复制或提交本地 `.env`。运行阶段 2 前必须重新评估全部八项并取得真实 supported 证据。
