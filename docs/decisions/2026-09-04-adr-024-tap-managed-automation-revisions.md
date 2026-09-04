---
id: ADR-024
status: accepted
date: 2026-09-04
supersedes:
  - ADR-001
  - ADR-004
  - ADR-008
superseded-by: []
related-rfcs:
  - RFC-009
---

# ADR-024：Automation Revision 由 TAP 管理

## 背景

ADR-001 和 ADR-004 将 Test IR 与统一 Evidence 设为平台核心，同时要求 Git 成为所有测试内容版本源。ADR-008 又确立模型只负责理解、规划、归纳、失败分析和建议，执行与发布判断保持确定性，但把自愈候选的最终去向固定为 Git。LCA 的目标用户需要在平台内用自然语言、BDD、手工动作或录制完成编辑、审查与发布；强制理解仓库、分支和提交会提高门槛，也会使 Test Plan、BDD、Test IR 与平台运行记录形成双重事实源。

## 决策

MySQL 是 Test Plan、Test Case、BDD、Test IR、Automation、Revision、严格可选 1:1 关联、发布状态、Run、Step Result、审批和审计的权威业务记录。Draft 经确定性 Schema/语义验证和人工发布后形成不可变 Revision；Published Automation Revision 固化 canonical Test IR、生成器版本、Playwright TypeScript Bundle digest、关联与步骤映射。

MinIO 保存内容寻址的源 Bundle、生成代码与 Evidence；MySQL 保存 digest、manifest、状态和 lineage，并通过 staging → digest verify → manifest 晋级及 Reconciler 收敛。Playwright 代码是从 canonical Test IR 生成的可验证制品，不是可绕过 Test IR 的第二写入口。

Git 改为可选导出/同步 Adapter，不参与当前发布或 Run 的必要路径。Test IR 作为稳定执行语义、统一 Evidence Manifest、BDD Step → Test IR Action → generated code → Step Result 的可追溯链继续是平台核心。

确定性门禁与 Agent 建议继续分离：模型可负责理解、规划、归纳、失败分析和建议，Recorder、AI 与自愈只能产生 Draft 或 Change Proposal；Schema/语义验证、测试执行、断言、重试、权限判断和发布判断必须保持确定性。Agent Finding 必须携带来源、置信度与 Evidence 引用，不能覆盖原始结果；获批候选进入 TAP 管理的 Draft/Revision 发布路径，仅在显式配置时再同步到 Git。

## 考虑过的方案

- **Git 继续作为强制事实源**：开发者审查体验成熟，但不适合 LCA 用户，且平台编辑需要复杂双向同步。
- **只保存生成的 Playwright 代码**：实现简单，却失去稳定动作模型、结构化编辑和跨版本语义 diff。
- **MySQL 与 Git 双主写入**：容易出现无法判定权威版本的冲突和不完整事务。

## 后果

- 每次正式 Run 必须固定 Published Revision 与 Bundle digest，不能运行可变 Draft 或在提交时临时重新生成代码。
- Recorder、AI 和手工编辑只能产生 Draft/Change Proposal，并通过同一发布路径形成权威版本。
- 取消 Git-required 不得弱化确定性验证、人工审批或 Agent Finding 的 provenance/evidence 门禁。
- Git 导出/同步必须拥有显式单向/冲突语义，不能静默回写并覆盖平台 Revision。
- MySQL、MinIO 和 Jenkins 之间不宣称分布式事务；幂等键、manifest、lease/fencing 和 Reconciler 负责最终收敛。
