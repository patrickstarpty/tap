# TAP 文档治理规范

本文是 TAP 文档信息架构的规范性来源。创建、移动或实质修改 `docs/` 内容前，必须遵循本规范。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `docs/architecture/` | 当前规范性的系统架构与领域设计。 |
| `docs/proposals/` | RFC、待评审设计和未决输入。 |
| `docs/decisions/` | 一项决策一份 ADR，以及被覆盖方案记录。 |
| `docs/plans/` | 实施、交付和路线图计划。 |
| `docs/reviews/` | 时间点审查、评估和评分结果。 |
| `docs/reference/` | 契约、术语、来源和治理规范。 |

目录按稳定职责组织，不按生命周期状态组织。状态变化不会移动文件。

## 文件名与日期

除索引和可复用模板外，所有 Markdown 内容文件使用 `YYYY-MM-DD-<lower-kebab-case>.md`。索引和模板不使用日期前缀：索引固定为 `index.md`，模板固定为 `rfc-template.md` 或 `adr-template.md`。

日期表示文档最初形成或所记录事件发生的日期，并按以下优先级确定：

1. 正文明确记录的创建日期；ADR 使用决策日期，Review 使用评审日期。
2. 文件首次加入 Git 的日期。
3. 新文档的创建日期。

后续更新不改变日期前缀；RFC 和 ADR 的编号也是稳定身份，不因移动、状态变化或替代关系重新编号。

## 生命周期与元数据

RFC、ADR 和 Plan 必须使用 YAML front matter 表达各自的最小生命周期元数据。

### RFC

RFC 状态机为 `draft → in-review → accepted → implemented`，并允许 `in-review → rejected`。`implemented` 和 `rejected` 是终态。RFC 使用 `id`、`status`、`date` 和 `related-adrs`。

### ADR

ADR 状态机为 `proposed → accepted → superseded`。ADR 使用 `id`、`status`、`date`、`supersedes`、`superseded-by` 和 `related-rfcs`。一份 ADR 只表达一个决策。

已接受 ADR 的决策语义不可改写；仅允许原地修正错别字、失效链接等非语义问题。方向变化时必须新建 ADR：新 ADR 的 `supersedes` 列出被替代 ADR，旧 ADR 的 `superseded-by` 列出新 ADR，两个方向都必须更新。

### Plan

Plan 状态机为 `planned → active → completed`。Plan 使用 `status` 和 `date`。计划完成后仍保留原路径，只更新状态。

## 索引与链接

每个一级目录及存在子文档的领域目录都必须有 `index.md`。索引说明本目录范围，并且只列出直属子项；索引是导航入口，不承担生命周期。

仓库内链接必须使用相对链接。移动或重命名任何文档后，必须修复所有受影响的仓库内相对链接和标题锚点；不得保留旧路径兼容页、符号链接或重复正文来规避链接修复。

## 模板使用

新建 RFC 必须从 `docs/proposals/rfc-template.md` 开始；新建 ADR 必须从 `docs/decisions/adr-template.md` 开始。模板定义必填 front matter 与正文结构，具体记录必须替换其中的可复用标记。

## 规则发现

文档治理的发现链路固定如下：

```text
根 AGENTS.md
    → docs/reference/2026-08-22-document-governance.md
        → docs/proposals/rfc-template.md
        → docs/decisions/adr-template.md
```

根 `AGENTS.md` 是仓库级入口；本治理规范是规则的规范性来源；模板提供 RFC 和 ADR 的可复用结构。
