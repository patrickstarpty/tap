# 待确认项

> **范围更新（2026-09-04）**：[RFC-009](2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md) 与 [ADR-021](../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 已确认 Validation-first V0–VG → P0 身份/RBAC/多 Project → P1 生产加固、Knowledge-first、Compose/MinIO/LiteLLM/外置 Jenkins、TAP 管理 Automation Revision、Web-only/Jenkins-first。下列旧问题仅在与该基线兼容时继续开放；Mobile、Azure DevOps、Git 强制事实源、AKS/Azure AI Search 首期部署不再是当前待确认项。

1. 产品负责人和首批使用团队；英文全称已确认为 **Test Automation Platform**。
2. V0–VG 各验证门禁的代表性数据、目标 Web 场景、成功阈值和产品签字人分别是什么？
3. 已固定 Playwright + TypeScript 后，Test IR v1 为首批代表性 Web 场景必须覆盖哪些标准 Action 与受治理 Fixture？
4. P1 后若启用可选 Git Sync，采用每 Project 独立仓库、单一资产仓库还是业务代码同仓？
5. TAP-managed Draft Test Plan/Automation 的 Publish Approval 策略、审批角色和权限范围是什么？AI 只能创建或提议 Draft，不能绕过人工发布形成不可变 Revision。
6. P1 后若评估 BrowserStack，是否允许访问；若允许，数据区域、Local Tunnel、并发和预算是什么？
7. P1 客户环境的 TLS、Secret Store、模型数据区域、日志与证据保留标准。
8. 自托管 MySQL/Redis/Milvus/MinIO 的版本、主机容量、备份介质与恢复责任人。
9. 外置 Jenkins Controller/Job、Agent Label、Runner Image、Credential Binding Profile 与回调密钥轮换/撤销配置。
10. 质量门禁、RPO/RTO、结果收敛延迟、单 Run 成本等目标值需基线测量后审批。
11. LiteLLM 是否只采用无状态 Gateway；若需要其 Virtual Keys/预算/Admin 持久化能力，必须先验证 MySQL/Redis 兼容性，不能静默新增 PostgreSQL。
12. 当前 React/Vite 产品壳层采用组织现有 Design System 的哪些 token/component；选择不得改变 REST/SSE、Scope/Policy 与 Citation 安全契约。
13. LiteLLM 后的受治理模型 provider、模型 alias、数据区域、保留、预算和并发由谁批准？
14. LiteLLM 当前部署是否完整兼容回答与生成所需的 streaming、structured output、取消和用量语义；不兼容能力采用哪一种受治理 Adapter 或降级策略？
