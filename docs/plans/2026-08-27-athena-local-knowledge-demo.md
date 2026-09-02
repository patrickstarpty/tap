---
status: completed
date: 2026-08-27
---

# Athena Local Knowledge Demo Implementation Plan

> **当前阶段处置（2026-09-02）**：本计划保持 `completed`，用于记录已交付的 Athena 本地能力。文中“完整 Phase 1 仍 active”属于当时验收语境；[ADR-019](../decisions/2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md) 已把完整 RAG/Knowledge Chat 后置，当前 Phase 1 改为 Intelligence Lab。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个可在开发机长期保存数据的 Athena Web Demo，让用户上传可提取文本的 PDF、DOCX、Markdown 与 TXT，观察真实 ingestion 状态，限定来源问答，并点击引用核验同一文档 revision 的原文。

**Architecture:** 在现有 provider-neutral `KnowledgeAPI` 外增加 document ledger、artifact storage、ingestion worker、只含 query/所选 revisions 的 answer resolver snapshot、citation resolver 与薄 HTTP 层；MySQL 是事实与 job checkpoint，但不保存回答正文/history，Azurite 保存原文件及派生 artifact，Milvus 是可重建投影，Redis 只做唤醒，LiteLLM 固定提供 chat/embedding alias。Web 采用来源优先的 Athena 工作区，不建立第二套 Chat/RAG，也不把本地 Demo 约定扩展为共享环境或生产基线。

**Tech Stack:** Python 3.13.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、Azure Blob SDK、PyPDF、python-docx、tiktoken、PyMilvus、LiteLLM、MySQL 8.4、Redis 7.4、Azurite、Milvus 2.6；Node 22.22.0、pnpm 10.15.1、Vite、React、TypeScript、Ant Design、Tailwind CSS、TanStack Query、React Markdown、Vitest、Testing Library、Playwright。

**Spec:** `docs/proposals/2026-08-27-rfc-005-athena-local-knowledge-demo.md`

**Acceptance result:** Tasks 1–10 已完成 mandatory deterministic/local-middleware、实际手工视觉/键盘与文档门禁，本计划因此为 `completed`，RFC-005 为 `implemented`。当前 checkout 没有 `.env`，可选真实模型项准确记录为 `not-run: credentials not provided`；runnable LiteLLM route configured, provider unverified，这不等于真实 provider 已验证。完整 Phase 1 仍为 `active`。

## Global Constraints

- `Athena` 只用于 Web 产品外壳和用户文案；Backend 模块、公共 DTO 与路由保持 `knowledge` / `document` / `citation` 命名。
- 首版固定单一知识空间 `Athena Lab`，最多保留 `50` 份未删除文档，一次问答最多选择 `20` 份 `ready` 文档。
- 单文件硬上限为 `25 MiB`；只接受可提取文本的 PDF、DOCX、Markdown 与 TXT，不实现 OCR、PPTX、表格语义、网页抓取或外部数据源。
- 文档公开状态固定为 `queued | processing | ready | failed | deleting`；processing stage 固定为 `stored | parsing | chunking | embedding | publishing | ready`。
- 上传、解析、切片、Embedding、发布、重试与删除必须使用真实持久事实；不得以浏览器内存、API 进程内队列、假进度或内嵌 fixture 回答冒充成功。
- 相同 content SHA-256 与 media type 的重复上传必须返回同一个 document/revision，不新增 job、manifest 或 vector。
- MySQL 保存 document/revision/job/manifest/answer/citation facts；Azurite 保存 original、normalized、chunk 与 embedding artifact；Milvus 只保存可重建 projection；Redis 丢失不能丢 job。
- 问答必须调用现有 `KnowledgeAPI.answer()`，固定 demo policy 只能由服务端构造，浏览器不能提交 tenant、group、classification、environment、corpus 或任意 provider filter。
- LiteLLM 公共模型名固定为 `athena-chat` 与 `athena-embedding`；raw provider model、API key、base URL、Blob locator、Milvus physical collection 和 SDK 异常都不能进入公共响应。
- 首版 answer 使用单次 JSON 响应；citation/claim 校验和最小 snapshot 提交成功前不能向浏览器返回未验证 answer delta。
- 服务端固定 query 最长 `8,000` 字符、列表默认 `25`/最大 `50`、cursor 最长 `512` 字符、answer citation 最多 `20` 个、quote 最长 `4,000` 字符且前后文各 `500` 字符；只保留最近 `1,000` 个 answer resolver snapshots（query hash/所选 revisions，不含回答正文/history）；Web 不提供修改这些上限的入口。
- 所有非拒答实质 claim 至少包含一个可解析 citation；revision/hash/anchor 不一致或文档删除时返回 `citation-stale`，不能展示近似内容。
- 无身份 Demo 的 Web/API 只绑定 `127.0.0.1`，Vite 使用同源 proxy；不开放任意 CORS origin。
- `make demo-down` 必须保留 MySQL、Redis、Azurite 与 Milvus 命名卷；任何清空命令都要独立命名并要求显式 opt-in 环境变量。
- 新行为严格按 Red → verify RED → Green → verify Green 实施；新增依赖使用 exact version 并提交 `uv.lock` / `pnpm-lock.yaml`。
- 每个任务结束前运行局部测试、`make check`、相关前端检查和 `git diff --check`；最后任务运行完整 acceptance gate。

## Final File Responsibilities

- `apps/backend/src/tap/contracts/http.py`：唯一人工维护的 document/answer/citation 公共 Pydantic DTO。
- `apps/backend/src/tap/modules/knowledge/domain/documents.py`：文档身份、状态、stage、normalized block、chunk 与 job 不变量。
- `apps/backend/src/tap/modules/knowledge/ports/documents.py`：repository、artifact、embedding、index 与 wake-up 的 provider-neutral ports。
- `apps/backend/src/tap/modules/knowledge/application/documents.py`：上传、列表、详情、重试和删除应用服务。
- `apps/backend/src/tap/modules/knowledge/application/ingestion.py`：可领取、可 checkpoint、可恢复的 ingestion/deletion state machine。
- `apps/backend/src/tap/modules/knowledge/application/answers.py`：选择 ready revisions、构造固定 demo policy、调用 `KnowledgeAPI.answer()` 并提交 snapshot。
- `apps/backend/src/tap/modules/knowledge/application/citations.py`：citation snapshot 的 revision/hash/anchor 复验与原文窗口解析。
- `apps/backend/src/tap/modules/knowledge/adapters/mysql_documents.py`：六类 Knowledge 表、lease 与事务性 Outbox 的 SQLAlchemy adapter。
- `apps/backend/src/tap/modules/knowledge/adapters/blob_artifacts.py`：Azurite original/normalized/chunk/embedding artifact lifecycle。
- `apps/backend/src/tap/modules/knowledge/adapters/document_parsers.py`：PDF、DOCX、Markdown、TXT 的闭合 parser registry。
- `apps/backend/src/tap/modules/knowledge/adapters/document_chunker.py`：结构优先、token 有界、anchor 可回查的 chunker。
- `apps/backend/src/tap/modules/knowledge/adapters/milvus_documents.py`：Athena `doc` collection bootstrap、upsert、对账与删除。
- `apps/backend/src/tap/interfaces/http/routes/`：薄 route、multipart 边界与 RFC 9457 error mapping。
- `apps/backend/src/tap/entrypoints/athena_api.py` 与 `athena_ingestion_worker.py`：只负责配置校验、依赖装配、生命周期和启动。
- `apps/web/src/pages/AthenaPage.tsx`：问答/知识库一级导航页。
- `apps/web/src/widgets/athena/AthenaWorkspace.tsx`：来源、问答、原文三栏工作区与响应式编排。
- `apps/web/src/features/knowledge/`：单一 feature 内的 API、query hooks、selection、上传、知识库、answer 和 citation UI；不存在 `feature → feature` import。
- `apps/web/src/shared/api/generated/schema.ts`：由提交的 OpenAPI 确定性生成，不人工编辑。
- `scripts/check-athena-demo.py`：数据库、Blob、Redis、Milvus 与两个 LiteLLM alias 的真实 preflight。
- `scripts/run-athena-dev.sh`：Web、API、Relay、Worker 四进程的本地 supervisor，接收信号后完整收尾。

---

### Task 1: Public Document and Citation Contracts

**Files:**
- Create: `apps/backend/src/tap/interfaces/http/dependencies.py`
- Create: `apps/backend/src/tap/interfaces/http/problems.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/knowledge_documents.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/knowledge_answers.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/citations.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/health.py`
- Create: `apps/backend/tests/contract/test_athena_http_contract.py`
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/src/shared/api/generated/schema.ts`
- Create: `apps/web/scripts/generate-api.mjs`
- Modify: `apps/backend/src/tap/contracts/http.py`
- Modify: `apps/backend/src/tap/modules/knowledge/domain/models.py`
- Modify: `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- Modify: `apps/backend/src/tap/modules/knowledge/api.py`
- Modify: `apps/backend/src/tap/interfaces/http/app.py`
- Modify: `apps/backend/pyproject.toml`
- Modify: `uv.lock`
- Modify: `apps/backend/tests/contract/test_generated_contracts.py`
- Modify: `apps/backend/tests/contract/test_public_retrieval_contract_strict.py`
- Modify: `apps/backend/tests/contract/test_knowledge_api.py`
- Modify: `apps/backend/tests/contract/test_litellm_strict.py`
- Modify: `scripts/export_contracts.py`
- Modify: `package.json`
- Modify: `pnpm-workspace.yaml`
- Modify: `pnpm-lock.yaml`
- Modify: `Makefile`
- Modify: `contracts/openapi/api.json`

**Interfaces:**
- Consumes: existing `RetrievalAnswerRequest`, `RetrievalAnswerResponse`, `ProblemDetails`, `ResourceRef` and side-effect-free `create_app()`.
- Produces: public DTOs `DocumentAccepted`, `DocumentPage`, `DocumentDetail`, `CitationPreview`, `LiveHealth`, `ReadyHealth`; answer-claim spans; stable operation IDs; `KnowledgeHttpService` protocol; deterministic `apps/web/src/shared/api/generated/schema.ts`.

- [ ] **Step 1: Write the failing closed-contract tests**

Add tests that inspect Pydantic JSON Schema and OpenAPI, including this exact behavior skeleton:

```python
def test_document_contract_is_closed_and_bounded() -> None:
    schema = DocumentDetail.model_json_schema(by_alias=True)
    assert schema["additionalProperties"] is False
    assert set(schema["$defs"]["DocumentStatus"]["enum"]) == {
        "queued", "processing", "ready", "failed", "deleting"
    }
    assert set(schema["$defs"]["IngestionStage"]["enum"]) == {
        "stored", "parsing", "chunking", "embedding", "publishing", "ready"
    }
    serialized = json.dumps(schema)
    assert all(
        forbidden not in serialized
        for forbidden in ("blobLocator", "physicalCollection", "providerModel")
    )


def test_athena_routes_have_stable_provider_neutral_operation_ids() -> None:
    paths = create_app().openapi()["paths"]
    assert paths["/v1/knowledge/documents"]["post"]["operationId"] == "knowledge_upload_document"
    assert paths["/v1/knowledge/answers"]["post"]["operationId"] == "knowledge_create_answer"
    assert paths["/v1/citations/{citation_id}"]["get"]["operationId"] == "citation_get_preview"
```

Also assert `limit` is `1..50`, public error media type is `application/problem+json`, answer request contains no authoritative policy field, and upload response is `202`.

- [ ] **Step 2: Run the contract tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/contract/test_athena_http_contract.py \
  apps/backend/tests/contract/test_generated_contracts.py -v
```

Expected: FAIL because the document/citation DTOs, route modules and generated TypeScript artifact do not exist.

- [ ] **Step 3: Define the exact public DTO graph**

Add closed enums and models with these fields and bounds:

```python
class DocumentStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class IngestionStage(str, Enum):
    STORED = "stored"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    PUBLISHING = "publishing"
    READY = "ready"


class DocumentSummary(ContractModel):
    document_id: Annotated[str, Field(min_length=1, max_length=64)]
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: Literal["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/markdown", "text/plain"]
    status: DocumentStatus
    stage: IngestionStage
    chunk_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    updated_at: TimestampValue
    error_code: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    error_summary: Annotated[str, Field(min_length=1, max_length=240)] | None = None


