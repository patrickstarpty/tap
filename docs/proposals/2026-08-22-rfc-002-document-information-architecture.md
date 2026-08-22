---
id: RFC-002
status: in-review
date: 2026-08-22
related-adrs: []
---

# RFC-002：TAP 文档信息架构

## 摘要

将当前扁平的 `docs/` 重组为按稳定文档职责划分的六类目录，并用文档自身的元数据表达生命周期。迁移只移动或拆分 `docs/` 中的现有内容，但会在全仓库修复受影响的引用；旧路径直接删除，不保留兼容文件。本 RFC 不引入文档站、CI 或新的运行时依赖。

## 背景

仓库目前有 12 份顶层文档，架构、设计、决策、计划、评审、契约和来源记录混放在 `docs/`。其中 `architecture.md` 和 `contracts.md` 已接近千行，后续继续增加复杂设计与实施计划时，单层目录会降低可发现性，并混淆“当前有效架构”“待评审提案”和“历史决策”。

行业方法解决的是不同维度的问题：Diátaxis 区分读者需求，arc42 组织架构内容，C4 表达架构视图，ADR/RFC/KEP 管理提案与决策生命周期。它们不支持把所有内容按 `draft/accepted/archive` 物理搬动。状态变化应更新元数据和索引，而不是改变稳定路径。

## 目标

- 为当前架构、提案、决策、计划、评审和参考资料建立清晰边界。
- 为所有内容文档采用 `YYYY-MM-DD-<topic>.md` 命名，保留原始创建或决策日期。
- 将 RFC、ADR 和 Plan 的状态机、编号与不可变规则固化为仓库规范。
- 为每级目录提供可扫描索引，避免孤儿文档。
- 一次性更新仓库中的旧路径引用，不维护双重路径或重复内容。
- 让 Codex 在处理文档前能稳定发现仓库特有的治理规则。

## 非目标

- 不重写现有架构结论、契约或路线图内容。
- 不引入 MkDocs、Docusaurus、文档发布站点或 CI 工作流。
- 不修改或派生 vendored Superpowers 技能。
- 不迁移 `docs/` 之外的文件；外部文件仅做必要的链接修复，以及在根 `AGENTS.md` 增加已批准的治理入口。
- 不保留旧路径兼容页、符号链接或内容副本。

## 信息架构

```text
docs/
├── index.md
├── architecture/   # 当前规范性的系统架构与领域设计
├── proposals/      # RFC、设计提案和未决输入
├── decisions/      # 一项决策一份 ADR
├── plans/          # 实施与交付计划
├── reviews/        # 时间点审查和评估结果
└── reference/      # 契约、术语、来源和治理规范
```

`architecture/` 可按稳定领域继续分层；首个领域子目录为 `architecture/rag/`。每个一级目录及存在子文档的领域目录都有 `index.md`，索引说明范围并只列出直属子项。索引是导航入口，不承担生命周期。

### 目录职责

| 目录 | 包含 | 不包含 |
| --- | --- | --- |
| `architecture/` | 当前规范性架构、领域设计、交互与运行视图 | 尚未采纳的候选方案、时间点评审 |
| `proposals/` | RFC、待评审设计、未决输入 | 已接受决策的唯一记录 |
| `decisions/` | 独立 ADR、被覆盖方案记录 | 大篇幅实施设计、开放问题清单 |
| `plans/` | 路线图、实施和交付计划 | 稳定架构事实 |
| `reviews/` | 带日期的审查、评估和评分 | 持续更新的规范文档 |
| `reference/` | 契约、来源、术语和文档治理规则 | 方案讨论和交付跟踪 |

`architecture/` 表示文档当前作为仓库架构基线使用，不等于其内容已经完成正式批准。迁移必须保留原文的成熟度标记；例如总体架构中的“待正式评审”不会因目录变化而升级状态。

## 命名与日期

除索引和可复用模板外，Markdown 内容文件统一命名为 `YYYY-MM-DD-<lower-kebab-case>.md`。日期代表文档最初形成或所记录事件发生的日期，按以下优先级确定：

