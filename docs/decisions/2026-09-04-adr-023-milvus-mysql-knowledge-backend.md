---
id: ADR-023
status: accepted
date: 2026-09-04
supersedes:
  - ADR-005
  - ADR-012
superseded-by: []
related-rfcs:
  - RFC-009
---

# ADR-023：知识后端采用 Milvus 文档投影与 MySQL Knowledge Graph

## 背景

早期架构把四个 Azure AI Search 索引作为企业知识后端，并由 TAP 管理切片与溯源。仓库随后已完成 Milvus `doc` 投影的固定版本实验和本地知识问答纵向切片；当前路线只要求文档知识、引用与 Knowledge Graph，不需要同时建设 code、BDD、failure 四类物理索引，也不希望引入新的图数据库。

## 决策

TAP 继续负责文档解析、typed chunking、稳定逻辑身份、不可变 revision/chunk、Embedding、ACL/Project filter、provenance、删除传播、发布和重建。Milvus 只保存可重建的 `doc` 混合检索投影；MySQL 保存 Knowledge Graph 的 Node、Edge、Snapshot、Evidence、Provenance 和发布状态。对象存储保存原件与标准化派生物。

在线问答通过 provider-neutral `SearchPort` 先做 Project/Source 范围内的 Milvus hybrid search，再仅对已授权 active Graph Snapshot 做有界图扩展。Graph 抽取结果先进入不可变候选 Snapshot，Evidence 可解析且确定性校验通过后才原子发布；`INFERRED` 关系必须显式标记，不能冒充来源事实。

Milvus 与 Graph 都不是业务权限或原件事实源。Milvus 投影可由 MySQL/对象存储账本重建，Graph 查询始终带 `project_id` 和 snapshot 边界。Azure AI Search Adapter 和四索引设计只作为历史/provider-specific 参考保留，不再是当前或生产必选基线。

## 考虑过的方案

- **继续使用 Azure AI Search 四索引**：适合更广的企业知识类型，但当前范围和自托管成本不匹配。
- **引入 Neo4j 等专用图数据库**：图查询能力更强，但首期只需证据化的有界探索，增加独立运维不划算。
- **把 Graph 也存入 Milvus**：会把关系事实、版本和证据生命周期放进可重建检索投影，职责不清。

## 后果

- Milvus schema、alias 发布、认证/TLS、备份/重建、容量与召回质量必须有独立门禁。
- MySQL Graph schema 需要显式 Snapshot 和 Evidence 外键，并限制遍历深度、节点数和关系类型。
- 现有 parser/chunker/provenance 与 Milvus Adapter 可以演进复用，但本地实验通过不等于生产部署通过。
- 若未来增加 code/BDD/failure 检索或专用图数据库，必须通过新 ADR 保持 `SearchPort` 与证据契约稳定。
