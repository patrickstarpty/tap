# 文档精简与 V1 总纲设计

日期：2026-09-29。子项目 0，输入来源：[架构与实施评审及 V1 范围收敛](../../archive/reviews/2026-09-29-architecture-review-and-v1-scope.md)。

## 目标

1. 把 RFC/ADR/Plan 三套状态机与六类目录的文档体系，收敛为 superpowers 的 `specs/` + `plans/`，历史文档冻结归档。
2. 产出唯一的 V1 总纲计划，定义 5 项能力的验收标准与子项目顺序。

本子项目只改文档与文档链接，不改产品代码、脚本或部署配置。

## 目标目录结构

```text
docs/
├── index.md            # 极简入口
├── architecture.md     # 单页现状架构，随代码更新
├── superpowers/
│   ├── specs/          # 设计：YYYY-MM-DD-<topic>-design.md
│   └── plans/          # 实施计划：YYYY-MM-DD-<topic>.md
├── decisions/          # 现有 ADR 原地保留；新 ADR 仅用于跨模块决策，无状态机
├── guides/             # 仍有效的操作类文档
├── assets/             # 原地不动
└── archive/            # 冻结，不再维护
    ├── index.md
    ├── architecture/
    ├── proposals/
    ├── plans/
    ├── reviews/
    └── reference/
```

## 迁移规则

| 来源 | 去向 |
| --- | --- |
| `docs/proposals/**`、`docs/plans/**`、`docs/reviews/**`、`docs/architecture/**`（含 drawio/svg 与 `rag/`） | `docs/archive/<同名子目录>/`，保持相对结构 |
| `docs/reference/` 中：`2026-09-22-product-prototype-baseline.md`、`2026-09-27-frontend-developer-onboarding.md`、`2026-09-27-backend-developer-onboarding.md`、`2026-09-13-tapper-developer-guide.md`、`2026-09-05-tap-light-design.md`、`2026-09-06-file-type-icons.md`、`2026-09-04-customer-prototype-demo-guide.md`、`2026-09-27-knowledge-review-workbench.md` | `docs/guides/`，文件名不变 |
| `docs/reference/` 其余文件（治理规范、契约、source notes、RFC-011 设计稿、`index.md`） | `docs/archive/reference/` |
| `docs/decisions/adr-template.md`、`docs/decisions/index.md` | 原地保留；`index.md` 顶部注明不再维护生命周期状态 |
| `docs/assets/**` | 不动 |

一律使用 `git mv`，保留历史。

## 链接处理

- 目录整体平移后，归档文档之间的相对链接保持有效。
- 归档文档中指向 `assets/`、`decisions/`、`guides/` 的链接，以及 `guides/`、`decisions/` 中指向已归档文档的链接，用一次性脚本按路径重写。
- 用一次性本地脚本（不提交、不加依赖、不进 CI）检查 `docs/`、`README.md`、`AGENTS.md`、`apps/tap-ai-frontend/PRODUCT.md` 中所有相对 Markdown 链接的目标文件存在；排除 `node_modules/`、`.worktrees/`。锚点不校验。
- 验收：迁移后断链数不多于迁移前（迁移前基线先测一次）。

## 同步修改

- **`AGENTS.md`**
  - "Project Structure" 中文档目录清单改为新结构。
  - "Documentation Governance" 改为：spec 放 `docs/superpowers/specs/`、plan 放 `docs/superpowers/plans/`，文件名 `YYYY-MM-DD-<topic>[-design].md`；ADR 仅用于跨模块决策；`docs/archive/` 只读。
  - 原型基准链接改为 `docs/guides/…`。
  - 对象存储与 LangGraph 描述改为代码现状（以 `compose.yaml` 与后端适配器为准），不写目标态。
