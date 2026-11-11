---
id: ADR-038
status: accepted
date: 2026-11-08
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-038：复用 OpenCode 主 Agent 与浏览器执行边界

## 背景

用户要求对标 Claude Code/Codex 的持续完成能力，并于 2026-11-08 明确要求 TAP 不造轮子、优先直接复用 OpenCode。此前 browser-use 主控、冻结机械步骤和确定性脚本生成的方案未通过 FWD/AIA 验收；本决定据最新要求调整默认职责。

## 决策

OpenCode 1.19.5 拥有主会话、上下文压缩、工具循环、编码和纠错。TAP AI 复用已有 `CodingRuntime`、会话 server 客户端、任务预算网关及取消边界；不新增通用 Agent 引擎。当前模型保留 qwen3.8-max 的真实验收选择，模型切换不作为解决接入问题的前提。

现有 Browser API/worker 和 Playwright 页面观察作为 OpenCode 原生工具暴露。工具使用既有私有邮箱直接传递结构化动作和完整服务观察；不再由模型生成 shell JSON，也不增加 MCP 协议实现或依赖。browser-use 兼容实现保留，不与 OpenCode 竞争默认页面控制权；后续独立委派需另行验证收益和边界。

TAP 持有业务目标、冻结用户输入、授权、费用、证据和验收。BDD 记录业务步骤与预期结果，允许根据页面反馈调整方法。有效 Then 读值必须来自实际 Browser API 回执，写动作后重新核验；不能以模型自报或打开控件代替结果。原生工具的错误与当前页面事实返回同一 OpenCode 会话。

TAP 非 AI `browser_automation` 继续持有页面会话、动作、不可变脚本包及证据。现场完成使用既有 live execution 合同保留原录像、结果、截图和 trace；OpenCode 在同一会话输出脚本，不因此重做已执行的业务。用户主动回放才创建新执行，冻结回放保持零模型调用，每次生成独立新录像。业务与脚本/回放分别验收。

模型请求传输提高至 8 MiB，邮箱封装 12 MiB，会话响应读取 16 MiB，模型窗口维持 129024 token；移除 100 条消息硬限制，继续复用 OpenCode 原生压缩并按预算计费。禁用插件的 `--pure` 不再用于该隔离容器；配置目录固化为只读，禁止继承宿主配置，仍保留无外网容器和已有网关授权。

## 后果与验证

这是对原 ADR-038 的最新职责修正；此前 browser-use/冻结机械步骤/零模型脚本生成的详细设计保留历史，不再描述默认入口。继续保留 ADR-037 的隔离、预算、证据归属，和 ADR-039 的公开网站访问策略。

插件离线加载需以真实固定二进制验证。定向测试、合成模型完整链路、真实 FWD/AIA 和冻结回放分别记录，任何一项不能替代其余验收。容器销毁后的完整会话恢复、长任务真实压缩与直接框架对照尚待验收；不宣称已具备 Claude Code/Codex 的全部能力。人工接管、登录、多标签和上传下载不在本次最小实现内，V1 六项能力不变。

详细范围见[已确认设计](../superpowers/specs/2026-11-08-tapper-agent-feedback-loop-design.md)及[实施计划](../superpowers/plans/2026-11-08-tapper-opencode-native-tools.md)。