1. 正文中明确记录的创建日期；ADR 使用决策日期，Review 使用评审日期。
2. 文件首次加入 Git 的日期。
3. 新文档的创建日期。

“更新时间”不作为最初形成日期；后续修改也不改变文件名前缀。`index.md`、`rfc-template.md` 和 `adr-template.md` 使用稳定文件名；仓库根 `README.md`、`AGENTS.md` 等控制文件不采用日期前缀。

RFC 和 ADR 编号为稳定身份，不因移动、状态变化或替代关系重新编号。现有 `ADR-001` 至 `ADR-015` 全部保留；现有 Codex Runtime 设计使用 `RFC-001`，本信息架构设计使用 `RFC-002`。实施本 RFC 时新增 `ADR-016` 记录采用该信息架构的决定。

## 生命周期与元数据

仅对具有生命周期的 RFC、ADR 和 Plan 强制最小 YAML front matter。架构、评审和参考文档不套用通用状态机；它们可以在正文中保留自身的成熟度或审查结论。

### RFC

```yaml
---
id: RFC-001
status: in-review
date: 2026-08-21
related-adrs:
  - ADR-014
---
```

允许的状态流为 `draft → in-review → accepted → implemented`，也允许 `in-review → rejected`。`accepted` 表示方案获准，不等于已经实现；`implemented` 只在迁移或功能实际完成后使用。`implemented` 和 `rejected` 是终态。

RFC 模板包含摘要、背景、目标、非目标、方案、替代方案、风险与缓解、迁移或发布方式、验收标准和未决问题。进入 `accepted` 前不得保留未决问题。

### ADR

```yaml
---
id: ADR-014
status: proposed
date: 2026-08-21
supersedes: []
superseded-by: []
related-rfcs:
  - RFC-001
---
```

允许的状态流为 `proposed → accepted → superseded`。一份 ADR 只表达一个决策，正文包含背景、决策、考虑过的方案和后果。已接受 ADR 的决策语义不可改写；方向变化时新建 ADR，并双向记录替代关系。错别字、失效链接等非语义修正可以原地完成。

现有状态映射如下：

- `ADR-001`—`ADR-008`、`ADR-011`—`ADR-013`、`ADR-015` 映射为 `accepted`。
- `ADR-009`、`ADR-010`、`ADR-014` 映射为 `proposed`。
- `ADR-014` 与 `RFC-001` 双向关联。
- 本 RFC 获接受后创建 `ADR-016`，初始状态为 `accepted`，并与 `RFC-002` 双向关联。

### Plan

Plan 使用 `planned → active → completed`，最小元数据为 `status` 和 `date`。当前路线图映射为 `active`。计划完成后保留原位并更新状态，不移动到归档目录。

## 现有文档迁移

| 当前路径 | 目标路径 |
| --- | --- |
| `docs/architecture.md` | `docs/architecture/2026-08-20-overview.md` |
| `docs/rag-phase-1.md` | `docs/architecture/rag/2026-08-21-foundation.md` |
| `docs/chunking-and-provenance.md` | `docs/architecture/rag/2026-08-21-chunking-and-provenance.md` |
| `docs/ai-search-index-design.md` | `docs/architecture/rag/2026-08-21-ai-search-index.md` |
| `docs/retrieval-tuning.md` | `docs/architecture/rag/2026-08-21-retrieval-tuning.md` |
| `docs/knowledge-chat-ui.md` | `docs/architecture/2026-08-21-knowledge-chat-ui.md` |
| `docs/codex-agent-runtime.md` | `docs/proposals/2026-08-21-rfc-001-codex-agent-runtime.md` |
| `docs/roadmap.md` | `docs/plans/2026-08-20-roadmap.md` |
| `docs/architecture-review.md` | `docs/reviews/2026-08-21-architecture-review.md` |
| `docs/contracts.md` | `docs/reference/2026-08-20-contracts.md` |
| `docs/source-notes.md` | `docs/reference/2026-08-20-source-notes.md` |

