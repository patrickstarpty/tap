# Tapper 自动执行与任务详情实施计划

> 使用 executing-plans 在当前隔离工作树逐项执行；用户要求主 agent 亲自实施，不再委派。

**Goal:** 自然语言直接执行，默认进度可理解，每个动作可诊断。
**Architecture:** 复用 NativeWorkflowGraph 路由与现有 Browser/CodingRuntime；将自动浏览器意图冻结为任务事件，并统一用于执行与证据投影。新增有界动作事件，前端按产品概念展示，诊断按需展开。
**Tech Stack:** Python/FastAPI/LangGraph、React/TypeScript、Playwright。
**Spec:** [设计](../specs/2026-11-08-tapper-automatic-execution-design.md)。

## 全局约束

不硬编码 FWD 业务操作；不添加依赖；隔离、授权、预算、取消保持；不公开原始模型请求或密钥；当前累计 242/250 次、USD 1.4703832/2.1496368，付费重试先预留。

## 1. 动作证据与页面检查

Files: `deploy/browser/inspector.mjs`、`apps/tap-ai-backend/src/tap/modules/workflows/adapters/opencode_runtime.py`、`application/queries.py` 及对应测试。

- [x] 增加失败测试：自定义 combobox/button 可检查，错误后仍有 DOM；动作事件带输入隐藏标记、目标、耗时、错误，失败原因经过脱敏。
- [x] 在现有观察结果 fields 中提供有界交互控件及实际值、定位器；用同一快照路径处理操作错误。
- [x] 在 Browser action 前后写持久化事件，查询按字段脱敏、保留原事件顺序。
- [x] 跑运行时与投影定向测试及真实隔离浏览器回归。

## 2. 用户任务详情

Files: `RunDetail.tsx`、`ActivityTimeline.tsx`、`RunStatusLink.tsx`、`WorkflowSidebar.tsx`、`workflow.css` 及对应测试。

- [x] 测试默认产出/Agent/来源分区、状态分组、真实失败动作可展开和空状态。
- [x] 以紧凑清晰的结果与任务列表替换默认图和流水日志；诊断详情按需展开，保留原命令/重放/断线交互。
- [ ] 在聊天进度入口展示最新真实动作，不刷重复事件；检查宽屏与窄屏及键盘可访问性。

## 3. 自然语言自动浏览器路由

Files: workflows `routing.py`、`native_graph.py`、`dispatch.py`、浏览器意图投影辅助模块及查询/重放调用点；`TapperChat.tsx`、`TapperWorkspace.tsx`、`browser-core-live.spec.ts`。

- [x] 测试原生模型选择 browser 后进入现有运行器、普通回答不执行、URL 缺失询问、能力禁用不执行、同预算计费及冻结意图重用。
- [x] 将 browser 纳入既有模型路由，冻结来源于用户输入的 URL/目标和现有模型配置；投影和重放消费同一冻结事件。
- [x] 移除首次聊天的 Browser run 门槛，保留手选 Agent 与阻塞输入续跑；E2E 直接提交自然语言。
- [x] 更新规格、路线图和架构的相应段落；跑定向测试、契约/类型检查和一次核心回归。

## 4. 验收与交付

- [x] 核实本轮失败资源清理并结算，保留旧证据。
- [ ] 无费用验证全部通过后，在剩余额度内或取得追加授权后真实 FWD 验收。
- [x] 审查最终 diff，仅提交本轮文件，合入 develop 并推送（`96f5d951`）；不将未完成 FWD 标为通过。

## 验证记录（2026-11-08）

- `make check` 通过：`/private/tmp/tapper-auto-bdd-check.log`；`make test` 587 项通过：`/private/tmp/tapper-auto-bdd-core-test.log`。随后增加强制计划执行闸门，运行时 48 项通过：`/private/tmp/tapper-final-runtime.log`；模拟路由网关 7 项通过。
- 最终自动入口隔离 E2E 通过：owner `tap-browser-core-5f65106522e9`，任务 `8467d9a315d3492b9b5cdfb6020ef5f5`，冻结重放 `6650deabb4dc4852a6df18c65f7dfb54`。BDD 在动作前持久化，Then 真实执行收据 Passed；截图已检查 Outputs / Subagents / Sources 同屏。模拟验证不计为真实模型验收。
- 官方引用真实 FWD 只读预检：`/private/tmp/tapper-fwd-official-refs-preflight-20261108.json`，年龄 combobox、radio 控件及实际选中值可读取，停止收据 true；未执行询价、未调用模型。
- 剩余 8 次调用，本轮尚未新增付费调用。追加调用授权待用户选择；FWD HK 全链路及 Codex 侧栏渲染故障仍未验收。

- 自动路由取消隔离 E2E 通过：owner `tap-browser-core-22eb098451fb`，终态 canceled 且浏览器停止收据 stopped=true；最终进度组件 35 项通过，类型、格式、lint 通过。
