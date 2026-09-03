# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Athena 面向参与软件生命周期的各类角色，包括 BA、产品、架构、开发、QA/SDET、运维和交付管理者。产品不以单一职业作为永久默认视角。

所有角色共享同一套核心工作区、项目事实、会话和证据链。角色与权限可以改变首页入口、快捷操作、默认筛选、可见数据和诊断深度，但不应把 Athena 分裂成互不一致的多套产品。

## Product Purpose

Athena 当前是一套以测试为核心的智能工作平台。它把需求与项目证据转化为可评审的测试用例和测试脚本，驱动受控执行，并把结果、运行证据和知识重新连接到同一条可追溯链路。

可信知识能力贯穿整个流程：用户可以基于需求、设计、文档、代码、BDD、测试资产和故障记录获得有来源、可核验的回答，并用这些证据理解、生成、执行和改进测试工作。

成功不只意味着生成内容，而是用户能够从输入意图一路追踪到测试设计、脚本、执行结果和证据，并清楚判断哪些内容是事实、建议、候选变更或已验证结果。

## Positioning

Athena 不是通用聊天机器人，也不只是测试用例生成器或测试执行器。它的差异化机制是把软件交付知识、测试资产、生成过程、确定性验证、运行结果和引用证据连接成同一条可审计工作流。

产品当前聚焦测试领域，但信息架构和能力边界不得阻止未来扩展到需求、设计、开发、发布和运维等更完整的软件生命周期。

## Operating Context

- 用户在 Project 范围内工作，并受环境、语料范围、角色和权限约束。
- 典型输入包括需求、设计文档、项目知识、代码、BDD、既有测试资产、运行结果和故障记录。
- 核心工作流是：理解需求与证据 → 生成测试用例 → 生成测试脚本 → 评审与验证 → 执行 → 查看结果与证据 → 诊断和改进。
- Knowledge Chat 提供项目会话、历史恢复、来源范围、流式状态、精确引用、Trace 和反馈能力，并作为其他测试工作流的知识入口，而不是孤立页面。
- 产品采用共享核心加角色适配的混合模式：保持一致的对象、状态与证据语言，同时根据角色和权限调整默认体验。

## Capabilities and Constraints

### Product target

- 来源与知识管理：接入、查看、检索、版本、处理状态、重试、删除、权限和新鲜度。
- 可信知识问答：Project、Conversation、历史、流式回答、停止、排队、分支、Quick/Deep、资源引用、claim-level citation、Sources/Claims、Retrieval Trace 和反馈。
- 测试设计：根据需求和证据生成、编辑、比较和评审测试用例。
- 测试实现：生成或更新测试脚本，展示结构化资产、语义差异、验证结果和来源依据。
- 测试执行：配置并发起受控执行，展示任务、Attempt、状态、日志、截图及其他证据。
- 结果闭环：归一化结果、失败分析、RCA/自愈候选、重试、审批和可追溯报告。

### Durable product constraints

- 回答、生成内容、变更建议和诊断必须区分事实、推断、候选与已验证结果。
- 实质性结论和生成产物应尽可能关联可解析来源、不可变 revision、运行证据或验证结果。
- Agent 或模型输出不能替代确定性门禁；高影响变更保持可评审、可审批和可追溯。
- 权限由服务端可信上下文决定；客户端只能收窄范围，不能扩大访问能力。
- 产品能力以模块化边界扩展，当前测试核心不能把未来生命周期能力硬编码成不可演进的导航结构。

### Current implementation boundary

当前仓库实现仍是较窄的 Athena 本地知识纵向切片：文档上传、六阶段 ingestion、ready 来源选择、单次 grounded answer、逐条引用、失败重试和删除。Conversation/history、SSE、权限、测试用例生成、脚本生成和执行等属于产品目标或后续阶段，设计与文案不得把它们描述成已经交付。

## Brand Commitments

- 当前用户界面名称为 `Athena`。
- 语气应专业、清晰、可信，避免把不确定的模型输出包装成确定事实。
- `Athena` 与 `TAP` 的长期品牌层级关系尚未最终确认；在确认前不得擅自重命名产品或虚构品牌资产。

## Evidence on Hand

- 平台目标、原则和当前实现边界：[`../../README.md`](../../README.md)
- 当前产品壳层、Low Code Automation 与 Athena 编排原型：[`../../docs/proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md`](../../docs/proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md)
- 软件生命周期与阶段路线图：[`../../docs/plans/2026-08-20-roadmap.md`](../../docs/plans/2026-08-20-roadmap.md)
- Knowledge Chat 目标交互：[`../../docs/architecture/2026-08-21-knowledge-chat-ui.md`](../../docs/architecture/2026-08-21-knowledge-chat-ui.md)
- 总体平台边界：[`../../docs/architecture/2026-08-20-overview.md`](../../docs/architecture/2026-08-20-overview.md)
- 当前 Athena Web 实现与测试：[`src/pages/AthenaPage.tsx`](src/pages/AthenaPage.tsx)、[`src/widgets/athena/AthenaWorkspace.tsx`](src/widgets/athena/AthenaWorkspace.tsx)、[`tests/e2e/athena.spec.ts`](tests/e2e/athena.spec.ts)
- 本轮 A/B/C 概念原型保存在仓库忽略的 `.superpowers/brainstorm/` 目录，仅作为设计探索证据。
- 当前没有已确认的正式 Logo、完整品牌资产、用户研究样本、客户背书或可公开产品指标；后续设计不得虚构这些内容。

## Product Principles

1. **证据优先于流畅表达**：让用户看见结论从哪里来，以及它是否经过验证。
2. **贯通而不是堆叠工具**：需求、用例、脚本、执行和结果属于同一条工作流，而不是彼此孤立的页面。
3. **共享核心，角色适配**：对象和状态保持一致，入口与信息深度根据角色、任务和权限变化。
4. **候选与权威事实分离**：生成和诊断可以智能化，发布、执行门禁与高影响变更必须可控制。
5. **为未来生命周期扩展留出边界**：以稳定对象和模块能力演进，不把产品永久限制在当前页面或当前测试阶段。

## Accessibility & Inclusion

Athena 应支持键盘操作、清晰焦点、屏幕阅读器语义、非颜色状态表达、触控可达性和响应式 Web 使用。当前实现已有这些基础行为，重设计不得回退。正式采用的可访问性合规级别仍是开放决策。
