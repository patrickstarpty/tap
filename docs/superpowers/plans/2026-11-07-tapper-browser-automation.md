# Tapper 真实浏览器自动化续建计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Checkboxes record verified progress.

**Goal:** 从 Tapper 发起 FWD HK MyTerm Plus 合成资料询价，经真实 OpenCode 生成和隔离 Playwright 执行，回传报价与证据，并能再次执行。

**Architecture:** 沿用双路径 Agent 工作流及 TAP 非 AI 后端的版本化资产/执行权威，不新增旁路执行器。先关闭阶段 0 的真实证据缺口，再依序实施原阶段 2、阶段 3；本计划只补充续建前置修复与 FWD 验收索引。

**Tech Stack:** 已固定 OpenCode 1.19.5、LiteLLM、Python、Playwright + TypeScript、Docker、MySQL、MinIO。

**Spec:** [整体设计 §11.1](../specs/2026-11-06-agent-workflows-and-test-loop-design.md#111-fwd-hk-真实询价验收2026-11-07-用户补充)、[阶段 0 设计](../specs/2026-11-06-opencode-capability-validation-design.md)。基线 `c492f3cc`，阶段 1 已交付，阶段 0 四项 supported/四项 unverified，阶段 2/3 未交付。

## Global Constraints

- 用户已授权继续实施并将一般工程决定交给控制器；重大范围、架构或实际投保行为仍需讨论。控制器统筹，GPT-6 Sol 实施。
- 阶段 0 所有 required 门禁必须有真实证据；不删除门禁、不把模拟或自然语言当执行证明。未知时仍 unverified，不解锁编码入口。
- 正式代码沿用阶段 2/3 计划的领域所有权、不可变版本、人工发布、回环绑定及隔离执行；不复制一个旁路工具冒充 Tapper 产品能力。
- 保留全部旧真实 fixture，不追写旧样本中的缺失事实；新证据使用独立文件。
- `.env` 仅由监督端读取；模型工作区、制品、日志和报告不包含原 LiteLLM 密钥。无关 `.env.bak-20261015`、`apps/tap-ai-backend/.quality/` 不读取、不修改、不提交。
- 本轮额外真实模型预算最多 120 次 / USD 2.00，历史 117 次 / USD 0.1496368 不归零。新增额度要在同一账本中明确记录，确定性检查和独立审查通过后才启用。每个实验先规定调用/时间/金额上限；缺证据不自动重复。
- FWD 测试仅使用 spec §11.1 的合成询价资料，止于公开报价；不提交投保、真实个人资料或付款。默认测试不访问外部网站。

## Task 1：可审计续建额度与确定性恢复前置条件

**Files:** Modify `scripts/opencode_probe/ledger.py`、`scripts/opencode_probe/scenarios.py`；Modify `apps/tap-ai-backend/tests/unit/operations/test_opencode_probe.py`、`test_opencode_scenarios.py`；必要时新增小型 `scripts/opencode_probe/resume_fixture.py`，仅负责监督端生成的合成 waiter。不改产品 runtime、权限 profile 或旧 fixture。

**Interfaces:**

- `ProbeLedger.authorize_continuation(grant_id: str, additional_calls: int, additional_cost_usd: float) -> tuple[int, float]`：监督端显式执行；在同一文件排他锁内核对已结算总额和现有额度，追加零消耗的授权事件，返回新累计上限。无新授权时仍为旧 120/USD2 上限。相同 ID 与相同参数幂等，ID 重用但参数冲突拒绝。存在未结算 reservation 时拒绝扩额；不重写历史行。新上限为已结算消耗加本次额外额度，不暗中继承未用旧额度。
- `prepare_resume_fixture(workspace: Path) -> str`：在任务私有工作区排他写入有界的 `waiter.py`，返回 SHA-256。脚本一次启动在 `first-action.log` fsync 追加一条 `first`，生成 `first.txt` 与现有进程所有权校验所需 PID 标记，再等待 60 秒，最后生成 `second.txt`。不给它额外系统操作或网络访问。
- `resume-workspace` 模型任务仅启动这个脚本一次；监督端按已有 busy/bash/进程身份条件中断，确认进程停止后在同一会话要求只生成 `second.txt`。保留三时点动作计数与摘要判定；不能通过重新执行 waiter 补齐结果。记录各中断前置条件以解释失败，而非只在全部满足时才留证据。

- [ ] 账本 RED：历史统计不变、旧上限生效、授权后最多增加指定额度、重放授权不再加额、冲突 ID/未结算预约/并发预约拒绝。代表性断言：

```python
ledger.append('prior', 117, 0.1496368, 0.0)
assert ledger.authorize_continuation('browser-e2e-2026-11-07', 120, 2.0) == (237, 2.1496368)
assert ledger.authorize_continuation('browser-e2e-2026-11-07', 120, 2.0) == (237, 2.1496368)
assert ledger.totals() == (117, 0.1496368)
```

- [ ] 恢复 RED：预置脚本内容固定且不覆盖已有文件、独立局部执行能捕获一条 durable marker/PID/first；受控停止后 second 不存在，未知进程仍不授权续接。测试使用短等待或受控进程停止，不真的等待 60 秒；不能执行危险资源样本。
- [ ] GREEN：仅实现上述接口和场景前置条件，保留现有 PID 身份复核、fail-closed unknown、源文件/输出/产物上限与网关关闭后结算。合成脚本预置不算 OpenCode 代码生成证据，只隔离恢复能力这一实验变量。
- [ ] 运行相关 operations 窄测、Ruff/format、mypy、文档链接与 diff 检查，提交 `fix: make opencode continuation probes auditable`；报告确切命令和结果。此任务不读取 `.env`、不扩真实账本、不发模型请求。
- [ ] 独立审查通过后控制器才在原持久账本执行一次本轮授权；记录总额、授权 ID 与上限。旧账本不存在或余额不符则先调查，不创建零余额替代。

## 后续执行顺序与阶段退出

1. 根据固定版本源代码和真实协议可达性，把 BYOK、权限和累计预算的缺口落实为有限实验与精确判定。源协议无法满足既定要求时提出具体重大取舍，不暗改要求；避免重复同一无效提示词。恢复只做一次完整实验，失败先依前置条件证据诊断。
2. 所有阶段 0 required 能力实测通过并经独立审查后，逐任务执行[阶段 2](2026-11-06-opencode-workflow-runtime.md) 2.1–2.4；每阶段保持原合入门禁，阶段 2 不宣称浏览器业务 E2E 已完成。
3. 逐任务执行[阶段 3](2026-11-06-versioned-test-automation-loop.md) 3.1–3.7，包括单写迁移、制品、人工发布、隔离执行和证据回写。FWD 已追加到 3.7；自有 Web 示例保留为可重复的确定性回归。
4. 最终以 Tapper 入口运行 FWD MyTerm Plus 流程，产品返回本次非零报价、截图、trace、版本和 Run，再通过产品入口重跑。只读网页调查、单独脚本或直接浏览器操作不算验收。存在外部验证码/地区限制等阻塞时如实报告，不写业务完成。