- **`README.md`**：保留顶部最新原型入口、启动命令与截图；删去历史文档链接清单、Gate/`PENDING` 散文和 ADR-029/RFC-011 目标叙述，改为链接 `docs/architecture.md`、V1 总纲、`docs/archive/index.md`。
- **`apps/tap-ai-frontend/PRODUCT.md`**：更新 4 处文档链接。
- **`docs/architecture.md`**：新写 1–2 页现状架构——应用边界、后端模块分层、基础设施、主要数据流（摄取、问答、图谱），以及已知与目标态的差距（链接 V1 总纲）。只写代码中可验证的事实。

## V1 总纲计划

文件：`docs/superpowers/plans/2026-09-29-v1-roadmap.md`。只做索引与验收，不写实施步骤。

每项能力用两个勾选框：**工程完成**（代码与自动化测试可证明）、**业务验证**（需真实资料或用户，单独勾选，不阻塞工程完成）。

| # | 能力 | 工程完成 | 业务验证 |
| --- | --- | --- | --- |
| 1 | 可靠问答 | 仓库内 golden set 格式与回归命令，CI 用 fake 模型运行、真实模型可选；LiteLLM 流式输出，SSE 由 worker 事件驱动逐 token 下发，替换 100ms 轮询；租约回收递增 `attempt` 且有上限，瞬时失败退避重试，worker loop 单次异常不退出；持久化集成测试缺 MySQL 时明确报告而非静默跳过 | 30–50 条真实标注问答跑出基线分数并记录 |
| 2 | 图谱展示 | 查询不再每次整快照载入内存，按快照缓存邻接表；本地基准约 1 万节点、5 万边下查询 p95 < 300ms | 真实资料生成的图谱可浏览 |
| 3 | 脉络分析 | 检索片段 → 实体 → 1–2 跳扩展 → 有界 LangGraph 工具循环（步数上限）→ 回答同时引用关系边与原文片段；点击引用在图谱中高亮路径；golden set 含关系类问题子集 | 真实关系类问题人工评审 |
| 4 | Skills/Agents | 导入 `SKILL.md` 生成不可变 Revision；Agent = 系统提示词 + Skills + 工具白名单 + 模型；工具注册表替代硬编码白名单；可注册 MCP server，其工具可加入白名单；导入 Skill 不执行内含代码 | 导入至少一个真实外部 Skill 并在对话中使用 |
| 5 | 可观测性 | 自托管 Langfuse 加入 compose；每个 Turn 有 trace ID，trace 含检索、图谱扩展、模型调用、工具调用 span 及 token 与成本；token 用量持久化到 MySQL；前端从回答跳转调用链并显示 token 面板 | 用真实对话排查一次问题 |

子项目顺序（各自走 spec → plan → 实施）：

0. 文档精简与本总纲（本 spec）
1. 基础设施收敛：移除 Codex 遗留路径与 Azure/Azurite 路径，3 个 LiteLLM 适配器合一，compose 收敛为 MySQL、Redis、MinIO、Milvus、LiteLLM
2. 可观测性（能力 5）
3. 可靠问答（能力 1，含后端可靠性修复）
4. 图谱查询扩展（能力 2）
5. 脉络分析（能力 3），按需拆分前端共享 `packages/` 与 router
6. Skills/Agents（能力 4）

"不在 V1"：Test IR、Jenkins、Web 执行、Low Code Automation 后端、主动 Agent、ADR-021/025 方向；`test_management` 冻结保留；Test Insights/ClickHouse 不在交付路径；Outbox → Relay → Redis Stream 简化暂不做，除非第 3 步顺带可完成。`/prototype` 继续展示全部模块，不构成 V1 交付物。

## 验证

- 链接检查：断链数不多于迁移前基线。
- `git diff --check` 通过。
- `make check` 中若有涉及文档路径的检查则须通过；本子项目不运行 `make test`（未改代码）。
- 人工预览 `README.md`、`AGENTS.md`、`docs/index.md`、`docs/architecture.md`、V1 总纲渲染。

## 不做

- 不改写归档文档正文，不修复归档文档内原有断链。
- 不删除任何文档。
- 不调整 `docs/assets/` 结构。