`docs/decisions.md` 拆分如下，不丢弃原始段落：

| 内容 | 目标路径 |
| --- | --- |
| 文档标题、导言及“已确认的方向”“本次整理补充的工程基线”分组 | `docs/decisions/index.md` |
| ADR-001 | `docs/decisions/2026-08-20-adr-001-platform-core-test-ir-git-evidence.md` |
| ADR-002 | `docs/decisions/2026-08-20-adr-002-azure-enterprise-deployment-baseline.md` |
| ADR-003 | `docs/decisions/2026-08-20-adr-003-dag-and-agentic-loop.md` |
| ADR-004 | `docs/decisions/2026-08-20-adr-004-storage-responsibility-boundaries.md` |
| ADR-005 | `docs/decisions/2026-08-20-adr-005-four-azure-ai-search-indexes.md` |
| ADR-006 | `docs/decisions/2026-08-20-adr-006-self-hosted-execution-grid.md` |
| ADR-007 | `docs/decisions/2026-08-20-adr-007-deepseek-harness-not-core-runtime.md` |
| ADR-008 | `docs/decisions/2026-08-20-adr-008-deterministic-gates-and-agent-advice.md` |
| ADR-009 | `docs/decisions/2026-08-20-adr-009-mysql-outbox-redis-delivery.md` |
| ADR-010 | `docs/decisions/2026-08-20-adr-010-modular-control-plane-independent-workers.md` |
| ADR-011 | `docs/decisions/2026-08-20-adr-011-phase-1-rag-foundation.md` |
| ADR-012 | `docs/decisions/2026-08-21-adr-012-tap-managed-chunking-and-provenance.md` |
| ADR-013 | `docs/decisions/2026-08-21-adr-013-phase-1-knowledge-chat.md` |
| ADR-014 | `docs/decisions/2026-08-21-adr-014-codex-specialist-runtime.md` |
| ADR-015 | `docs/decisions/2026-08-21-adr-015-react-typescript-python-fastapi.md` |
| 被后续讨论覆盖的旧方案 | `docs/decisions/2026-08-20-superseded-options.md` |
| 待确认项 | `docs/proposals/2026-08-20-open-questions.md` |

`docs/decisions/index.md` 保留原文标题和导言，并在原有两个分组标题下链接对应 ADR，以保存分类语义；ADR 的规范状态仍以各文件 front matter 为准。

新增的治理文件为：

- `docs/reference/2026-08-22-document-governance.md`
- `docs/proposals/rfc-template.md`
- `docs/decisions/adr-template.md`
- `docs/decisions/2026-08-22-adr-016-adopt-document-information-architecture.md`
- `docs/index.md` 以及每个一级目录和 `architecture/rag/` 的 `index.md`

## 规则发现

`docs/reference/2026-08-22-document-governance.md` 是规范性来源，模板体现必填结构，索引服务于人工导航。根 `AGENTS.md` 增加简短路由规则，要求在创建、移动或实质修改 `docs/` 内容前阅读治理规范，并使用 RFC/ADR 模板。

不使用 `docs/AGENTS.md` 作为唯一入口。Codex 从项目根目录向启动时的当前工作目录构建指令链；从仓库根启动时，嵌套的 `docs/AGENTS.md` 不保证被加载。也不新增文档治理 skill，因为 skill 触发依赖任务匹配，而根 `AGENTS.md` 是更确定的仓库级入口。

发现链路为：

```text
根 AGENTS.md
    → docs/reference/2026-08-22-document-governance.md
        → docs/proposals/rfc-template.md
        → docs/decisions/adr-template.md
```

## 迁移过程