class DocumentAccepted(ContractModel):
    document: DocumentSummary
    job_id: Annotated[str, Field(min_length=1, max_length=64)]
    duplicate: bool


class DocumentPage(ContractModel):
    items: Annotated[list[DocumentSummary], Field(max_length=50)]
    next_cursor: Annotated[str, Field(min_length=1, max_length=512)] | None = None


class DocumentDetail(DocumentSummary):
    revision_id: Annotated[str, Field(min_length=1, max_length=128)]
    source_content_hash: CanonicalSha256
    stages: Annotated[list[DocumentStageSnapshot], Field(min_length=1, max_length=6)]
    normalized_preview: Annotated[str, Field(max_length=4_000)] | None = None


class CitationPreview(ContractModel):
    citation_id: Annotated[str, Field(min_length=1, max_length=64)]
    document_id: Annotated[str, Field(min_length=1, max_length=64)]
    revision_id: Annotated[str, Field(min_length=1, max_length=128)]
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    source_content_hash: CanonicalSha256
    chunk_content_hash: CanonicalSha256
    anchor: StructuralAnchor
    quote: Annotated[str, Field(min_length=1, max_length=4_000)]
    prefix: Annotated[str, Field(max_length=500)] = ""
    suffix: Annotated[str, Field(max_length=500)] = ""


class LiveHealth(ContractModel):
    status: Literal["ok"]


class ReadyHealth(ContractModel):
    status: Literal["ready", "unready"]
    components: Annotated[list[HealthComponent], Field(min_length=5, max_length=5)]
```

`HealthComponent` contains closed name `mysql | redis | blob | milvus | models`, state `ok | failed`, and optional fixed remediation code; it exposes no endpoint or exception. `DocumentStageSnapshot` is `{stage, state, completedAt, errorCode}` with state limited to `pending | processing | completed | failed`. `DocumentDetail` must reject error fields for non-failed status and require them for failed status through a model validator.

Extend the provider-neutral `RetrievalClaim` with strict `answer_start` and `answer_end` non-negative Unicode code-point offsets. `RetrievalAnswerResponse` validates ordered, non-overlapping spans, requires `answer[answer_start:answer_end] == claim.text`, and requires each claim span to occupy a complete paragraph boundary (`start == 0` or preceded by `\n\n`; `end == len(answer)` or followed by `\n\n`). This is a general grounded-answer contract, not an Athena-only field.

Extend the domain `Claim` with the same fields. In `AuthorizedRetrieval.answer`, require each generated claim text to occur exactly once as a complete answer paragraph, compute the Unicode code-point span, sort spans and abstain on missing/ambiguous/overlapping text. Map both offsets in `answer_response_to_http`. Tighten the LiteLLM system prompt to require every claim text to be one complete paragraph copied exactly into `answer`, while retaining server-side validation as authority.

- [ ] **Step 4: Add typed routes without starting external services**

Define `KnowledgeHttpService` with the signatures later tasks implement:

```python
class KnowledgeHttpService(Protocol):
    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...
    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...
    async def get_document(self, document_id: str) -> DocumentDetail: ...
    async def retry_document(self, document_id: str) -> DocumentAccepted: ...
    async def delete_document(self, document_id: str) -> None: ...
    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse: ...
    async def citation(self, citation_id: str) -> CitationPreview: ...
```

`UploadInput` contains sanitized `filename`, canonical `media_type` and an `AsyncIterable[bytes]`; it never contains a local path. `create_app(services: HttpServices | None = None)` registers all routes for OpenAPI but only resolves the service when a handler is called. The unconfigured runtime returns a stable `503 knowledge-runtime-unavailable` Problem Details rather than fake data.

Wrap `UploadFile.read(1_048_576)` in an async iterator; reject an oversized declared `Content-Length` early but always enforce the byte count again while streaming. No route calls `await upload.read()` without a bound or buffers the complete multipart body in application memory.

Normalize the display filename to NFC, reject NUL/control characters, path separators and more than 255 code points, then require the extension/media pair `.pdf/application/pdf`, `.docx/application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `.md|.markdown/text/markdown`, or `.txt/text/plain`. The filename remains untrusted display text and is never used as a filesystem or Blob path.

Before registering the multipart route, run `uv add --project apps/backend --exact python-multipart` and commit the lock; FastAPI app construction must succeed in contract export without an undeclared optional parser.

- [ ] **Step 5: Add deterministic TypeScript generation**

Make `apps/web` a pnpm workspace package named `@tap/web`, pin `openapi-typescript@7.13.0` and `typescript@5.9.3` as exact dev dependencies, and add `contracts` / `contracts:check` scripts around the location-stable generator. Make the root contract flow:

```make
contracts:
	uv run --project apps/backend python scripts/export_contracts.py
	corepack pnpm --filter @tap/web run contracts
```

`apps/web/scripts/generate-api.mjs` resolves `../../../contracts/openapi/api.json` and `../src/shared/api/generated/schema.ts` from `import.meta.url`, never from process cwd. Its `--check` mode generates into a temporary file, byte-compares it to `schema.ts`, and always removes the temporary file. Add it to `make check`. Register every new Pydantic model in `KNOWLEDGE_HTTP_MODELS` so route schemas and standalone components cannot drift.

- [ ] **Step 6: Generate contracts and verify GREEN**

```sh
corepack pnpm install
make contracts
uv run --project apps/backend pytest \
  apps/backend/tests/contract/test_athena_http_contract.py \
  apps/backend/tests/contract/test_generated_contracts.py \
  apps/backend/tests/contract/test_public_retrieval_contract_strict.py \
  apps/backend/tests/contract/test_knowledge_api.py \
  apps/backend/tests/contract/test_litellm_strict.py -v
corepack pnpm --filter @tap/web run contracts:check
git diff --check
```

Expected: all commands exit `0`; a second `make contracts` produces no diff, and OpenAPI contains all nine RFC-005 routes including health endpoints.

- [ ] **Step 7: Commit the contract slice**

```sh
git add apps/backend/src/tap/contracts/http.py apps/backend/src/tap/interfaces/http \
  apps/backend/src/tap/modules/knowledge \
  apps/backend/pyproject.toml uv.lock apps/backend/tests/contract apps/web \
  package.json pnpm-workspace.yaml pnpm-lock.yaml \
  Makefile scripts/export_contracts.py contracts/openapi/api.json
git commit -m "feat: add knowledge document contracts"
```

### Task 2: Stable Identities, Parsers, and Structural Chunking

**Files:**
- Create: `apps/backend/src/tap/modules/knowledge/domain/documents.py`
- Create: `apps/backend/src/tap/modules/knowledge/ports/documents.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/document_parsers.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/document_chunker.py`
- Create: `apps/backend/tests/unit/knowledge/test_document_identities.py`
- Create: `apps/backend/tests/unit/knowledge/test_document_parsers.py`
- Create: `apps/backend/tests/unit/knowledge/test_document_chunker.py`
- Create: `apps/backend/tests/fixtures/athena/source.md`
- Create: `apps/backend/tests/fixtures/athena/source.txt`
- Modify: `apps/backend/pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: public media-type set and existing `DocumentAnchor` / canonical SHA-256 conventions.
- Produces: `ParserRegistry.parse(DocumentSource) -> NormalizedArtifact`, `StructuralChunker.chunk(NormalizedArtifact) -> tuple[ChunkDraft, ...]`, stable ID functions, and closed ingestion errors used by persistence and worker tasks.

- [ ] **Step 1: Write identity and parser RED tests**

Use in-memory PDF/DOCX builders in test helpers and assert real structural output:

```python
def test_revision_and_chunk_ids_are_stable_but_revision_sensitive() -> None:
    source_hash = canonical_sha256(b"# Refunds\nTwo approvers are required.")
    document_id = DocumentId("doc_01JTESTDOCUMENT000000000000")
    first = revision_id_for(document_id, source_hash, "parser-v1")
    second = revision_id_for(document_id, source_hash, "parser-v2")
    assert first == revision_id_for(document_id, source_hash, "parser-v1")
    assert first != second


@pytest.mark.parametrize("kind", ["pdf", "docx", "markdown", "txt"])
def test_supported_parser_preserves_order_and_anchor(kind: str) -> None:
    artifact = registry.parse(build_source(kind, heading="Refund policy", body="Two approvals."))
    paragraph = next(block for block in artifact.blocks if block.kind == "paragraph")
    assert paragraph.heading_path == ("Refund policy",)
    assert paragraph.text == "Two approvals."
    assert paragraph.start_offset < paragraph.end_offset


def test_scanned_pdf_fails_as_ocr_required() -> None:
    with pytest.raises(DocumentParseRejected, match="ocr-required"):
        registry.parse(DocumentSource("scan.pdf", MediaType.PDF, empty_text_pdf()))
```

Add separate tests for a `25 MiB`-adjacent source hash stream, fenced Markdown code, Markdown/DOCX table text remaining one block, DOCX heading levels, PDF page numbers, TXT paragraphs, empty input, invalid/zip-bomb DOCX, and an unsupported extension/media mismatch.

- [ ] **Step 2: Run the unit tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/knowledge/test_document_identities.py \
  apps/backend/tests/unit/knowledge/test_document_parsers.py \
  apps/backend/tests/unit/knowledge/test_document_chunker.py -v
```

Expected: FAIL because the document domain, parser registry and chunker do not exist.

- [ ] **Step 3: Implement closed document values and stable IDs**

Define immutable values and exact bounds:

```python
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_NORMALIZED_CHARACTERS = 8_000_000
MAX_CHUNKS_PER_DOCUMENT = 10_000
PARSER_VERSION = "athena-parser-v1"
CHUNKER_VERSION = "athena-structure-512-v1"


def new_document_id(id_factory: Callable[[], str]) -> DocumentId:
    return DocumentId("doc_" + sha256(id_factory().encode("utf-8")).hexdigest()[:32])


def revision_id_for(document_id: DocumentId, source_hash: str, parser_version: str) -> RevisionId:
    return RevisionId("rev_" + sha256(f"{document_id}\0{source_hash}\0{parser_version}".encode()).hexdigest())


def chunk_id_for(revision_id: RevisionId, anchor_json: str, chunk_hash: str) -> ChunkId:
    return ChunkId("h_" + sha256(f"{revision_id}\0{anchor_json}\0{chunk_hash}\0{CHUNKER_VERSION}".encode()).hexdigest())
```

`document_id` is an opaque generated identity, not a content hash; active-upload deduplication belongs to the repository so deleting and later re-uploading the same bytes creates a new document and keeps old citations stale. `NormalizedBlock` contains `block_id`, `text`, `heading_path`, `page`, `paragraph_index`, `start_offset`, `end_offset`, and `kind` (`heading | paragraph | list | code | table_text`). Heading blocks may update the heading path without becoming answer content. `NormalizedArtifact` stores source identity, frozen `normalized-artifact-v1` schema and ordered blocks only; it rejects more than `8_000_000` normalized characters. `ChunkDraft` carries content, canonical anchor JSON, hashes, root/parent IDs and no vector.

- [ ] **Step 4: Implement four parsers and fail-closed registry**

Run `uv add --project apps/backend --exact pypdf python-docx tiktoken` and `uv add --project apps/backend --dev --exact reportlab`; update the lock. Implement:

```python
PARSERS: Mapping[MediaType, DocumentParser] = {
    MediaType.PDF: PdfParser(),
    MediaType.DOCX: DocxParser(),
    MediaType.MARKDOWN: MarkdownParser(),
    MediaType.TEXT: TextParser(),
}
```

PDF reads pages through `pypdf.PdfReader`, rejects encrypted inputs and fails `ocr-required` when all extracted page text is blank. Before python-docx opens a ZIP, reject more than 10,000 entries, any unsafe path, or more than 100 MiB total declared uncompressed bytes; DOCX then maps `Heading 1..9` to heading paths, preserves paragraph order and flattens each table to one `table_text` block with row/cell separators. Markdown tracks ATX headings, treats fenced code as one block and preserves a pipe-table region as one `table_text` block. TXT splits Unicode-normalized paragraphs. These table blocks preserve locator/text only and do not claim table semantics. All parsers normalize CRLF to LF, reject NUL, preserve source text without executing macros/links, and convert provider exceptions to only `unsupported-document`, `empty-document`, `invalid-document`, or `ocr-required`.

- [ ] **Step 5: Implement structural token chunking**

