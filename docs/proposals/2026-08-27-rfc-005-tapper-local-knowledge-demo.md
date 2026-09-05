---
id: RFC-005
status: implemented
date: 2026-08-27
related-adrs:
  - ADR-004
  - ADR-012
  - ADR-013
  - ADR-015
---

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

# RFC-005：Tapper 本地知识工作区 Demo

> **现行处置（2026-09-04）**：本 RFC 保持 `implemented`，只记录已经交付的 loopback、单知识空间 Tapper 本地纵向切片，并作为 [RFC-009](2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md) V1 的可复用起点；它不表示 V0/V1 已完成。现行交付已由 [ADR-021](../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 恢复为 Knowledge-first Web Automation，下文“当前 Phase 1 是 Intelligence Lab”、Azure 企业基线及“四索引不在当前交付”等表述均只记录 2026-09-02 及更早的历史语境。

> **当前阶段处置（2026-09-02）**：本 RFC 作为已实现的 Tapper 本地能力继续有效；文中“完整 Phase 1 仍 active/回到现有 Phase 1”记录的是当时路线图，现已由 [ADR-019](../decisions/2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md) 后置。当前 Phase 1 是 Intelligence Lab；Tapper 文档、revision/hash/anchor 和 Citation 只作为可选资料能力被复用，不代表完整 Knowledge Chat 或企业四索引成为当前交付项。

## 摘要

本 RFC 定义一个可在开发机运行的 Tapper 纵向 Demo：用户在 Web 页面上传 PDF、DOCX、Markdown 或 TXT 文档，查看真实解析与索引状态，选择可用来源进行知识问答，并从回答中的行内引用定位到不可变文档版本的原文片段。

Demo 复用现有 `KnowledgeAPI`、provider-neutral `SearchPort`、Milvus adapter、LiteLLM adapter、MySQL Outbox 与本地中间件，不另建一套简化 RAG。用户体验以 NotebookLM 的来源优先问答为主参考，知识导入与故障处理吸收 RAGFlow 和 Dify 的成熟模式，本地上传路径参考 AnythingLLM 与 Open WebUI。`Tapper` 仍只作为产品和 Web 外壳名称；后端领域与公共 API 使用稳定的 `knowledge` 命名。

实施结果：Tasks 1–10 的 runnable vertical slice、mandatory deterministic/local-middleware gate、跨应用/Compose 重启的文档与 ingestion/index 状态恢复、实际手工视觉/键盘验收和文档门禁均已完成，本 RFC 因此为 `implemented`。当前回答正文只在 Web 页面内存中，刷新会清空且本版没有 history 恢复 API。当前验收 checkout 没有 `.env`，真实模型项保持 `not-run: credentials not provided`；runnable LiteLLM route configured, provider unverified。此状态不关闭仍为 `active` 的完整 Phase 1，也不表示真实 provider、完整 Knowledge Chat、Azure AI Search 四 family 或共享/生产部署已验证。

## 背景

本 RFC 形成时，仓库已经实现公共契约、Turn/Outbox 持久层、可信 Policy 边界、授权 Knowledge 检索、Azure AI Search adapter 与 Milvus 本地检索实验，但尚无 `apps/web/`、用户上传入口、真实 ingestion worker 或可完成问答的 HTTP 纵向链路。当前 Phase 1 计划面向完整企业交付，包含身份、可恢复 Chat/SSE、四类索引、Trace、反馈、AKS 与容量门禁；若把全部能力作为 Demo 前置条件，无法尽快验证用户是否愿意使用 Tapper 完成“喂资料、限定来源、核验回答”的核心任务。

Tapper 不是只在聊天框旁增加上传按钮。它必须把来源建设、可用状态、回答范围和证据核验放在同一个工作区内，同时保持 RAG 的数据、检索和引用边界可演进到正式 Phase 1。

本 RFC 是 ADR-002 已允许的本地 Lab 交付，不改变 Azure 企业部署基线，也不把 RFC-004 的 Milvus 实验结论外推到共享或生产环境。

## 目标

- 提供一条无需身份登录的本地 Tapper 用户路径：添加文档、等待索引、选择来源、提问、核验引用、重试失败任务和删除文档。
- 支持可提取文本的 PDF、DOCX、Markdown 与 TXT；单文件上限固定为 `25 MiB`。
- 原文件、normalized artifact、文档元数据、任务账本和 Milvus 投影职责分离，上传与重试不产生重复文档或重复 chunk。
- 复用现有 `KnowledgeAPI.answer()` 完成服务端可信范围检索、回答生成、claim/citation 校验与证据不足拒答。
- 让每个引用解析到同一 source revision、content hash、结构锚点和原文片段；浏览器不直接读取 Blob 或 Milvus。
- 让解析、切片、Embedding、索引发布的当前阶段、失败摘要和重试动作对用户可见。
- 用真实本地中间件和真实配置模型完成可重复的端到端验收，同时用 deterministic fake 保护日常测试。

## 非目标

- 不实现 Entra ID、租户、多用户、多 Project、共享知识库或 ACL 管理页面。
- 不支持扫描 PDF、图片 OCR、PPTX、表格语义解析、网页抓取或外部数据源同步。
- 不实现 NotebookLM Studio 类音频、思维导图、报告或笔记生成功能。
- 不交付 Agent、工具调用、Web Search、Test IR、测试生成或执行能力。
- 不实现完整 Conversation 历史、消息排队、Turn SSE 重放、Trace 控制台或反馈闭环。
- 不暴露 chunk size、score threshold、向量权重、reranker 等 RAG 运维参数给普通 Demo 用户。
- 不把本地 Milvus、固定 demo policy 或本地凭据约定描述为共享环境或生产能力。

## 方案

### 产品基线与信息架构

Tapper 采用两个一级入口，并保持用户主流程与知识运维分离：

1. **问答**：左侧是可搜索、可勾选的来源列表；中间是基于当前来源的问答；右侧是引用原文查看器。处理中或失败的文档不能被勾选。回答中的引用支持点击后定位原文，而不是只显示文件名。
2. **知识库**：以文档表格展示文件类型、状态、chunk 数、更新时间与可执行动作；详情区展示保存、解析、切片、Embedding、索引发布阶段。失败文档提供安全错误摘要和重试，已就绪文档提供解析结果预览和删除。

上传使用单一弹窗和拖放区。上传完成后弹窗不承担长任务进度；用户回到知识库查看状态。空知识库先引导添加来源，只有存在至少一个已就绪来源时才启用提问。

首版只有一个固定的本地知识空间 `Tapper Lab`，不显示无意义的 workspace selector。知识空间最多保留 `50` 份未删除文档，一次问答最多选择 `20` 份 ready documents；这些是服务端硬上限，不由浏览器或模型提高。

交互模式的来源如下：

- [NotebookLM 来源管理](https://support.google.com/gemininotebook/answer/16215270)与[问答引用](https://support.google.com/gemininotebook/answer/16179559)：来源勾选、严格来源 grounding、行内引用和点击定位原文。
- [RAGFlow Quickstart](https://github.com/infiniflow/ragflow/blob/main/docs/quickstart.mdx)：文档解析状态、chunk 可见性和检索前质量检查。
- [Dify Knowledge Retrieval](https://github.com/langgenius/dify-docs/blob/main/en/cloud/use-dify/nodes/knowledge-retrieval.mdx)：知识范围、文档 metadata、检索结果和 citation attribution。
- [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)与[Open WebUI Knowledge](https://docs.openwebui.com/features/workspace/knowledge/)：本地优先、拖放上传和低门槛知识库使用路径。

Tapper 借鉴这些已经验证的交互，不复制品牌视觉、Agent builder、Studio 产物或管理员调参界面。

### 运行组件

```text
Browser / Tapper Web
        |
        v
FastAPI Knowledge HTTP API
   |          |             |
   |          |             +--> KnowledgeAPI --> Milvus + LiteLLM
   |          +--> MySQL document/job/manifest ledger
   +--> Azurite original + normalized artifact
                    |
                    v
          Outbox / Redis wake-up
                    |
                    v
           Tapper Ingestion Worker
              |             |
              +--> LiteLLM Embedding
              +--> Milvus Index Writer
```

- `apps/web` 使用 Vite、React、TypeScript、Ant Design、TanStack Query 与测试工具，遵守 RFC-003 的 `pages/widgets/features/shared` 依赖方向。
- FastAPI 只做有界 multipart 接收、事务性元数据写入、查询和依赖装配；解析、切片、Embedding 与索引写入不进入 API event loop。
- Ingestion Worker 是独立 entrypoint。MySQL job/manifest 是任务事实，Outbox 与 Redis 只负责可靠唤醒；Worker 同时扫描可领取的 pending/stale job，因此 Redis 丢失不会永久遗失上传任务。
- Azurite 保存原文件和 normalized artifact。MySQL 不保存大段原文，只保存 revision、hash、Blob locator、阶段结果和 chunk manifest。
- Milvus 继续只是可重建投影。Demo 复用 `doc` family collection schema、alias 绑定、strict provenance 与查询期 filter；固定 demo policy 由服务端构造，浏览器不能提交 tenant、group 或任意 filter。
- LiteLLM 暴露固定 `tapper-chat` 与 `tapper-embedding` alias；provider/model/key 只从本地环境注入。应用不按 provider 分支，也不把 raw provider model name 返回浏览器。

### 文档身份与数据模型

新增 Alembic revision `0003_tapper_documents`，在现有 Outbox 表之后建立以下事实：

- `knowledge_document`：`document_id`、显示文件名、media type、当前 revision、状态、当前阶段、最后安全错误摘要和时间戳。
- `knowledge_document_revision`：`revision_id`、`document_id`、source/content SHA-256、原文件 Blob locator、normalized artifact locator、parser/chunker/pipeline version。
- `knowledge_ingestion_job`：job identity、revision、attempt、状态、阶段、lease、重试时间和错误分类。
- `knowledge_chunk_manifest`：稳定 logical chunk ID、不可变 chunk ID、顺序、结构锚点、chunk hash、embedding/index version；正文仍保存在 normalized artifact 与 Milvus projection。
- `knowledge_answer_snapshot` 与 `knowledge_citation_snapshot`：绑定 `trace_id`、本次选择的 revisions、citation ID、chunk/hash/anchor 和创建时间，只用于 Citation Resolver，不形成 Conversation 历史。

文档身份规则固定如下：

- 上传内容 SHA-256 与当前已存在 revision 相同，且 media type 相同，返回现有文档，不创建重复 ingestion。
- `revision_id` 绑定 document identity、source content hash 与 parser version。
- `logical_chunk_id` 绑定文档和结构位置；`chunk_id` 绑定 revision、normalized content、anchor 与 chunker version。
- 相同 job 重放只能 upsert 相同 manifest/vector；发布前后都按 manifest 对账。
- 文件名不是身份，可重复且可安全显示；Blob locator、Milvus physical collection 和本地绝对路径不进入公共响应。

### 上传与 ingestion 数据流

1. API 在读取完整文件前检查 multipart metadata，并以流式方式执行 `25 MiB` 硬上限、扩展名/media type 白名单和 SHA-256 计算。
2. 原文件先写临时 Blob；MySQL 原子创建 document、revision、job 与 Outbox 后再提交为正式 locator。任何失败都不产生可查询的半成品 document。
3. Worker 按 `stored → parsing → chunking → embedding → publishing → ready` 顺序执行有界批次，并在每一阶段写入 checkpoint。
4. Parser 生成带页码、heading path、段落顺序与字符 offset 的 normalized artifact。PDF 只接受可提取文本；检测到无有效文本时以 `ocr-required` 失败。
5. Chunker 优先使用 heading/paragraph 结构，再使用有界 token fallback；不得把代码块、列表项或表格文本无提示地拆成不可定位内容。
6. Embedding 按服务端固定 batch、deadline、模型 alias 与维度执行；任何维度或 model version 漂移在写 Milvus 前失败。
7. Index Writer upsert 全部 chunks，按 revision/source/hash 对账并验证可读后，才把 document 标记为 `ready`。
8. 重试复用同一 document/revision，从最后一个未完成阶段继续。删除先将文档标记为不可选，再删除 Milvus 投影、Blob artifacts 和 MySQL manifest；任一步失败时保持 `deleting` 并可恢复清理。

### 问答与引用数据流

1. 浏览器把 query 和已勾选 document IDs 映射为现有 closed `ResourceRef` 列表后提交；至少一个对应文档必须为 `ready`。
2. HTTP mapper 将 selection 转换为 `doc` family 的 `ResourceRef(scope)`；固定 demo policy、环境、corpus 与模型配置由服务端注入。
3. `KnowledgeAPI.answer()` 执行 query embedding、Milvus hybrid search、现有 evidence/claim/citation validation 与证据不足拒答。查询结果只能来自本次选择的 revisions。
4. 回答通过验证后，API 在返回前原子保存最小 answer/citation snapshots；随机 citation ID 因此可以解析，但 snapshots 不提供历史列表或恢复 Chat 的能力。
5. 首版使用单次 JSON 响应，不在 citation 校验完成前向浏览器泄露未验证的 answer delta。UI 显示“检索来源”和“组织回答”等待状态。
6. 非拒答回答中的每个实质 claim 至少含一个 citation；Citation Resolver 重新校验 citation snapshot、revision、hash 和 document selection，再从 normalized artifact 返回小型原文窗口。
7. 点击引用更新右侧原文查看器并高亮 anchor 对应文本。hash 或 anchor 不一致、文档已删除或 snapshot 不存在时不显示近似内容，返回结构化 `citation-stale` 问题。

### HTTP API

公共路由保持后端 provider-neutral 与产品名无关：

```text
POST   /v1/knowledge/documents
GET    /v1/knowledge/documents?cursor=&limit=
GET    /v1/knowledge/documents/{documentId}
POST   /v1/knowledge/documents/{documentId}/retry
DELETE /v1/knowledge/documents/{documentId}
POST   /v1/knowledge/answers
GET    /v1/citations/{citationId}
GET    /health/live
GET    /health/ready
```

- 上传返回 `202` 与 document/job snapshot；重复内容返回同一个 document identity 和当前 snapshot。
- 文档状态是闭合联合：`queued | processing | ready | failed | deleting`；`processing` 另带闭合 stage。
- `/v1/knowledge/answers` 复用现有公开 retrieval request/response 语义，只增加文档选择所需的 closed `ResourceRef` 使用方式，不新增 Tapper 专有 answer DTO。
- REST 错误统一使用 RFC 9457 `application/problem+json`。公共 Problem Details 不返回异常堆栈、provider、credential、Blob locator、Milvus target 或原始 filter。
- cursor、limit、文件大小、文件数量、query 长度、选择文档数量、返回 citation 数和 source preview 字符数全部有服务端固定上限。

### 错误与安全语义

- 不支持格式、文件过大、空文件或无可提取文本分别映射为稳定的 `unsupported-document`、`document-too-large`、`empty-document` 和 `ocr-required` problem type。
- Parser、Embedding、Milvus 发布失败写入 job 的安全错误分类与阶段；用户可重试。密钥、endpoint、原始 SDK 异常和内部路径不进入错误摘要。
- LiteLLM 或 Milvus 在问答时不可用返回 `503`，不能伪装成正常零召回；检索确实无证据时返回现有结构化 abstention。
- 文档内容始终作为不可信数据，不能改变 system instruction、范围、模型路由或工具能力。Demo 不提供工具，因此文档 prompt injection 不能触发外部动作。
- Markdown 回答使用 allowlist sanitizer；链接默认作为文本，只有 Citation Resolver 返回的内部 citation action 可打开原文。
- 浏览器永不直连 MySQL、Redis、Azurite、Milvus 或 LiteLLM。

### Web 状态与视觉行为

- TanStack Query 管理 document list/detail、answer response 和 citation preview；上传进度属于本地 mutation 状态。
- Document processing 使用有限频率轮询；首版不引入只为进度服务的 SSE。离开上传弹窗或切换“问答/知识库”不取消 job。
- 当前来源 selection 是本地 UI 状态，只能包含服务端 snapshot 中的 `ready` documents；文档状态变化后自动移除不可用 selection。
- Citation viewer 只保留当前回答中选中的 citation。提交新问题或切换来源后清除旧 viewer，防止把旧证据误当当前回答依据。
- 桌面采用来源、问答、原文三栏；窄屏按来源、问答、原文顺序堆叠，不通过缩小字体维持三栏。

### 本地运行与配置

根命令新增以下稳定入口：

```sh
make demo-up       # 启动 MySQL、Redis、Azurite、Milvus 与 LiteLLM
make demo-dev      # 启动 Web、API、Relay 与 Tapper Ingestion Worker
make demo-check    # 验证数据库、Blob、Redis、Milvus 和两个模型 alias
make demo-e2e      # 运行本地 Tapper 用户路径
make demo-down     # 停止 Demo 服务但不隐式删除命名卷
```

`.env.example` 只声明本地默认值和空 credential placeholder。真实 provider credential 保存在 ignored `.env`；任何 destructive reset 必须使用独立、显式 opt-in 命令，普通 `demo-down` 不删除上传内容或数据库卷。

所有无身份保护的 Demo HTTP 端口默认只绑定 `127.0.0.1`。Web 开发服务器通过同源 proxy 访问 API，不开放任意 CORS origin；若未来需要局域网或共享访问，必须先引入身份、TLS 和单独的部署设计。

### 测试与验收策略

- **Unit**：四种 parser、无文本 PDF、结构 chunk、stable identity、大小边界、重复上传、stage transition、retry eligibility、citation anchor/hash validation。
- **Contract**：所有 HTTP DTO、Problem Details、closed status/stage、分页上限、秘密不进入响应、OpenAPI/TypeScript 确定性生成。
- **Integration**：真实 MySQL transaction/lease、Redis 唤醒丢失恢复、Azurite artifact round-trip、真实 Milvus upsert/filter/delete/rebuild、固定模型维度。
- **Web component**：来源勾选、处理中不可选、轮询状态、上传错误、重试、回答拒答、citation viewer、Markdown XSS、键盘与焦点。
- **Playwright**：上传 fixture、观察阶段、限定来源提问、点击引用定位原文、排除来源后不再引用、注入一次可恢复失败并重试、删除后零命中。
- **真实模型 smoke**：只有显式配置 provider credential 时运行，记录 model alias、调用成功与匿名 timing，不保存问题、原文、回答或 credential。日常测试使用 deterministic fake，不把 fake GREEN 描述成真实模型已验收。

## 替代方案

### 单进程、内存任务和本地目录

实现最少，但会绕过现有 Outbox、Blob port、Milvus adapter 和 Knowledge contract，产生一套无法演进的 Demo-only RAG，因此拒绝。

### 直接部署 RAGFlow、Dify、AnythingLLM 或 Open WebUI

这些产品适合作为交互与运维参考，但直接嵌入会引入第二套用户、知识身份、模型路由、引用和数据存储边界，无法验证 TAP 自己的 `KnowledgeAPI` 与未来 Tapper 嵌入契约，因此拒绝作为产品实现。它们仍可用于行为对标和验收比较。

### 先完成完整 Phase 1 Task 2–7

企业边界最完整，但把身份、Conversation 恢复、SSE、Trace、反馈和四 family ingestion 变成 Demo 前置条件，延迟核心用户验证，因此拒绝。本 RFC 完成后仍回到现有 Phase 1 计划逐项补齐这些能力。

### 在 Chat composer 临时上传附件

适合一次性问答，但会把来源状态、失败重试、范围选择和长期知识库隐藏在聊天历史里，不符合 Tapper 的来源优先定位，因此只保留为未来可选快捷入口，不作为本 Demo 的知识建设主路径。

## 风险与缓解

- **产品范围再次膨胀**：只实现问答与知识库两个入口；Studio 产物、Agent、检索调参和多知识空间全部列为非目标。
- **PDF 质量差导致回答差**：首版明确拒绝扫描 PDF，保存结构锚点，提供解析结果预览；不以无依据 OCR 猜测掩盖失败。
- **Embedding vector space 漂移**：collection、manifest、模型 alias、raw provider model 和维度共同绑定；任何不一致在发布前失败。
- **模型生成无效 citation**：生成结果在返回浏览器前进行 claim/evidence label 校验；无效结果转为明确拒答或受控失败。
- **Redis 唤醒丢失**：MySQL job 是事实，Worker 扫描 pending/stale job；Redis 只降低延迟。
- **删除留下可检索投影**：先将 document 从可选集合移除并阻止新查询，再清理 Milvus/Blob/manifest；真实 Milvus negative probe 是验收门禁。
- **参考产品变成视觉抄袭**：只复用信息架构和行为模式，继续使用 TAP/Tapper 品牌、组件 token 与领域语言。
- **本地 Demo 被误报为生产能力**：README、RFC、启动命令和验收报告都明确 local-only、no-auth、no-OCR 与真实模型门禁状态。

## 迁移或发布方式

1. 先扩展公共 document/answer/citation contracts，并取得 deterministic generation RED/GREEN。
2. 新增 document/revision/job/manifest migration、Blob port、parser/chunker 与 repository，使用 fake adapter 完成可恢复 ingestion。
3. 复用 Outbox/Redis，增加独立 Tapper Ingestion Worker，并接通真实 Azurite 与 Milvus publish/delete/rebuild。
4. 配置 LiteLLM 的 chat/embedding alias，接通现有 `KnowledgeAPI.answer()` 与 citation resolver。
5. 创建 `apps/web`，先完成知识库上传/状态/重试，再完成来源选择、问答和原文查看器。
6. 运行 contract、unit、integration、component 与 Playwright；具备 credential 时追加真实模型 smoke。
7. 同步 README、Phase 1 implementation plan 与相关架构状态，只把本 RFC 标记为 `implemented`；不得提前关闭完整 Phase 1 或改变 Azure 企业基线。

每一阶段保持可单独回退。回退代码或 Web 不隐式删除命名卷；需要清理 Demo 数据时使用精确项目名和显式 opt-in reset 命令。

## 验收标准

- 从空知识库开始，用户可在页面上传 PDF、DOCX、Markdown 与 TXT fixture，并看到每份文档进入 `ready` 或可解释的 `failed` 状态。
- 对同一文件重复上传不产生第二份 document、revision、job、manifest 或 vector。
- 注入 Parser、Embedding 和 publish 三类失败时，页面显示准确阶段；点击重试后从正确 checkpoint 继续并最终可用于问答。
- 用户只选择部分 ready documents 后，回答及 citations 只来自该范围；取消选择的来源在 search hits、claims、citations 三层均为零。
- 每个非拒答实质 claim 至少一个可解析 citation；点击 citation 打开相同 revision/hash/anchor 的原文窗口并高亮对应文本。
- 无证据、来源冲突或 citation 校验失败不会生成貌似成功的无依据答案。
- 删除文档后，新问答零命中该 source，Blob、manifest 与 Milvus projection 完成可验证清理。
- 恶意 Markdown 和文档 prompt injection 不能执行脚本、打开任意链接、改变检索范围或触发工具。
- `make demo-check`、Backend 全套测试、Web unit/component、Playwright、contract regeneration、`make check` 与 `git diff --check` 全部通过。
- README 明确写出本地启动、模型配置、支持格式、限制、清理方式和真实模型 smoke 状态。

## 未决问题

无。模型 provider 与具体 raw model 由本地 LiteLLM 配置选择，不属于本 RFC 的产品或领域决策。