1. 接受本 RFC 时先把 `RFC-002` 从 `in-review` 更新为 `accepted`。
2. 盘点所有指向当前 `docs/*.md` 的文件链接和标题锚点。
3. 创建目录、索引、模板和治理规范；创建状态为 `accepted` 的 `ADR-016`，并更新它与 `RFC-002` 的双向关联。
4. 使用 Git rename 迁移未拆分文档；拆分 `decisions.md` 并逐项保存原内容。
5. 先修复 `docs/` 内部链接，再修复全仓库 README、AGENTS 和其他文件中的旧路径引用。
6. 删除旧路径，不创建兼容页、符号链接或重复正文。
7. 校验文件、元数据、索引、链接、锚点、Mermaid 和 Git diff。
8. 迁移通过全部验收后，把 `RFC-002` 从 `accepted` 更新为 `implemented`。

若一个旧锚点在拆分后有多个可能目标，迁移必须根据原段落语义选择唯一目标；不能删除链接、指向目录首页或保留已知失效引用来绕过问题。外部 HTTP 链接不因本次目录迁移改写。

## 验收标准

- `docs/` 根目录只保留 `index.md` 和六个一级分类目录。
- 除索引与模板外，每个 Markdown 内容文件都以合法 `YYYY-MM-DD-` 开头。
- 每个分类和领域目录都有索引，每份内容文档都能从上级索引到达。
- RFC、ADR 和 Plan 的必填元数据存在，ID 唯一，状态属于允许集合。
- 原有 12 份文档的正文内容完整保留；`decisions.md` 的标题、导言、两个分类标题、15 项 ADR、旧方案和待确认项都有唯一目标。
- 全仓库不再引用旧文档路径，也不存在旧路径文件。
- 所有仓库内相对文件链接和 Markdown 标题锚点均能解析。
- 变更后的 Markdown 表格与 Mermaid 图可以正常渲染。
- `git diff --check` 通过，Git 能识别未拆分文档的 rename 历史。
- 除必要链接更新和根 `AGENTS.md` 治理入口外，`docs/` 外没有内容性修改。

## 替代方案

### 保持扁平目录，仅增加日期前缀

改动最少，但不能区分规范架构、提案、决策和计划；文档增长后仍需二次迁移，因此拒绝。

### 使用生命周期作为物理目录

将文档放入 `draft/accepted/archive` 会在状态变化时改变路径，产生链接破坏和 Git 噪声，也不符合读者按主题寻找内容的方式，因此拒绝。

### 同时引入文档站与完整 CI

自动导航、schema 和链接检查有长期价值，但当前仓库没有构建工具或 CI。先建立信息架构和治理契约，等内容规模与实现工具确定后再独立决策，避免扩大本次迁移范围。

## 风险与缓解

| 风险 | 缓解措施 |
| --- | --- |
| 路径迁移造成断链 | 迁移前盘点、全仓库重写、文件和锚点双重校验 |
| 拆分 `decisions.md` 遗失上下文 | 使用明确映射表，逐段迁移，并对标题、导言、两个分类标题、15 个 ID、旧方案和待确认项做完整性核对 |
| 元数据和索引后续漂移 | 根 `AGENTS.md` 路由到规范与模板；每次新增文档同时更新直属索引 |
| 治理规则过重 | 只强制三类生命周期元数据，不引入站点、schema 工具或新依赖 |
| Superpowers 升级覆盖本项目规则 | 不修改 vendored skill；项目规则独立保存在 `docs/reference/` 并由 `AGENTS.md` 引用 |

## 参考实践

- [Diátaxis](https://diataxis.fr/start-here/)
- [arc42 模板概览](https://arc42.org/overview/)
- [C4 与 arc42 的对应关系](https://c4model.com/faq)
- [Azure Architecture Decision Record 指南](https://learn.microsoft.com/en-us/azure/well-architected/architect-role/architecture-decision-record)
- [Kubernetes Enhancement Proposal 流程](https://github.com/kubernetes/enhancements/blob/master/keps/sig-architecture/0000-kep-process/README.md)
- [GitLab 文档目录规范](https://docs.gitlab.com/development/documentation/site_architecture/folder_structure/)
- [OpenAI Codex 的 AGENTS.md 指令发现](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