Use `tiktoken.get_encoding("cl100k_base")`, maximum `512` tokens and `64` token overlap only when one structural block itself exceeds the limit. Keep a code block or list item intact when it is within the limit. The chunker must execute these guards before returning:

```python
if not chunks:
    raise DocumentParseRejected("empty-document")
if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
    raise DocumentParseRejected("document-too-complex")
if any(counter.count(chunk.content) > 512 for chunk in chunks):
    raise AssertionError("chunker emitted an oversized chunk")
if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
    raise AssertionError("chunk identities are not unique")
```

Anchor JSON uses sorted compact JSON with `type=document`, `headingPath`, optional `page`, `startOffset`, and `endOffset`. `logical_chunk_id` binds document plus structural location; `chunk_id` additionally binds revision, content and chunker version.

- [ ] **Step 6: Verify parser/chunker GREEN and regressions**

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/knowledge -v
uv run --project apps/backend ruff check apps/backend/src/tap/modules/knowledge apps/backend/tests/unit/knowledge
uv run --project apps/backend mypy apps/backend/src/tap/modules/knowledge
make check
git diff --check
```

Expected: all supported format tests pass, scanned PDF returns only `ocr-required`, IDs are deterministic across processes, and existing Knowledge/Milvus contracts stay green.

- [ ] **Step 7: Commit the parsing slice**

```sh
git add apps/backend/pyproject.toml uv.lock apps/backend/src/tap/modules/knowledge \
  apps/backend/tests/unit/knowledge apps/backend/tests/fixtures/athena
git commit -m "feat: parse and chunk knowledge documents"
```

### Task 3: Durable Document Ledger and Upload Commands

**Files:**
- Create: `apps/backend/migrations/versions/0003_athena_documents.py`
- Create: `apps/backend/src/tap/platform/db/schema.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/mysql_documents.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/documents.py`
- Create: `apps/backend/tests/unit/knowledge/test_document_service.py`
- Create: `apps/backend/tests/integration/test_document_ledger.py`
- Create: `apps/backend/tests/integration/test_document_upload_recovery.py`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/documents.py`
- Modify: `apps/backend/src/tap/modules/chat/adapters/mysql.py`
- Modify: `apps/backend/migrations/env.py`
- Modify: `apps/backend/tests/architecture/test_module_boundaries.py`

**Interfaces:**
- Consumes: stable identities, `UploadInput`, the generic `outbox` table, `async_sessionmaker[AsyncSession]`, and an `ArtifactStore` fake.
- Produces: shared DB `metadata/outbox`, `DocumentService.upload/list/get/retry/delete`, `MysqlDocumentRepository`, six durable Knowledge tables, job leases and transactional `knowledge.ingestion_requested` / `knowledge.deletion_requested` Outbox rows.

- [ ] **Step 1: Write service and real-MySQL RED tests**

Cover one behavior per test, including concurrent duplicate submission and restart:

```python
@pytest.mark.asyncio
async def test_duplicate_upload_returns_same_document_without_second_job() -> None:
    first = await service.upload(markdown_upload("policy.md", b"# Policy\nTwo approvers"))
    second = await service.upload(markdown_upload("renamed.md", b"# Policy\nTwo approvers"))
    assert second.duplicate is True
    assert second.document.document_id == first.document.document_id
    assert await repository.count_jobs(first.document.document_id) == 1


@pytest.mark.asyncio
async def test_claimed_job_survives_repository_reconstruction(mysql_sessions) -> None:
    reservation = await first_repository.reserve_upload(command)
    created = await first_repository.activate_upload(reservation, ArtifactLocator("blob:test"))
    del first_repository
    claimed = await MysqlDocumentRepository(mysql_sessions).claim_jobs(
        worker_id="worker-b", now=clock.now(), lease_duration=timedelta(seconds=30), limit=10
    )
    assert [job.job_id for job in claimed] == [created.job_id]
```

Also test 50-document rejection, byte 25 MiB accepted/25 MiB+1 rejected without a document row, cursor stability, failed-only retry, `deleting` immediately disappearing from selectable documents, lease token ownership, expired lease recovery, and rollback leaving no document/job/outbox combination.

- [ ] **Step 2: Run the ledger tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/knowledge/test_document_service.py \
  apps/backend/tests/integration/test_document_ledger.py \
  apps/backend/tests/integration/test_document_upload_recovery.py -v
```

Expected: FAIL because migration `0003_athena_documents`, the repository and application service are absent.

- [ ] **Step 3: Create the exact durable schema**

Make `0003_athena_documents` depend on `0002_outbox_claim_token` and create:

```text
knowledge_document
  document_id PK, filename, media_type, current_revision_id, source_content_hash,
  dedupe_key NULL,
  status, stage, chunk_count, error_code, error_summary,
  activated_at NULL, created_at, updated_at, deleted_at NULL
  UNIQUE(dedupe_key)
  INDEX(status, updated_at, document_id)

knowledge_document_revision
  revision_id PK, document_id FK, source_content_hash,
  original_blob_locator, normalized_blob_locator NULL,
  chunks_blob_locator NULL, embeddings_blob_locator NULL,
  parser_version, chunker_version, pipeline_version, created_at
  UNIQUE(document_id, source_content_hash, parser_version)

knowledge_ingestion_job
  job_id PK, revision_id FK, kind, attempt, status, stage,
  stage_results_json,
  lease_owner NULL, lease_token NULL, lease_until NULL,
  next_attempt_at, error_code NULL, error_summary NULL,
  created_at, updated_at, completed_at NULL
  UNIQUE(revision_id, kind)
  INDEX(status, next_attempt_at, created_at)
  INDEX(status, lease_until)

knowledge_chunk_manifest
  chunk_id PK, logical_chunk_id, revision_id FK, ordinal,
  root_id, parent_id NULL, anchor_json, chunk_content_hash,
  embedding_model_version, index_version, created_at
  UNIQUE(revision_id, ordinal)

knowledge_answer_snapshot
  trace_id PK, query_hash, selected_revisions_json, created_at

knowledge_citation_snapshot
  citation_id PK, trace_id FK, document_id, revision_id, chunk_id,
  source_content_hash, chunk_content_hash, anchor_json, created_at
  UNIQUE(trace_id, citation_id)
```

The citation-to-answer foreign key uses `ON DELETE CASCADE`; document/revision identities in citation snapshots are immutable values rather than cascading foreign keys so soft deletion retains stale-proof evidence. Use `DATETIME(fsp=6)`, canonical UTC-naive storage like the existing repository, closed string values, named foreign keys/indexes, and a reverse-order downgrade. Locators are internal and never mapped into `DocumentSummary` or `DocumentDetail`.

`dedupe_key` is canonical SHA-256 of media type plus source hash while a document is active; the final delete transaction sets it to `NULL`, so later identical bytes create a new opaque document identity and old citations remain stale. Create `knowledge_document.current_revision_id` nullable before the revision table, then add the named current-revision foreign key after both tables exist; downgrade drops that circular FK first. `stage_results_json` is a closed six-entry checkpoint projection containing state/completion time/error code so fast stages remain visible after restart.

Inside `reserve_upload`, lock the active `dedupe_key` index range, return an existing duplicate before applying capacity, then count locked active rows and reject a 51st unique document. The concurrency test submits two different 50th/51st files simultaneously and proves exactly one reservation succeeds.

- [ ] **Step 4: Define repository and artifact transaction boundaries**

Add these provider-neutral port methods with immutable command/result dataclasses:

```python
class DocumentRepository(Protocol):
    async def reserve_upload(self, command: ReserveUpload) -> UploadReservation: ...
    async def activate_upload(self, reservation: UploadReservation, original: ArtifactLocator) -> DocumentRecord: ...
    async def abandon_upload(self, reservation_id: str, owner_token: str) -> None: ...
    async def list_documents(self, cursor: DocumentCursor | None, limit: int) -> DocumentRecordPage: ...
    async def get_document(self, document_id: DocumentId, *, include_deleting: bool = False) -> DocumentRecord | None: ...
    async def retry_failed(self, document_id: DocumentId, now: datetime) -> IngestionJob: ...
    async def request_delete(self, document_id: DocumentId, now: datetime) -> IngestionJob: ...
    async def claim_jobs(self, *, worker_id: str, now: datetime, lease_duration: timedelta, limit: int) -> tuple[ClaimedIngestionJob, ...]: ...
    async def renew_lease(self, job_id: str, lease_token: str, now: datetime, lease_duration: timedelta) -> None: ...
    async def checkpoint(self, checkpoint: JobCheckpoint) -> None: ...
    async def fail_job(self, failure: JobFailure) -> None: ...
```

`reserve_upload` creates an `activated_at IS NULL` reservation guarded by the unique media/hash identity. Such rows never appear in list/get/select queries. Its result distinguishes `owned`, `duplicate_pending`, and `duplicate_active`. `activate_upload` locks the reservation and atomically sets the formal original locator, creates one job plus one Outbox row, and makes the document visible; it is idempotent when another uploader activated the same reservation first. Concurrent uploaders may both help finish the same content-addressed copy, but only one transaction creates the job/Outbox.

Pagination orders by immutable `(created_at DESC, document_id DESC)` and encodes exactly those values plus cursor version `v1` in URL-safe base64; malformed, widened or over-512-character cursors return request validation rather than falling back to page one.

First move the existing `MetaData` and generic `outbox` `Table` unchanged into `tap.platform.db.schema`; both Chat and Knowledge adapters import it from there. Knowledge must never import `tap.modules.chat`. Update migration metadata registration and rerun the existing Turn/Relay integration tests to prove the extraction preserved behavior.

- [ ] **Step 5: Implement bounded upload/list/retry/delete behavior**

`DocumentService.upload()` must follow this exact settle path:

```python
staged = await artifacts.stage_original(upload, max_bytes=MAX_UPLOAD_BYTES)
reservation = await repository.reserve_upload(ReserveUpload.from_staged(staged))
if reservation.state is ReservationState.DUPLICATE_ACTIVE:
    await artifacts.discard_staged(staged)
    return accepted(reservation.document, duplicate=True)
try:
    original = await artifacts.commit_original(staged, reservation.revision_id)
    record = await repository.activate_upload(reservation, original)
except BaseException:
    cleanup = [artifacts.discard_staged(staged)]
    if reservation.state is ReservationState.OWNED:
        cleanup.append(repository.abandon_upload(reservation.reservation_id, reservation.owner_token))
    await settle_cleanup(*cleanup)
    raise
return accepted(record, duplicate=reservation.state is ReservationState.DUPLICATE_PENDING)
```

The artifact port computes size/SHA-256 while streaming and raises `document-too-large` before accepting byte `25 MiB + 1`. `retry_document` only accepts `failed` ingestion, clears the public error, increments attempt and starts at the first incomplete durable stage. `delete_document` transactionally sets `deleting`, makes the source unselectable, and emits a deletion job before returning `204`.

- [ ] **Step 6: Implement lease/checkpoint consistency and error redaction**

Every job update requires `(job_id, lease_token, expected_stage)` in the `WHERE` clause. A zero-row update raises `JobLeaseLost`; it must never silently checkpoint a stolen lease. Store only error codes from this set:

```python
SAFE_JOB_ERRORS = frozenset({
    "invalid-document", "ocr-required", "document-too-complex",
    "parser-unavailable", "embedding-unavailable", "embedding-dimension-mismatch",
    "index-unavailable", "index-reconciliation-failed", "artifact-unavailable",
})
```

Map each code to a fixed Chinese summary of at most 240 characters. Do not persist `str(provider_error)` in `knowledge_document`; detailed exception logs must be structured and exclude secrets and document content.

- [ ] **Step 7: Apply migration and verify GREEN**

```sh
TAP_ALEMBIC_DATABASE_URL="mysql+pymysql://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4" \
  uv run --project apps/backend alembic -c apps/backend/alembic.ini upgrade head
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/backend pytest \
  apps/backend/tests/integration/test_document_ledger.py \
  apps/backend/tests/integration/test_document_upload_recovery.py -v
uv run --project apps/backend pytest apps/backend/tests/unit/knowledge/test_document_service.py -v
make check
git diff --check
```

Expected: `alembic current` reports `0003_athena_documents`, duplicate/concurrency tests show one job and one outbox row, and reconstructing repository objects preserves all visible state.

- [ ] **Step 8: Commit the durable ledger**

```sh
git add apps/backend/migrations/versions/0003_athena_documents.py \
  apps/backend/migrations/env.py apps/backend/src/tap/platform/db/schema.py \
  apps/backend/src/tap/modules/knowledge apps/backend/src/tap/modules/chat/adapters/mysql.py \
  apps/backend/tests/unit/knowledge apps/backend/tests/integration \
  apps/backend/tests/architecture/test_module_boundaries.py
