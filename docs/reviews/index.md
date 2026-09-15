# 评审索引

本目录保存时间点审查、评估和评分结果。

- [整体架构评审](2026-08-21-architecture-review.md)：记录评审结论、优先级问题、整改建议与分阶段决策门禁。
- [Milvus 本地检索实验评审](2026-08-27-milvus-local-search-experiment.md)：记录本地真实数据库、ACL、重启、空卷重建与 embedding 预算证据，以及不改变既有生产决策的实验结论。
- [Tapper 本地知识 Demo 验收评审](2026-08-27-tapper-local-knowledge-demo.md)：汇总本地中间件、四格式浏览器路径、故障恢复、持久化、视觉/键盘与可选真实模型证据；mandatory local gate 与实际手工验收结论为 `approved / GREEN`。
- [Low Code Automation 交互原型评审](2026-09-03-low-code-automation-prototype-review.md)：记录调整前的结构性问题，以及同日完成的 Automation 资产、BDD/动作映射、执行配置、Test Plan 关联、Tapper 编排与 Conversation 恢复复核。
- [Tapper 知识与 Web 自动化平台设计基线评审](2026-09-05-tapper-platform-design-baseline-review.md)：确认 RFC-009、当前架构、核心契约与 55 项实施计划已经收口，可从 V0 开始方案验证；该结论不代表功能已实现或已达到生产就绪。
- [Tapper 品牌与运行命名空间迁移评审](2026-09-05-tapper-brand-migration-review.md)：`pass`；记录零残留守卫、全量构建测试、隔离 Demo E2E、桌面/移动及真实 reduced-motion 浏览器验收、40 张截图与旧资源非删除证据。

- [TAP 原型浅色改造与 V0 启动验收](2026-09-05-tap-fwd-and-v0-start-review.md)：记录原型 FWD 浅色改造、40 张截图、V0 Task 1、隔离数据库门禁与回归环境修正。
- [Tapper V0 固定验证身份验收](2026-09-05-tapper-v0-identity-review.md)：Task 2A 通过；记录固定 Scope、共同授权、0006 迁移、隔离全量回归及测试隔离复审。
- [Tapper V0 Project 数据隔离验收](2026-09-05-tapper-v0-project-scope-review.md)：Task 2B 实现与定向验收通过；记录 0007 多批次迁移、仓储隔离、两项审查修正及完整回归中一项待复核失败。
- [Tapper V0 事件与错误契约验收](2026-09-06-tapper-v0-contracts-review.md)：Task 2C 通过；记录统一事件/Problem、同事务 Outbox、异常时间戳隔离与完整回归旧断言的修正证据。
- [Tapper V0 Project 接口与验证模式验收](2026-09-06-tapper-v0-http-review.md)：Task 3 通过；记录可信 HTTP/Origin、Project 缓存、原型验证提示、40 张截图与完整回归旧代理断言的修正。
- [Tapper V0 Project Audit 账本验收](2026-09-06-tapper-v0-audit-review.md)：Task 3A 通过；记录闭集审计、同事务三写、0008 迁移、并发重放与冻结后的完整隔离回归。

- [Tapper V0 恢复与有界运维验收](2026-09-06-tapper-v0-recovery-review.md)：Task 4 通过；记录 Redis/Outbox 恢复、Operator 完成三写、0009、范围清理、独立审查及完整回归的实际限制。

- [Tapper V0 对象存储与真实上传验收](2026-09-06-tapper-v0-object-storage-review.md)：Task 5 通过；记录独立 MinIO、旧 Azure 兼容、当前原型真实上传、重启验证、取消修正与回归限制。

- [Tapper V0 文档解析隔离验收](2026-09-06-tapper-v0-parser-isolation-review.md)：Task 5A 通过；记录真实容器隔离、上传边界、冷恢复与原子记录修正、两轮复审及回归时序限制。

- [V0 Validation Scope 与可靠性门禁](2026-09-06-v0-validation-scope-reliability-gate.md)：Task 5B 与 V0 通过；保留首轮失败、清理/权限修正与复审，记录最终 296 项必需验证、原生证据和源码一致性。

- [Tapper V1 知识来源账本验收](2026-09-06-tapper-v1-source-ledger-review.md)：Task6通过；记录0010/25表、事务Audit/Outbox、查询脱敏、两轮修正和最终Backend2880/Web294验证，V1质量出口仍待后续。

- [Tapper V1 Source API 与 projection 验收](2026-09-08-tapper-v1-source-api-and-projection-review.md)：Task6A通过；记录0010a/26表、Source API/Picker、canonical Milvus v2、显式v1回滚、五份审查和最终隔离回归。
- [Tapper V1 统一 ModelGateway 与模型目录验收](2026-09-08-tapper-v1-model-gateway-review.md)：Task 7 通过；记录唯一 LiteLLM Gateway、Project 模型目录、legacy Codex 隔离、一轮审查修正和完整隔离回归。
- [Tapper V1 AI Agent 与 Skill Revision 验收](2026-09-09-tapper-v1-ai-agent-skill-catalog-review.md)：Task 7A 通过；记录不可变 Catalog Revision、并发幂等 Validation seed、只读 API、选择器恢复态、四轮审查修正与隔离回归。
- [Tapper V1 Conversation 持久化与可恢复流验收](2026-09-09-tapper-v1-conversation-streaming-review.md)：Task 8 通过；记录不可变双快照、generation worker、lease/fencing、Evidence、可恢复 SSE、0012a 兼容迁移与五轮审查修正。
- [Tapper V1 真实 Conversation Web 接线验收](2026-09-09-tapper-v1-durable-conversation-web-review.md)：Task 9 通过；记录默认 API 模式、历史 Context/Citation、可恢复多轮 SSE、页面级重启旅程、两轮审查修正和完整隔离回归。
- [V1 可信知识质量门禁评审](2026-09-09-v1-trusted-knowledge-gate.md)：`pass`；100 条人工复核 case、11 份合成语料与批准的百炼实际路由通过全部硬阈值、zero-skip、真实 Milvus、隔离 E2E 和完整回归，V2 已放行。
- [V2 Grounded Knowledge Graph 门禁评审](2026-09-13-v2-knowledge-graph-gate.md)：历史评审；原 `pass` 已被后续更正撤销。
- [V3 AI 测试设计门禁评审](2026-09-14-v3-ai-test-design-gate.md)：历史评审；原 `pass` 已被后续更正撤销。
- [V2/V3 门禁更正评审](2026-09-14-v2-v3-gate-correction.md)：V2/V3 `gate-reopened`；记录多 Revision Graph、真实质量复核和完整 Web 评审闭环缺口，V4 暂不放行。
- [IBM Bob COBOL 采购与 Tapper 能力评估](2026-09-15-ibm-bob-cobol-procurement-assessment.md)：基于 IBM 官方资料及有限外部证据，评估 Bob Premium Package for Z、采购试点、风险、成本与 Tapper 选择性借鉴路径。