git commit -m "feat: persist knowledge document jobs"
```

### Task 4: Recoverable Ingestion and Deletion Worker

**Files:**
- Create: `apps/backend/src/tap/modules/knowledge/application/ingestion.py`
- Create: `apps/backend/src/tap/platform/messaging/redis_wakeup.py`
- Create: `apps/backend/src/tap/entrypoints/athena_ingestion_worker.py`
- Create: `apps/backend/tests/unit/knowledge/test_ingestion_worker.py`
- Create: `apps/backend/tests/integration/test_ingestion_recovery.py`
- Create: `apps/backend/tests/integration/test_ingestion_entrypoint.py`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/documents.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/mysql_documents.py`
- Modify: `apps/backend/src/tap/platform/messaging/redis_dispatch.py`

**Interfaces:**
- Consumes: parser/chunker, artifact/repository/embedding/index ports, generic Outbox dispatch and MySQL job leases.
- Produces: `IngestionWorker.run_once(limit: int) -> WorkerRun`, `DeletionWorker` branch within the same job runner, and optional Redis Stream wake-up with mandatory DB scan fallback.

- [ ] **Step 1: Write the stage-machine and recovery RED tests**

Drive the worker with real application objects and stateful fakes:

```python
@pytest.mark.asyncio
async def test_publish_failure_retries_from_embedding_artifact() -> None:
    index.fail_next_upsert = True
    first = await worker.run_once(limit=1)
    assert first.failed == 1
    assert repository.document.stage is IngestionStage.PUBLISHING
    assert embeddings.calls == 1

    await service.retry_document(str(repository.document.document_id))
    second = await worker.run_once(limit=1)
    assert second.ready == 1
    assert embeddings.calls == 1
    assert index.upsert_calls == 2


@pytest.mark.asyncio
async def test_worker_scans_mysql_when_redis_wakeup_is_lost() -> None:
    wakeups.drop_all = True
    await service.upload(markdown_upload("source.md", b"# Source\nPersistent fact"))
    assert (await worker.run_once(limit=10)).ready == 1
```

Add tests for parser, chunker, embedding and publication checkpoints; vector-dimension mismatch before index write; repeated `run_once` idempotence; expired lease takeover; cancellation settling provider calls; delete ordering; and a delete failure remaining `deleting` until automatic retry.

Add a fake-clock test that advances beyond 20 seconds during a long embedding batch and proves the active worker renews its 60-second lease; after an injected `JobLeaseLost`, it must stop writing artifacts/checkpoints and let the new owner continue.

- [ ] **Step 2: Run worker tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/knowledge/test_ingestion_worker.py \
  apps/backend/tests/integration/test_ingestion_recovery.py \
  apps/backend/tests/integration/test_ingestion_entrypoint.py -v
```

Expected: FAIL because no ingestion runner, Redis wake-up consumer or worker entrypoint exists.

- [ ] **Step 3: Implement one checkpointed stage per durable transition**

Use this closed transition table; never infer progress from the presence of a provider object alone:

```python
NEXT_STAGE = {
    IngestionStage.STORED: IngestionStage.PARSING,
    IngestionStage.PARSING: IngestionStage.CHUNKING,
    IngestionStage.CHUNKING: IngestionStage.EMBEDDING,
    IngestionStage.EMBEDDING: IngestionStage.PUBLISHING,
    IngestionStage.PUBLISHING: IngestionStage.READY,
}
```

Stage results are:

```text
stored      -> original locator already durable
parsing     -> normalized artifact locator + parser version
chunking    -> chunk artifact locator + complete manifest rows
embedding   -> embedding artifact locator + model alias + dimension
publishing  -> Milvus upsert/flush/count receipt matching manifest
ready       -> document status ready, chunk_count set, job completed
```

Write the artifact or provider projection first, verify it, then commit the corresponding MySQL checkpoint with the active lease token. Replaying a stage writes the same content-addressed locator/IDs.

- [ ] **Step 4: Implement bounded worker execution and failure taxonomy**

`run_once` claims at most `1..50` jobs with a 60-second lease, processes sequentially in the first implementation, and returns counts rather than looping forever:

```python
@dataclass(frozen=True, slots=True)
class WorkerRun:
    claimed: int
    ready: int
    deleted: int
    failed: int
    lease_lost: int


async def run_once(self, limit: int) -> WorkerRun:
    jobs = await self._repository.claim_jobs(
        worker_id=self._worker_id,
        now=self._clock.now(),
        lease_duration=timedelta(seconds=60),
        limit=limit,
    )
    return await self._process_claimed(jobs)
```

Provider timeouts map to the fixed safe error taxonomy. `asyncio.CancelledError` is re-raised only after the in-flight provider operation is settled and the lease is allowed to expire; it is not converted into a user failure.

Run a lease heartbeat at most every 20 seconds and before/after each embedding or index batch. Heartbeat updates require the exact lease token; `JobLeaseLost` cancels further stage work after settling the active SDK call.

- [ ] **Step 5: Implement deletion in the required order**

For `kind=delete`, keep public status `deleting`, then execute idempotently:

```text
1. delete all revision chunk IDs from Milvus and flush
2. negative-query/count probe confirms zero rows for source_id
3. delete original, normalized, chunks and embedding artifacts
4. delete manifest rows and mark job complete/document soft-deleted
```

If any step fails, retain enough locators/manifest facts for a retry. List/selection queries never return a deleting or soft-deleted document, and citations for it become stale immediately.

- [ ] **Step 6: Add Redis wake-up without making it authoritative**

Use the existing Outbox relay stream with a dedicated consumer group `athena-ingestion`. `RedisWakeupConsumer.wait(max_wait_seconds=1.0)` may shorten idle latency and ACK a Knowledge message only after a DB claim attempt; it ACKs unrelated aggregate types for this group without consuming them for other groups. The entrypoint must still call `run_once` once per second when Redis is empty, unavailable, reset, or contains a duplicate command. Stream payload supplies only `aggregateId`; all job details come from MySQL.

```python
wakeup = await wakeups.wait(max_wait_seconds=1.0)
run = await worker.run_once(limit=settings.job_batch_size)
if wakeup is not None:
    await wakeups.ack(wakeup)
```

- [ ] **Step 7: Verify stage recovery GREEN**

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/knowledge/test_ingestion_worker.py -v
TAP_RUN_MYSQL_INTEGRATION=1 TAP_RUN_REDIS_INTEGRATION=1 \
  uv run --project apps/backend pytest \
  apps/backend/tests/integration/test_ingestion_recovery.py \
  apps/backend/tests/integration/test_ingestion_entrypoint.py -v
make check
git diff --check
```

Expected: each injected failure exposes its exact stage, retries do not redo a completed expensive stage, and dropping all Redis messages still reaches `ready` from MySQL scanning.

- [ ] **Step 8: Commit the worker slice**

```sh
git add apps/backend/src/tap/modules/knowledge apps/backend/src/tap/platform/messaging \
  apps/backend/src/tap/entrypoints/athena_ingestion_worker.py \
  apps/backend/tests/unit/knowledge apps/backend/tests/integration
git commit -m "feat: add recoverable ingestion worker"
```

### Task 5: Real Azurite, LiteLLM Embedding, and Mutable Milvus Projection

**Files:**
- Create: `apps/backend/src/tap/modules/knowledge/adapters/blob_artifacts.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus_documents.py`
- Create: `apps/backend/src/tap/operations/milvus/doc_schema.py`
- Create: `apps/backend/tests/contract/test_blob_artifact_contract.py`
- Create: `apps/backend/tests/contract/test_document_index_contract.py`
- Create: `apps/backend/tests/integration/test_azurite_artifacts.py`
- Create: `apps/backend/tests/integration/test_athena_milvus_projection.py`
- Create: `apps/backend/tests/integration/test_athena_milvus_rebuild.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/milvus/transport.py`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/errors.py`
- Modify: `apps/backend/src/tap/operations/milvus/client.py`
- Modify: `apps/backend/src/tap/operations/milvus/contracts.py`
- Modify: `apps/backend/src/tap/operations/milvus/fixtures.py`
- Modify: `apps/backend/tests/unit/operations/test_milvus_fixtures.py`
- Modify: `apps/backend/tests/unit/operations/test_milvus_embeddings.py`
- Modify: `apps/backend/tests/contract/test_litellm_strict.py`
- Modify: `apps/backend/pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: artifact/index/embedding ports, canonical doc schema, existing strict `MilvusSearchAdapter` and `LiteLLMAdapter` transport rules.
- Produces: content-addressed `AzureBlobArtifactStore`, batch `LiteLLMAdapter.embed_many`, `MilvusDocumentIndex.ensure_target/upsert_revision/delete_revision/count_revision/rebuild`, and a reader-compatible Athena alias.

- [ ] **Step 1: Write provider contract RED tests**

Use the same conformance suite for fakes and real adapters:

```python
@pytest.mark.asyncio
async def test_embedding_artifact_round_trip_is_revision_scoped(store) -> None:
    locator = await store.put_embeddings(
        revision_id="rev_a", model="athena-embedding", dimension=3,
        batches=(EmbeddingBatch(("h_1",), ((0.1, 0.2, 0.3),)),),
    )
    restored = await store.read_embeddings(locator)
    assert restored.model == "athena-embedding"
    assert restored.vectors_by_chunk_id == {"h_1": (0.1, 0.2, 0.3)}


@pytest.mark.asyncio
async def test_upsert_read_back_and_delete_are_exact(index) -> None:
    receipt = await index.upsert_revision(revision, chunks, vectors)
    assert receipt.expected_rows == receipt.visible_rows == len(chunks)
    await index.delete_revision(revision.revision_id, tuple(c.chunk_id for c in chunks))
    assert await index.count_revision(revision.revision_id) == 0
```

Test malformed/missing artifact hashes, server-side copy failure, batch embeddings returned out of order, extra/missing vector rows, NaN/dimension mismatch, wrong alias target, stale corpus/model metadata, source-scope negative probes and rebuild parity.

- [ ] **Step 2: Run provider tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/contract/test_blob_artifact_contract.py \
  apps/backend/tests/contract/test_document_index_contract.py \
  apps/backend/tests/integration/test_azurite_artifacts.py \
  apps/backend/tests/integration/test_athena_milvus_projection.py -v
```

Expected: FAIL because the real artifact/index adapters and batch embedding operation do not exist.

- [ ] **Step 3: Implement content-addressed Azurite artifacts**

Pin `azure-storage-blob` with `uv add --project apps/backend --exact`. Use the async Blob client and exactly two containers:

```text
athena-originals
  staging/{upload_id}
  revisions/{revision_id}/{source_sha256_without_prefix}

athena-artifacts
  revisions/{revision_id}/normalized-v1.json
  revisions/{revision_id}/chunks-v1.jsonl.gz
  revisions/{revision_id}/embeddings/{model}/{dimension}-v1.jsonl.gz
```

Every artifact envelope contains `schemaVersion`, `revisionId`, `sourceContentHash`, `payloadSha256` and bounded counts. Serialize canonical JSON with sorted keys, UTF-8 and one newline; gzip uses `mtime=0`. On read, recompute both Blob content hash and envelope payload hash. `commit_original` generates a read-only staging-Blob SAS valid for at most five minutes, uses it only for server-side copy, waits for a terminal copy state under a deadline, verifies length/hash, and only then deletes staging. The SAS is never persisted, logged or returned. Add a bounded orphan scavenger for staging objects older than 24 hours and invisible reservations older than one hour. Fault-injection integration tests cover stage write, reservation commit, copy terminal failure, activation rollback and orphan cleanup; no failure window may expose a half-created document.

Create both containers with public access disabled. Locator values contain only container/blob identity; credentials and SAS tokens remain in the adapter and are redacted from `repr`, logs, errors and database columns.

- [ ] **Step 4: Add exact batch embeddings to the strict LiteLLM adapter**

Expose:

```python
async def embed_many(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
    """Embed 1..32 bounded texts on fixed alias athena-embedding."""
```

Send one `/v1/embeddings` request per batch of at most 32 texts, total request at most 262,144 bytes, model exactly `athena-embedding`, and dimension exactly the configured integer. Reorder results by the provider `index` field; reject duplicate/missing/out-of-range indices, non-finite values, wrong dimension, widened response fields and a returned model outside the fixed allowlist. Do not add provider-specific environment variables to application DTOs. Move `ModelUnavailable` to `tap.modules.knowledge.ports.errors` and import it from the LiteLLM adapter; keep a compatibility re-export if existing callers require it. HTTP mappings only catch the provider-neutral exception.

- [ ] **Step 5: Extract a reusable doc schema without weakening fixture trust**

Move canonical field/function/index construction into `tap.operations.milvus.doc_schema`:

```python
@dataclass(frozen=True, slots=True)
class DocCollectionMetadata:
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int


def build_doc_collection_schema(metadata: DocCollectionMetadata) -> dict[str, object]: ...
def doc_schema_sha256() -> str: ...
def doc_collection_description(metadata: DocCollectionMetadata) -> str: ...
```

Keep fixture-only manifest digest, exact 12-row identity checks and trusted corpus constants in `fixtures.py`. The existing digest must remain exactly `sha256:998b3ca8933a0ad33e61d2acc6b5aa629b10fa691f42860bbe3fe2074402c71f` after the extraction.

- [ ] **Step 6: Implement Athena's dedicated mutable Milvus target**

Use these exact local defaults, all overrideable only by server environment:

```text
physical collection: kb_doc_v1_athena_demo
reader alias:        kb_doc_athena_demo_active
schema version:      doc-schema-v1
corpus version:      athena-demo-v1
embedding model:     athena-embedding
vector dimension:   1536
tenant/project:      local / athena-demo
group/environment:   athena-local / global
classificationRank: 1
```

Create a reusable `PyMilvusDocProvisioner` from the fixture script's general schema builder; do not reuse the 2-dimensional health provisioner. Keep `tap.modules.knowledge.adapters.milvus.transport` as the only Knowledge file importing `pymilvus`; add bounded writer/query transport methods there and let `milvus_documents.py` depend only on their protocols. Grant `READER_TARGET_PRIVILEGES` to `tap_reader` and `WRITER_PRIVILEGES` to `tap_writer`. Unlike the immutable fixture publisher, keep effective writer access for this local-only mutable projection. Verify the writer has exactly the existing writer privilege set and no reader/provisioner/admin capability; do not falsely assert collection exclusivity while the accepted local bootstrap still uses its current collection-wildcard base grant. This exception does not change RFC-004 or the Azure enterprise baseline.

Map every row to the existing strict output schema, including `source_id=document_id`, `source_revision=revision_id`, `revision_kind=blob_version`, hashes, canonical anchor JSON and `physical_collection`. Upsert batches of at most 64, flush, then query by revision and compare the exact chunk ID/hash set before returning a receipt.

- [ ] **Step 7: Implement delete and full rebuild parity**

`delete_revision` deletes explicit chunk IDs in batches of at most 256, flushes, then queries `source_revision == <escaped revision>` and requires zero rows. `rebuild(records)` creates a new physical name `kb_doc_v1_athena_demo_<12 hex>`, writes every ready revision from artifact storage, verifies row/hash parity, grants the two scoped roles, atomically alters the alias, and only then retires the previous physical collection. Cancellation/failure restores the previous alias and leaves a cleanup report; it never points the alias at a partial collection.

```python
class DocumentIndex(Protocol):
    async def ensure_target(self) -> IndexTargetReceipt: ...
    async def upsert_revision(self, revision: RevisionRecord, chunks: tuple[ChunkDraft, ...], vectors: Mapping[ChunkId, tuple[float, ...]]) -> IndexReceipt: ...
    async def count_revision(self, revision_id: RevisionId) -> int: ...
    async def delete_revision(self, revision_id: RevisionId, chunk_ids: tuple[ChunkId, ...]) -> None: ...
    async def rebuild(self, records: tuple[ReadyRevisionArtifacts, ...]) -> RebuildReceipt: ...
```

- [ ] **Step 8: Run real provider GREEN tests**

```sh
TAP_RUN_AZURITE_INTEGRATION=1 uv run --project apps/backend pytest \
  apps/backend/tests/integration/test_azurite_artifacts.py -v
TAP_RUN_MILVUS_INTEGRATION=1 uv run --project apps/backend pytest \
  apps/backend/tests/integration/test_athena_milvus_projection.py \
  apps/backend/tests/integration/test_athena_milvus_rebuild.py -v
uv run --project apps/backend pytest \
  apps/backend/tests/contract/test_blob_artifact_contract.py \
  apps/backend/tests/contract/test_document_index_contract.py \
  apps/backend/tests/contract/test_litellm_strict.py \
  apps/backend/tests/unit/operations/test_milvus_fixtures.py \
  apps/backend/tests/unit/operations/test_milvus_embeddings.py -v
make check
git diff --check
```

Expected: real Azurite round-trips all artifact kinds; real Milvus proves upsert/filter/delete/rebuild; fixture digest and strict reader tests stay unchanged; the writer exposes only writer privilege kinds and no reader/provisioner/admin capability.

- [ ] **Step 9: Commit the real provider slice**

```sh
git add apps/backend/pyproject.toml uv.lock apps/backend/src/tap/modules/knowledge \
  apps/backend/src/tap/operations/milvus apps/backend/tests
git commit -m "feat: publish knowledge documents to milvus"
```

### Task 6: Grounded Answers, Fixed Demo Policy, and Citation Resolver

**Files:**
- Create: `apps/backend/src/tap/modules/knowledge/application/answers.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/citations.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/demo_policy.py`
- Create: `apps/backend/src/tap/interfaces/http/knowledge_service.py`
- Create: `apps/backend/tests/unit/knowledge/test_demo_policy.py`
- Create: `apps/backend/tests/unit/knowledge/test_answer_service.py`
- Create: `apps/backend/tests/unit/knowledge/test_citation_resolver.py`
- Create: `apps/backend/tests/integration/test_knowledge_answer_http.py`
- Create: `apps/backend/tests/integration/test_citation_snapshot_transaction.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/mysql_documents.py`
- Modify: `apps/backend/src/tap/interfaces/http/routes/knowledge_documents.py`
- Modify: `apps/backend/src/tap/interfaces/http/routes/knowledge_answers.py`
- Modify: `apps/backend/src/tap/interfaces/http/routes/citations.py`
- Modify: `apps/backend/src/tap/interfaces/http/app.py`
- Modify: `apps/backend/tests/architecture/test_module_boundaries.py`

**Interfaces:**
- Consumes: `KnowledgeAPI.answer()`, ready document revisions, `ResourceRef`, fixed local policy facts, answer/citation snapshot tables and artifact/index adapters.
- Produces: a concrete `KnowledgeHttpService`, atomic answer snapshot behavior, and bounded/stale-safe citation previews for the Web.

- [ ] **Step 1: Write Answer and Citation RED tests**

Assert the critical boundaries directly:

```python
@pytest.mark.asyncio
async def test_empty_selection_fails_before_search_or_model_io() -> None:
    with pytest.raises(AnswerSelectionRejected, match="source-selection-required"):
        await service.answer(AnswerRequest(query="What is the rule?", resource_refs=()))
    assert search.calls == 0
    assert model.calls == 0


@pytest.mark.asyncio
async def test_snapshot_failure_prevents_success_response() -> None:
    repository.fail_snapshot_commit = True
    with pytest.raises(AnswerSnapshotUnavailable):
        await service.answer(request_for("doc_a"))
    assert repository.answer_snapshots == ()


@pytest.mark.asyncio
async def test_deleted_document_makes_old_citation_stale() -> None:
    response = await service.answer(request_for("doc_a"))
    await documents.delete_document("doc_a")
    with pytest.raises(CitationStale):
        await citations.resolve(response.citations[0].citation_id)
```

Also test 21 sources, processing/failed/deleting sources, duplicate IDs, forged family/mode/revision/anchor/environment/corpus, delete-vs-answer race, selected revision changing before search, zero evidence, conflicting sources, malformed model labels, index/model `503`, prompt injection, citation hash/anchor/chunk tampering and preview bounds with Chinese/emoji text.

- [ ] **Step 2: Run answer/citation tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/knowledge/test_demo_policy.py \
  apps/backend/tests/unit/knowledge/test_answer_service.py \
  apps/backend/tests/unit/knowledge/test_citation_resolver.py \
  apps/backend/tests/integration/test_knowledge_answer_http.py \
  apps/backend/tests/integration/test_citation_snapshot_transaction.py -v
```

Expected: FAIL because selection orchestration, fixed policy and snapshots are absent.

- [ ] **Step 3: Construct the fixed policy through the existing authorization boundary**

Build verified facts and current project policy, then call `build_retrieval_policy_context`:

```python
DEMO_SUBJECT = VerifiedSubjectFacts(
    tenant_id="local", user_id="athena-local-user",
    group_ids=frozenset({"athena-local"}), roles=frozenset(), token_verified=True,
)


def project_policy_for(revisions: tuple[ReadyDocumentRevision, ...]) -> ProjectPolicy:
    grants = tuple(ResourceGrant(
        family="doc", source_id=str(item.document_id), revision_kind="blob_version",
        revision=str(item.revision_id), source_content_hash=item.source_content_hash,
        allow_all_anchors=True,
    ) for item in revisions)
    return ProjectPolicy(
        tenant_id="local", project_id="athena-demo", permission_granted=True,
        allowed_group_ids=frozenset({"athena-local"}),
        classification_ceiling=Classification.INTERNAL,
        allowed_environments=frozenset({"global"}),
        allowed_source_families=frozenset({"doc"}),
        active_corpus_version="athena-demo-v1",
        acl_digest=digest_grants(grants), policy_version="athena-demo-policy-v1",
        decision_id=digest_decision(grants), resource_grants=grants,
    )
```

`DemoCurrentPolicyVerifier.verify_current(expected)` reloads every expected document/revision from MySQL and reconstructs this policy. If a source is no longer ready/current, it raises `AuthorizationDenied`; if MySQL cannot establish current state, it raises `PolicyUnavailable`. It never silently returns the stale expected context or treats state loss as zero retrieval.

- [ ] **Step 4: Normalize browser selection before calling KnowledgeAPI**

The HTTP mapper first converts `RetrievalAnswerRequest` to the framework-free domain `AnswerRequest`; the application layer never constructs Pydantic DTOs or calls `answer_request_from_http`. `AnswerService.answer(request: AnswerRequest) -> AnswerResponse` receives a structurally typed gateway that the composition root must satisfy with the existing concrete `KnowledgeAPI`.

Require `1..20` unique refs. Accept only `family=doc`, `mode=scope`, a document ID and no browser revision or anchor. Reject any non-null `top_k`, requested environment/corpus, non-quick answer mode, or non-doc source family instead of silently clamping/ignoring a hidden tuning control. Reload all rows in one locked/current-revision query and create a fresh domain request:

```python
trusted = AnswerRequest(
    query=request.query,
    answer_mode=AnswerMode.QUICK,
    source_families=(SourceFamily.DOC,),
    resource_refs=tuple(
        ResourceRef(family=SourceFamily.DOC, source_id=str(row.document_id), mode=ResourceMode.SCOPE)
        for row in ready_rows
    ),
)
response = await knowledge_gateway.answer(trusted, policy)
```

Empty selection never becomes global doc scope. Re-check the same revision/hash set immediately before snapshot commit. `interfaces/http/knowledge_service.py` owns DTO/domain mapping and uses the existing `answer_response_to_http`; routes do not import adapters.

Consume the exact answer spans already guaranteed by Task 1; `AnswerService` must preserve them unchanged while replacing only the resource selection and persisting snapshots.

- [ ] **Step 5: Commit Answer and Citation snapshots atomically**

After `KnowledgeAPI.answer()` has validated claims/citations, open one MySQL transaction that inserts one `knowledge_answer_snapshot` and exactly the returned citation set. Require every non-abstained claim to reference at least one inserted citation and reject more than 20 citations. `selected_revisions_json` is a sorted closed array of `{documentId, revisionId, sourceContentHash}`. If the transaction fails, return RFC 9457 `503 answer-snapshot-unavailable`; do not return the generated answer.

```python
snapshot = AnswerSnapshot.from_response(
    response=response,
    query_hash=canonical_sha256(trusted.query.encode("utf-8")),
    selected_revisions=tuple(sorted(ready_rows, key=lambda row: row.document_id)),
)
await repository.save_answer_with_citations(snapshot)  # one transaction
return response
```

Within the same transaction, delete answer snapshots older than the newest 1,000 and cascade their citation snapshots. This is bounded resolver state, not Conversation history; no list/recovery API is added.

Abstentions persist an answer snapshot plus the actual citation set returned by `KnowledgeAPI` (conflict/revision abstentions may retain evidence citations). The UI receives the existing structured abstention and must not invent a claim, but every returned citation remains resolvable.

- [ ] **Step 6: Resolve a citation from immutable facts**

`CitationResolver.resolve(citation_id)` performs these checks in order:

```text
snapshot exists and belongs to its answer snapshot
document exists and is not deleting/deleted
snapshot revision equals selected revision and document current revision
source hash equals revision and normalized/chunk artifact envelope
manifest contains the exact chunk ID/hash/anchor
chunk artifact content recomputes the same chunk hash
```

Return the chunk text as `quote` capped at 4,000 Unicode code points, plus at most 500 code points before/after from the normalized artifact. Offset slicing operates on Python Unicode code points and tests Chinese/emoji boundaries. Any failed check maps to `404 citation-stale`; provider outage maps to `503 citation-unavailable`. Never search for a nearby substring.

- [ ] **Step 7: Wire the concrete HTTP service and stable problems**

Implement upload/list/detail/retry/delete/answer/citation handlers through the service and add stable Problem Details mappings:

```text
400 unsupported-document, empty-document, source-selection-required,
    unsupported-answer-control
404 document-not-found, citation-stale
409 document-not-retryable, document-state-changed
413 document-too-large
422 request-validation
429 document-limit-reached
503 knowledge-runtime-unavailable, embedding-unavailable,
    search-unavailable, answer-snapshot-unavailable, citation-unavailable
```

The upload POST cannot return `ocr-required` synchronously; the worker records it as a `failed` document at stage `parsing`, and list/detail expose only the safe code/summary. Provider model failures map from the provider-neutral `ModelUnavailable`, never the concrete LiteLLM exception class.

- [ ] **Step 8: Verify Answer/Citation GREEN and provider neutrality**

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/knowledge -v
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/backend pytest \
  apps/backend/tests/integration/test_knowledge_answer_http.py \
  apps/backend/tests/integration/test_citation_snapshot_transaction.py -v
uv run --project apps/backend pytest \
  apps/backend/tests/contract/test_knowledge_api.py \
  apps/backend/tests/architecture/test_module_boundaries.py -v
make contracts
make check
git diff --check
```

Expected: source exclusion is zero at hit/claim/citation layers, empty selection causes no Search I/O, snapshot failure returns no answer, delete/answer races fail closed, and no new application/HTTP file branches on `milvus` or a raw model provider.

- [ ] **Step 9: Commit the grounded-answer slice**

```sh
git add apps/backend/src/tap/modules/knowledge apps/backend/src/tap/interfaces/http \
  apps/backend/tests apps/backend/src/tap/contracts/http.py contracts/openapi/api.json \
  apps/web/src/shared/api/generated/schema.ts
git commit -m "feat: answer from selected knowledge sources"
```

### Task 7: Athena App Shell and Knowledge Library

**Files:**
- Create: `apps/web/index.html`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/src/app/main.tsx`
- Create: `apps/web/src/app/App.tsx`
- Create: `apps/web/src/app/providers.tsx`
- Create: `apps/web/src/app/theme.ts`
- Create: `apps/web/src/app/styles.css`
- Create: `apps/web/src/pages/AthenaPage.tsx`
- Create: `apps/web/src/features/knowledge/api/client.ts`
- Create: `apps/web/src/features/knowledge/api/types.ts`
- Create: `apps/web/src/features/knowledge/api/queries.ts`
- Create: `apps/web/src/features/knowledge/components/KnowledgeLibrary.tsx`
- Create: `apps/web/src/features/knowledge/components/DocumentTable.tsx`
- Create: `apps/web/src/features/knowledge/components/DocumentDetail.tsx`
- Create: `apps/web/src/features/knowledge/components/UploadDialog.tsx`
- Create: `apps/web/src/features/knowledge/components/DocumentStatus.tsx`
- Create: `apps/web/src/features/knowledge/components/KnowledgeLibrary.test.tsx`
- Create: `apps/web/src/shared/testing/renderApp.tsx`
- Create: `apps/web/src/shared/testing/fakeKnowledgeClient.ts`
- Create: `apps/web/dependency-cruiser.cjs`
- Modify: `apps/web/package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `Makefile`

**Interfaces:**
- Consumes: generated OpenAPI types and document endpoints from Tasks 1/6.
- Produces: runnable Athena shell with `问答/知识库` navigation, real knowledge library/upload/detail/retry/delete behavior, query hooks and a typed `KnowledgeClient` reused by the workspace.

- [ ] **Step 1: Install exact compatible Web dependencies**

Use `pnpm add -E` so `package.json` and lockfile contain no ranges. Pin this compatible set:

```text
runtime: react 19.2.8, react-dom 19.2.8, antd 6.6.1,
         @ant-design/icons 6.3.2, @tanstack/react-query 5.102.7,
         openapi-fetch 0.17.0, react-markdown 10.1.0, rehype-sanitize 6.0.0
dev:     vite 8.2.2, @vitejs/plugin-react 6.1.0, vitest 4.1.11,
         typescript 5.9.3, openapi-typescript 7.13.0,
         tailwindcss 4.3.3, @tailwindcss/vite 4.3.3,
         @testing-library/react 16.3.2, @testing-library/dom 10.4.1,
         @testing-library/user-event 14.6.6, @testing-library/jest-dom 7.0.1,
         jsdom 29.1.1, eslint 10.9.1, typescript-eslint 8.68.0,
         prettier 3.9.6, dependency-cruiser 18.2.0
```

Do not upgrade TypeScript to 7 or jsdom to 30: those versions are outside the checked Node/openapi-typescript compatibility matrix.

- [ ] **Step 2: Write the knowledge-library RED component tests**

Use a real QueryClient and an injected stateful fake client:

```tsx
it('polls only while a document is non-terminal and enables retry on failure', async () => {
  const api = fakeKnowledgeClient()
    .listOnce([document({ status: 'processing', stage: 'embedding' })])
    .listOnce([document({ status: 'failed', stage: 'embedding', errorCode: 'embedding-unavailable' })]);
  renderApp(<KnowledgeLibrary />, { api });
  expect(await screen.findByText('正在生成向量')).toBeVisible();
  await advancePollingClock(2000);
  expect(await screen.findByRole('button', { name: '重试' })).toBeEnabled();
  expect(api.listCalls).toBe(2);
});


it('keeps a completed upload visible after closing the upload dialog', async () => {
  const api = fakeKnowledgeClient();
  renderApp(<KnowledgeLibrary />, { api });
  await userEvent.upload(screen.getByLabelText('选择文档'), markdownFile('policy.md'));
  await userEvent.click(screen.getByRole('button', { name: '开始添加' }));
  expect(await screen.findByText('policy.md')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
```

Add tests for empty state, four accepted extensions, 25 MiB client hint without treating it as authority, row focus/detail, six-stage timeline, failed safe summary, duplicate response, delete confirmation, delete row becoming unavailable, keyboard focus restoration and server Problem Details rendering.

- [ ] **Step 3: Run Web tests and confirm RED**

```sh
corepack pnpm --filter @tap/web test -- KnowledgeLibrary.test.tsx --run
```

Expected: FAIL because the app, client, providers and knowledge-library components do not exist.

- [ ] **Step 4: Build the typed API client and query policy**

Create one `KnowledgeClient` interface and an `openapi-fetch` implementation:

```ts
export interface KnowledgeClient {
  listDocuments(input: { cursor?: string; limit: number }): Promise<DocumentPage>;
  getDocument(documentId: string): Promise<DocumentDetail>;
  uploadDocument(file: File, onProgress: (ratio: number) => void): Promise<DocumentAccepted>;
  retryDocument(documentId: string): Promise<DocumentAccepted>;
  deleteDocument(documentId: string): Promise<void>;
  createAnswer(request: RetrievalAnswerRequest): Promise<RetrievalAnswerResponse>;
  getCitation(citationId: string): Promise<CitationPreview>;
}
```

Use generated `components`/`paths` types; never duplicate public response shapes by hand. Upload alone may use `XMLHttpRequest` for progress, but it must parse the same Problem Details and abort on component unmount. Document list polling is `2_000 ms` only when at least one item is `queued | processing | deleting`; otherwise `refetchInterval=false`.

- [ ] **Step 5: Implement the benchmarked Athena shell**

Register `@tailwindcss/vite` in Vite. Use Ant Design for form/table/modal/timeline semantics and centralized theme tokens; use Tailwind for layout, spacing and responsive composition, with a restrained neutral palette, 14px base type and clear focus rings. The header shows `Athena` and `Athena Lab`, with only `问答` and `知识库` tabs. The knowledge library follows the approved RAGFlow/Dify-inspired operational pattern:

```text
toolbar: title + ready/processing/failed counts + 添加来源
table:   文件名 | 类型 | 状态/阶段 | chunks | 更新时间 | 操作
detail:  immutable revision/hash summary + six-stage timeline + normalized preview
```

Do not add workspace selectors, model selectors, chunk sliders, Agent controls or admin settings. Keep all product copy in `src/features/knowledge/copy.ts` so error/status language stays consistent.

- [ ] **Step 6: Implement upload, retry and delete mutations**

The upload dialog accepts `.pdf,.docx,.md,.markdown,.txt`, supports drag/drop and one file per request, shows client upload progress, then closes after the `202` receipt and invalidates the list. Background ingestion continues after closing or tab changes. Retry invalidates list/detail without resetting the row; delete first marks the row unavailable, requests confirmation containing the filename, then removes it from all selectable query data after `204`.

```ts
const uploadMutation = useMutation({
  mutationFn: ({ file, onProgress }: UploadCommand) => api.uploadDocument(file, onProgress),
  onSuccess: async () => {
    closeUploadDialog();
    await queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents() });
  },
});
```

- [ ] **Step 7: Add frontend dependency and quality gates**

Configure dependency-cruiser to allow only `app/pages → widgets → features → shared`, plus imports within one layer; fail any `feature → feature` dependency. Add scripts:

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build",
    "test": "vitest",
    "lint": "eslint .",
    "format:check": "prettier --check .",
    "architecture": "depcruise src --config dependency-cruiser.cjs",
    "check": "pnpm run lint && pnpm run format:check && pnpm run architecture && pnpm run build"
  }
}
```

Wire root `make check` and `make test` to the Web checks without removing Backend gates.

- [ ] **Step 8: Verify knowledge-library GREEN**

```sh
corepack pnpm --filter @tap/web test -- --run
corepack pnpm --filter @tap/web run check
make contracts
make check
git diff --check
```

Expected: component tests pass without React act warnings, generated types remain clean, the production bundle builds, and the library has no nonterminal polling after all rows settle.

- [ ] **Step 9: Commit the knowledge library**

```sh
git add apps/web pnpm-lock.yaml Makefile
git commit -m "feat: add athena knowledge library"
```

### Task 8: Source-First Question Workspace and Citation Viewer

**Files:**
- Create: `apps/web/src/widgets/athena/AthenaWorkspace.tsx`
- Create: `apps/web/src/features/knowledge/components/SourcesPanel.tsx`
- Create: `apps/web/src/features/knowledge/components/QuestionComposer.tsx`
- Create: `apps/web/src/features/knowledge/components/GroundedAnswer.tsx`
- Create: `apps/web/src/features/knowledge/components/CitationViewer.tsx`
- Create: `apps/web/src/features/knowledge/model/sourceSelection.ts`
- Create: `apps/web/src/features/knowledge/components/AthenaWorkspace.test.tsx`
- Create: `apps/web/src/features/knowledge/components/MarkdownSafety.test.tsx`
- Modify: `apps/web/src/pages/AthenaPage.tsx`
- Modify: `apps/web/src/features/knowledge/api/queries.ts`
- Modify: `apps/web/src/app/styles.css`

**Interfaces:**
- Consumes: `KnowledgeClient`, document list, `RetrievalAnswerResponse.claims/citations`, and `CitationPreview`.
- Produces: NotebookLM-inspired source selection, one-shot grounded Q&A, inline citation actions, current-answer citation viewer, sanitization and responsive three-panel UX.

- [ ] **Step 1: Write workspace state RED tests**

Test behavior rather than component internals:

```tsx
it('removes a selected source when it stops being ready', async () => {
  const api = fakeKnowledgeClient().withDocuments([
    document({ documentId: 'doc_a', status: 'ready' }),
    document({ documentId: 'doc_b', status: 'ready' }),
  ]);
  renderApp(<AthenaWorkspace />, { api });
  await userEvent.click(await screen.findByRole('checkbox', { name: /doc_a/ }));
  api.replaceDocument('doc_a', document({ documentId: 'doc_a', status: 'deleting' }));
  api.emitListUpdate();
  expect(screen.getByRole('checkbox', { name: /doc_a/ })).toBeDisabled();
  expect(screen.getByText('已选择 0 个来源')).toBeVisible();
});


it('opens only an internal citation preview and clears it for a new question', async () => {
  renderApp(<AthenaWorkspace />, { api: answeredFake() });
  await ask('退款需要几人审批？');
  await userEvent.click(await screen.findByRole('button', { name: '引用 1' }));
  expect(await screen.findByText('原文依据')).toBeVisible();
  await ask('额度是多少？');
  expect(screen.queryByText('原文依据')).not.toBeInTheDocument();
});
```

Add tests for non-ready disabled, select-all-ready capped at 20, no-source composer disabled, selection change clearing answer/viewer, exact `scope` ResourceRefs, abstention, provider `503`, query double-submit prevention, claim-local citation buttons, narrow-screen DOM order, and focus returning to the clicked citation after viewer closes.

While the single JSON request is pending, assert the center panel shows both `检索所选来源` and `组织可核验回答` as indeterminate work, without inventing a completed backend stage or streaming answer text.

- [ ] **Step 2: Write Markdown/XSS RED tests**

Feed a malicious model response and require inert rendering:

```tsx
it('renders model links as text and strips executable markup', () => {
  render(<GroundedAnswer response={answerWith(
    '<img src=x onerror=alert(1)> [steal](https://evil.example) <script>alert(2)</script>'
  )} />);
  expect(document.querySelector('script,img')).toBeNull();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.getByText('steal')).toBeVisible();
});
```

Also test `javascript:`, data URLs, SVG, iframe, style attributes, raw HTML, oversized code blocks and prompt-injection text. Only citation IDs already present in the response can trigger `getCitation`.

- [ ] **Step 3: Run workspace tests and confirm RED**

```sh
corepack pnpm --filter @tap/web test -- AthenaWorkspace.test.tsx MarkdownSafety.test.tsx --run
```

Expected: FAIL because the workspace, selection model and safe answer renderer do not exist.

- [ ] **Step 4: Implement ready-only source selection**

Use a reducer with events `snapshotChanged`, `toggle`, `selectAllReady`, `clear`, `questionSubmitted`. Its state contains only document IDs from the latest server snapshot, never cached revisions. `selectAllReady` chooses the first 20 sorted ready IDs. `buildAnswerRequest` emits exactly:

```ts
{
  query,
  answerMode: 'quick',
  sources: ['doc'],
  resourceRefs: selectedIds.map(sourceId => ({ family: 'doc', sourceId, mode: 'scope' }))
}
```

It emits no environment, corpus, revision, anchor or topK. A state snapshot with zero selections disables submit rather than sending an empty array.

- [ ] **Step 5: Build the approved three-panel workspace**

Desktop grid is `280px minmax(420px, 1fr) 360px`; left is searchable ready/processing/failed sources with checkboxes, center is question/composer/answer, right is citation preview. Use the approved source-first interaction influenced by NotebookLM and the operational status vocabulary from RAGFlow/Dify, while retaining Athena colors/copy.

At widths below 1,024px, keep DOM and reading order `来源 → 问答 → 原文`; source and citation panels become full-width sections, not tiny columns. The composer stays attached to the answer section and maintains at least 44px touch targets.

During the non-streaming answer mutation, render one accessible `aria-live="polite"` pending block containing both `检索所选来源` and `组织可核验回答`; do not mark either server phase complete or reveal partial model output before the response arrives.

```tsx
<main className="grid grid-cols-[280px_minmax(420px,1fr)_360px] max-[1023px]:grid-cols-1">
  <SourcesPanel className="max-[1023px]:order-1" />
  <QuestionPane className="max-[1023px]:order-2" />
  <CitationViewer className="max-[1023px]:order-3" />
</main>
```

- [ ] **Step 6: Render claims with truly inline citation actions**

Use the public `claim.answerStart` / `claim.answerEnd` Unicode code-point offsets and complete-paragraph boundaries validated in Task 1 and preserved by Task 6; never rediscover positions with regex or fuzzy matching. Convert with `Array.from(answer)` because JavaScript string indices are UTF-16 code units, then split only at those paragraph boundaries, render each safe Markdown fragment, and place the claim's citation buttons immediately after its paragraph. Revalidate the exact text and boundaries; if the graph is invalid, show a controlled answer-format error rather than attaching citations to the wrong sentence.

Clicking a citation fetches `/v1/citations/{id}`, shows filename, revision hash, heading path/page and highlighted quote/prefix/suffix, and records no external URL. New question submission or any source selection change clears answer, citation query and viewer.

```ts
const answerCodePoints = Array.from(response.answer);
for (const claim of [...response.claims].sort((a, b) => a.answerStart - b.answerStart)) {
  if (answerCodePoints.slice(claim.answerStart, claim.answerEnd).join('') !== claim.text) {
    throw new AnswerFormatError();
  }
  segments.push(textBefore(claim), citedClaim(claim));
}
```

- [ ] **Step 7: Apply a strict Markdown allowlist**

Use `react-markdown` + `rehype-sanitize` with only `p, h1, h2, h3, h4, ul, ol, li, blockquote, pre, code, strong, em, table, thead, tbody, tr, th, td, hr`. Disable raw HTML. Override `a` to return a `<span>` containing its children and no href. Do not pass through `class`, `style`, event attributes, images, iframes, SVG or arbitrary data attributes. Citation buttons are React controls built from trusted response IDs, never Markdown nodes.

```tsx
<ReactMarkdown
  rehypePlugins={[[rehypeSanitize, answerSchema]]}
  components={{ a: ({ children }) => <span>{children}</span> }}
>
  {markdown}
</ReactMarkdown>
```

- [ ] **Step 8: Verify workspace GREEN and accessibility**

```sh
corepack pnpm --filter @tap/web test -- --run
corepack pnpm --filter @tap/web run check
corepack pnpm --filter @tap/web run build
make check
git diff --check
```

Expected: all source-state and XSS tests pass, no external link is interactive, tab order matches visual order, narrow layout does not overflow at 390px, and every visible answer claim has at least one adjacent citation action or is an explicit abstention.

- [ ] **Step 9: Commit the source-first workspace**

```sh
git add apps/web/src apps/web/package.json pnpm-lock.yaml
git commit -m "feat: add athena grounded question workspace"
```

### Task 9: Local Runtime, Stable Make Commands, and Playwright Journey

**Files:**
- Create: `apps/backend/src/tap/entrypoints/athena_api.py`
- Create: `apps/backend/src/tap/entrypoints/athena_runtime.py`
- Create: `apps/backend/src/tap/testing/deterministic_model.py`
- Create: `apps/backend/src/tap/testing/failure_injection.py`
- Create: `apps/backend/tests/unit/entrypoints/test_athena_runtime.py`
- Create: `apps/backend/tests/contract/test_demo_commands.py`
- Create: `apps/backend/tests/integration/test_athena_persistence_restart.py`
- Create: `scripts/check-athena-demo.py`
- Create: `scripts/run-athena-dev.sh`
- Create: `scripts/run-athena-e2e.sh`
- Create: `scripts/athena_collection.py`
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/tests/e2e/fixtureBuilder.ts`
- Create: `apps/web/tests/e2e/athena.spec.ts`
- Create: `apps/web/tests/e2e/persistence.spec.ts`
- Modify: `apps/backend/pyproject.toml`
- Modify: `apps/backend/src/tap/interfaces/http/app.py`
- Modify: `apps/web/vite.config.ts`
- Modify: `apps/web/package.json`
- Modify: `compose.yaml`
- Modify: `deploy/local/litellm/config.yaml`
- Modify: `.env.example`
- Modify: `Makefile`
- Modify: `pnpm-lock.yaml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: all application services/adapters, existing Compose services and local Milvus role bootstrap.
- Produces: `make demo-up/demo-dev/demo-check/demo-e2e/demo-down`, real LiteLLM aliases, loopback-only API/Web, deterministic E2E mode and a repeatable persistence/failure user journey.

- [ ] **Step 1: Write runtime-command RED tests**

Assert resolved behavior, not only text presence:

```python
def test_demo_down_never_removes_named_volumes() -> None:
    recipe = make_recipe("demo-down")
    assert " down " in f" {recipe} "
    assert "-v" not in recipe
    assert "--volumes" not in recipe


def test_runtime_rejects_non_loopback_bind_without_auth() -> None:
    with pytest.raises(ValueError, match="loopback"):
        AthenaSettings.from_mapping(valid_settings() | {"ATHENA_API_HOST": "0.0.0.0"})


def test_litellm_exposes_only_fixed_athena_aliases_for_demo() -> None:
    config = yaml.safe_load(Path("deploy/local/litellm/config.yaml").read_text())
    assert {item["model_name"] for item in config["model_list"]} >= {
        "athena-chat", "athena-embedding"
    }
```

Add tests for all five Make targets, fixed/validated Compose project names, named volumes, Vite proxy only `/v1` and `/health`, no wildcard CORS, required migration, alias/model/dimension settings, missing credentials producing actionable preflight failure, fake model forbidden outside `TAP_DEMO_MODE=e2e`, and E2E cleanup restricted to exact project `tap-athena-e2e`.

- [ ] **Step 2: Run runtime tests and confirm RED**

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/entrypoints/test_athena_runtime.py \
  apps/backend/tests/contract/test_demo_commands.py -v
```

Expected: FAIL because the settings/composition root, scripts and commands do not exist and LiteLLM has only `default-chat`.

- [ ] **Step 3: Configure the two fixed LiteLLM routes**

Keep application aliases fixed while reading raw provider values only from environment:

```yaml
model_list:
  - model_name: athena-chat
    litellm_params:
      model: os.environ/LITELLM_MODEL
      api_key: os.environ/OPENAI_API_KEY
  - model_name: athena-embedding
    litellm_params:
      model: os.environ/LITELLM_EMBEDDING_MODEL
      api_key: os.environ/LITELLM_EMBEDDING_API_KEY
      api_base: os.environ/LITELLM_EMBEDDING_API_BASE
```

Pass these variables through Compose but never print their values. Update the existing embedding-configuration contract test to distinguish the earlier paid fixture-research path from the newly accepted Athena runtime path; it must still reject credentials in committed files and public contracts. Pin `uvicorn` and any remaining runtime dependencies exactly.

- [ ] **Step 4: Build a strict composition root and health checks**

`AthenaSettings.from_mapping()` validates all values before constructing clients, including:

```text
ATHENA_API_HOST=127.0.0.1
ATHENA_API_PORT=8000
ATHENA_WEB_HOST=127.0.0.1
ATHENA_WEB_PORT=5173
ATHENA_MODEL_BACKEND=litellm
ATHENA_EMBEDDING_DIMENSION=1536
ATHENA_POLL_SECONDS=1
ATHENA_JOB_BATCH_SIZE=10
ATHENA_COLLECTION=kb_doc_v1_athena_demo
ATHENA_ALIAS=kb_doc_athena_demo_active
ATHENA_CORPUS_VERSION=athena-demo-v1
```

`/health/live` returns `200` after the event loop starts and performs no external I/O. `/health/ready` checks migration head, artifact containers, Redis ping, Milvus alias metadata and LiteLLM model inventory under individual deadlines; it returns one closed component status per dependency and no endpoint/credential.

- [ ] **Step 5: Add stable Make command semantics**

Implement these exact flows with default project `tap-athena-demo`:

```make
demo-up:
	docker compose -p "$(TAP_ATHENA_COMPOSE_PROJECT)" --profile milvus up -d --wait --wait-timeout 180
	TAP_ALEMBIC_DATABASE_URL="$${TAP_ALEMBIC_DATABASE_URL:-mysql+pymysql://tap:tap@127.0.0.1:3306/tap?charset=utf8mb4}" \
		uv run --project apps/backend alembic -c apps/backend/alembic.ini upgrade head
	TAP_ALLOW_INITIAL_MILVUS_ROOT=1 uv run --project apps/backend python scripts/milvus_bootstrap.py
	uv run --project apps/backend python scripts/athena_collection.py ensure

demo-check:
	uv run --project apps/backend python scripts/check-athena-demo.py

demo-dev:
	bash scripts/run-athena-dev.sh

demo-e2e:
	bash scripts/run-athena-e2e.sh

demo-down:
	docker compose -p "$(TAP_ATHENA_COMPOSE_PROJECT)" --profile milvus down --remove-orphans
```

Before any command, validate the project name against `^[a-z0-9][a-z0-9_-]{2,62}$`. Add `demo-reset` separately; it requires both exact project `tap-athena-demo` and `TAP_ALLOW_ATHENA_VOLUME_RESET=1` before `down -v`. Ordinary commands never call reset.

- [ ] **Step 6: Implement preflight and four-process supervisor**

`check-athena-demo.py` performs real MySQL `SELECT 1` and migration-head query, Redis `PING`, Azurite write/read/delete canary, Milvus alias/schema/model/dimension query, and LiteLLM `/v1/models` membership for both aliases. It reports only component + `ok|failed` + safe remediation; provider calls are reserved for the explicit real smoke.

`run-athena-dev.sh` loads ignored `.env` with `set -a`, starts API, existing Relay, ingestion worker and Vite, records each PID separately, waits for readiness, and on INT/TERM stops each child by PID then waits. It must not use `kill 0`. API and Vite bind loopback; Vite proxies `/v1` and `/health` to the API and no application CORS middleware is enabled.

```sh
api_pid=""; relay_pid=""; worker_pid=""; web_pid=""
cleanup() {
  for child_pid in "$web_pid" "$worker_pid" "$relay_pid" "$api_pid"; do
    [ -z "$child_pid" ] || kill "$child_pid" 2>/dev/null || true
  done
  wait || true
}
trap cleanup EXIT INT TERM
```

- [ ] **Step 7: Add deterministic E2E model and fail-once hooks**

The fake backend is available only when both `ATHENA_MODEL_BACKEND=fake` and `TAP_DEMO_MODE=e2e`. Its embedding is a stable normalized 1,536-dimensional hashed-token vector; its answer copies exact evidence sentences into `answer` and `claims`, with the current evidence labels. It makes no network call and is never the `demo-dev` default.

```python
def deterministic_vector(text: str, dimension: int = 1536) -> tuple[float, ...]:
    values = [0.0] * dimension
    for token in normalized_tokens(text):
        values[int.from_bytes(sha256(token.encode()).digest()[:4], "big") % dimension] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)
```

Failure hooks are registered only in E2E mode and outside OpenAPI. They support one-shot `parsing`, `embedding`, or `publishing` failure for the next job and expose no arbitrary exception text. Production/local LiteLLM mode cannot import or enable them through a browser request.

- [ ] **Step 8: Build the Playwright fixtures and full journey**

Pin `@playwright/test@1.62.1`, `pdf-lib@1.17.1` and `docx@9.5.1` with `pnpm add -DE`. `fixtureBuilder.ts` generates PDF, DOCX, Markdown and TXT files at test runtime with the same fictional policy facts and one malicious prompt-injection source. Run Playwright serially against an isolated Compose project `tap-athena-e2e` with offset ports.

Install the pinned Chromium runtime with `corepack pnpm --filter @tap/web exec playwright install chromium`; `demo-e2e` checks for it and prints that exact remediation instead of downloading silently during a test.

The tests perform this exact sequence:

```text
upload all four formats -> observe each ready and stage timeline
upload renamed duplicate -> same document identity and counts
reload browser -> all rows remain
select two sources -> ask -> every rendered claim has inline citation
click citation -> same revision/hash/anchor quote is highlighted
deselect one source -> ask again -> excluded source absent from claims/citations
inject embedding fail-once -> failed at embedding -> retry -> ready
upload prompt injection -> answer scope/model/tool behavior unchanged
delete one source -> row unselectable immediately -> subsequent answer has zero source hits
stop API/worker/Web -> restart those processes -> documents and citations still resolve
demo-down -> demo-up without -v -> persisted document list remains
```

`run-athena-e2e.sh` validates project name exactly, creates fresh test volumes, runs the journey, and removes only `tap-athena-e2e` volumes in an EXIT trap. It never targets the user's `tap-athena-demo` project.

- [ ] **Step 9: Verify local runtime GREEN**

```sh
docker compose config
make demo-up
make demo-check
uv run --project apps/backend pytest \
  apps/backend/tests/unit/entrypoints/test_athena_runtime.py \
  apps/backend/tests/contract/test_demo_commands.py \
  apps/backend/tests/integration/test_athena_persistence_restart.py -v
make demo-e2e
make demo-down
make demo-up
make demo-check
make demo-down
git diff --check
```

Expected: middleware and application restarts retain documents; E2E uses real MySQL/Redis/Azurite/Milvus plus deterministic model; ordinary down/up never removes named volumes; both aliases are visible and the application remains loopback-only.

- [ ] **Step 10: Commit the runnable Demo**

```sh
git add apps/backend apps/web compose.yaml deploy/local/litellm .env.example \
  Makefile scripts pnpm-lock.yaml uv.lock
git commit -m "feat: run athena knowledge demo locally"
```

### Task 10: Full Acceptance, Real-Model Smoke, and Documentation Lifecycle

**Files:**
- Create: `apps/backend/tests/smoke/test_athena_real_model.py`
- Create: `docs/reviews/2026-08-27-athena-local-knowledge-demo.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture/2026-08-20-overview.md`
- Modify: `docs/architecture/2026-08-21-knowledge-chat-ui.md`
- Modify: `docs/architecture/rag/2026-08-21-foundation.md`
- Modify: `docs/reference/2026-08-20-contracts.md`
- Modify: `docs/plans/2026-08-20-roadmap.md`
- Modify: `docs/plans/2026-08-23-phase-1-application-implementation.md`
- Modify: `docs/plans/2026-08-27-athena-local-knowledge-demo.md`
- Modify: `docs/plans/index.md`
- Modify: `docs/proposals/2026-08-27-rfc-005-athena-local-knowledge-demo.md`
- Modify: `docs/proposals/index.md`
- Modify: `docs/reviews/index.md`

**Interfaces:**
- Consumes: completed runnable vertical slice and every automated gate from Tasks 1–9.
- Produces: evidence-backed acceptance report, current README/AGENTS/architecture/contracts, lifecycle updates, and a separately authorized real-provider smoke result.

- [x] **Step 1: Write the real-model smoke before running it**

The opt-in test must execute one embedding and one grounded answer through LiteLLM and assert public aliases plus citations:

```python
@pytest.mark.asyncio
async def test_real_athena_aliases_produce_grounded_cited_answer(runtime) -> None:
    if os.environ.get("TAP_RUN_ATHENA_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real Athena model smoke requires explicit opt-in")
    embedding = await runtime.model.embed("退款审批")
    assert len(embedding.vector) == runtime.settings.embedding_dimension
    response = await runtime.answer_service.answer(runtime.smoke_request())
    assert response.abstained is False
    assert response.claims
    assert all(claim.citation_ids for claim in response.claims)
```

Emit only alias, success/failure and monotonic elapsed milliseconds. Do not log query, original text, answer, vector, endpoint, request ID, provider model or credential.

- [x] **Step 2: Run the complete deterministic gate from a clean process state**

```sh
make bootstrap
make contracts
git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/
make check
make test
make demo-e2e
git diff --check
```

Expected: all commands exit `0`, Backend reports no unexpected skips inside selected integration suites, Web has no warnings, generated contracts are unchanged, and Playwright proves upload/answer/citation/retry/delete plus document/ingestion/index persistence on real local middleware. The rendered answer remains page-local state: refresh clears it, no answer-history recovery API is part of this Demo, and the user can ask again from persisted `ready` sources.

- [x] **Step 3: Run the real-provider smoke only with explicit local credentials**

```sh
set -a
. ./.env
set +a
TAP_RUN_ATHENA_REAL_MODEL_SMOKE=1 uv run --project apps/backend pytest \
  apps/backend/tests/smoke/test_athena_real_model.py -v
```

If credentials are not configured, record `not-run: credentials not provided` in the review and do not imply a real-provider GREEN. If configured, require the test to pass; provider `401`, wrong dimension, malformed claims or unresolvable citations are failures, not skips.

完成记录：已检查本 checkout 的 credential 前置条件，`.env` 不存在，故按本步骤规则未调用 provider，并记录 `not-run: credentials not provided`。本 checkbox 只表示凭据检查与必需记录已完成，不表示 real-provider GREEN。

- [x] **Step 4: Perform manual visual and keyboard acceptance**

Open the loopback Web at desktop and 390px widths. Verify the approved Athena visual hierarchy, tab order, focus rings, upload keyboard flow, source checkbox labels, inline citation focus, right-panel close/return focus, six-stage statuses, Chinese/English wrapping and no horizontal overflow. Save screenshots only to the review evidence directory; commit screenshots only if they materially clarify a documented UI change.

- [x] **Step 5: Update current developer and architecture documentation**

Make these statements exact and non-overlapping:

```text
README/AGENTS: repository now contains Python and Web applications; list exact bootstrap,
               check, test and demo commands; document loopback/no-auth/no-OCR/local-only.
overview:      Athena local vertical slice is implemented; Azure AI Search remains the
               enterprise baseline and full Phase 1 remains active.
UI design:     distinguish the source-first local Athena workspace from future durable
               Conversation/SSE/Trace/Feedback Knowledge Chat.
RAG foundation: record doc-only local Milvus projection without claiming four-family completion.
contracts:     document statuses/stages/routes/Problem Details, answer claim spans and citation preview.
roadmap/Phase1: credit only reused capabilities; do not mark complete Chat/SSE/auth/Trace tasks done.
```

README must include supported formats, 25 MiB, 50/20 limits, model environment names, document/ingestion/index persistence across refresh/restart/down-up, the page-local answer/no-history boundary, `demo-reset` danger, deterministic E2E versus real-model smoke, and troubleshooting for each `demo-check` component.

- [x] **Step 6: Write the evidence review and apply lifecycle rules**

The review contains a table for contract generation, Backend unit/contract/integration, Web component/build, four-format Playwright, duplicate/concurrency, three fail-once stages, source exclusion, citation tamper, delete cleanup, app restart, `demo-down/up` persistence, loopback binding and real-model smoke. Record the exact command, exit status, test count, skipped count and sanitized evidence artifact hash for each run.

Only after every mandatory deterministic/local-middleware gate passes:

```text
RFC-005: accepted -> implemented
this plan: active -> completed
RFC-003: remains accepted
RFC-004: remains draft
Phase 1 plan: remains active
all existing ADR statuses and semantics: unchanged
```

If the optional real-model smoke is not run, RFC-005 may still be implemented only when the review explicitly says the runnable route is configured but provider smoke is unverified; never label it real-model validated.

- [x] **Step 7: Validate documentation and full diff**

```sh
rg --files README.md AGENTS.md docs apps scripts contracts
rg -n 'documentation-only|尚未建立 `apps/web`|实现尚未开始' README.md AGENTS.md docs
git diff --check
git diff -- README.md AGENTS.md docs/
make contracts
git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/
```

Expected: no stale documentation-only claim, lifecycle metadata follows governance, relative links resolve in the rendered preview, and regeneration remains clean.

- [x] **Step 8: Commit the accepted implementation evidence**

fresh whole-branch review 已以 Critical/Important/Minor 全部为 `0` 的 `APPROVED` 结束；root 通过本次提交完成验收证据回填。Task 10 implementer 未 commit、push 或 merge，root 也未 push 或 merge。

```sh
git add README.md AGENTS.md apps/backend/tests/smoke \
  apps/web/vite.config.ts apps/web/src/shared/testing/productionBuild.test.ts docs
git commit -m "docs: record athena demo acceptance"
```

The branch is ready for `verification-before-completion`, a fresh whole-branch code review, and `finishing-a-development-branch`; do not merge or push without separate user authorization.
