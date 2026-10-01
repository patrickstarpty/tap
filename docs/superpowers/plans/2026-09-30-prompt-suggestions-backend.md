# 推荐问题后端实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `apps/tap-ai-backend` 实现推荐问题的生成、依据校验、缓存、读取过滤、刷新触发与 `GET /prompt-suggestions` 接口。

**Architecture:** 推荐能力归 `chat` 模块：领域类型、刷新节流规则与 `PromptSuggestionService` 只依赖 chat 内定义的端口。chat 不能导入 Knowledge 内部实现（`tests/architecture/test_module_boundaries.py`），因此“知识库主题 / 当前来源版本 / 依据校验”三个端口由 `entrypoints` 中的适配器实现并在 `tapper_runtime` 组装；chat 自有表（会话、轮次）上的使用统计由 chat 适配器实现。刷新以 MySQL 刷新队列表驱动，由新的独立 worker 进程处理，模型调用经 `ModelGateway.generate_structured`。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy Core async、Alembic、pytest、LiteLLM 模型网关。

**Spec:** [Tapper 推荐问题设计](../specs/2026-09-30-tapper-prompt-suggestions-design.md)（“架构与数据流”“读取与过滤”“验证”，“实施顺序”第 3 步）。

## Global Constraints

- 路径均相对 `apps/tap-ai-backend/`，除非写明仓库根路径。
- 不新增基础设施或依赖；复用 MySQL、模型网关、`AnswerService`、Alembic、现有 worker 循环 `run_worker_loop`。
- chat 只能以 `_` 别名导入 `tap.modules.knowledge.api` 的白名单符号；不扩白名单，Knowledge 访问一律经 chat 端口 + entrypoints 适配器。
- 每次刷新最多 8 个候选；每个候选附 1–3 个来源 ID，且必须属于本次输入的知识库来源；推荐语言与请求 `locale`（`en` | `zh`）一致。
- “有依据”的定义：以当前用户授权执行 `AnswerService.answer`（来源以 `ResourceMode.SCOPE` 传入），`response.abstained is False`。代码库没有数值阈值，不另造分数阈值。
- 节流常量 `REFRESH_THROTTLE = timedelta(minutes=10)`，仅作用于“对话完成”与“读取过滤”两类触发；“知识发布”“缓存缺失”立即到期；每日兜底 `DAILY_REFRESH = timedelta(hours=24)`。节流以（项目，用户，语言）为单位。
- 其他用户数据只以“来源 ID → 次数”进入模型输入；其他用户提问原文不得出现在任何模型输入或接口输出中。
- 知识库为空时不调用模型，缓存写为空列表。
- 接口只读缓存与当前来源状态，不调用模型；过滤后为空返回 `{"items": []}`。
- 迁移编号取当前 Alembic head 的下一个（写本计划时 main 为 `0025_managed_purge_obligation`，则用 `0026_prompt_suggestions`；若可观测性的 `0026_observability` 先合入，则用 `0027_prompt_suggestions`）。新表加入 `platform/db/registry.py` 的 `BUSINESS_TABLES` 与 `tests/architecture/test_migration_metadata.py` 的 `EXPECTED_TABLES`。
- 单测：`uv run --project apps/tap-ai-backend pytest <path> -q`（仓库根执行）；MySQL 集成测试加 `TAP_RUN_MYSQL_INTEGRATION=1`；收尾 `make check`、`make test`。
- 提交用小写祈使句 Conventional Commit，结尾附 `Co-Authored-By` 行。

## 与设计的两处落地约定

- **来源版本**：记录不透明版本串 `"{current_revision_id}:{managed_version}"`（无托管文档时 `managed_version` 为 `0`），由 Knowledge 适配器生成；读取时不等即视为“有新版本”。chat 领域只比较字符串。
- **项目热门**：现有轮次只记录所用来源，不记录图谱实体，因此“其他用户常用图谱实体的统计”无数据来源；本计划的项目热门只含其他用户的来源使用次数，图谱实体只来自知识库主题（主要实体）。执行前请用户确认此收窄。

## Review Focus

1. 刷新进行中（已被 worker 认领）时又来一次“知识发布”触发 → 本次刷新完成后仍需再刷新一次，不能被 `complete` 覆盖掉新的到期时间 → Task 2 `test_request_during_running_refresh_keeps_new_due_at`。
2. 模型返回的来源 ID 不在输入里、重复问题或超过 3 个来源 → 丢弃该候选而不是整次失败 → Task 3 `test_refresh_drops_invalid_candidates`。
3. 刷新失败（模型或检索异常）→ 保留旧推荐（读取过滤仍会拦住失效项），不写空列表 → Task 3 `test_failed_refresh_keeps_previous_suggestions`。
4. 首次读取（从未刷新）→ 返回空列表并立即排入刷新，而不是等每日兜底 → Task 3 `test_first_read_requests_refresh`。
5. 过滤触发在 10 分钟内反复读取 → 只排一次、到期时间不前移 → Task 1 `test_throttled_reason_never_moves_due_earlier_than_throttle`。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `src/tap/modules/chat/domain/suggestions.py` | 领域类型与刷新到期规则 |
| `src/tap/modules/chat/application/suggestion_ports.py` | chat 侧端口 Protocol |
| `src/tap/modules/chat/application/suggestions.py` | `PromptSuggestionService`（读取过滤、刷新）与 `SuggestionRefreshWorker` |
| `src/tap/modules/chat/adapters/memory_suggestions.py` | 单测用内存 store |
| `src/tap/modules/chat/adapters/mysql_suggestions.py` | 两张表与 `MysqlSuggestionStore` |
| `src/tap/modules/chat/adapters/mysql_suggestion_usage.py` | 个人使用与项目热门统计 |
| `src/tap/modules/chat/adapters/model_gateway_suggestions.py` | 结构化生成 |
| `src/tap/entrypoints/prompt_suggestion_knowledge.py` | Knowledge 侧适配器、依据校验、发布触发投影 |
| `src/tap/entrypoints/tapper_suggestion_worker.py` | worker 进程入口 |
| `src/tap/interfaces/http/routes/prompt_suggestions.py` | HTTP 接口 |
| `migrations/versions/00NN_prompt_suggestions.py` | 迁移 |

---

### Task 1: 领域类型与刷新到期规则

**Files:**
- Create: `src/tap/modules/chat/domain/suggestions.py`、`tests/unit/chat/test_suggestion_domain.py`

**Interfaces:**
- Produces:
  ```python
  SuggestionLocale = Literal["en", "zh"]
  class RefreshReason(StrEnum): KNOWLEDGE_PUBLISHED="knowledge_published"; TURN_COMPLETED="turn_completed"; DAILY="daily"; FILTERED="filtered"; MISSING="missing"
  REFRESH_THROTTLE: timedelta  # 10 min
  DAILY_REFRESH: timedelta     # 24 h
  MAX_CANDIDATES = 8; MAX_SOURCES_PER_SUGGESTION = 3
  @dataclass(frozen=True, slots=True) class SuggestionKey: actor_id: str; locale: SuggestionLocale
  @dataclass(frozen=True, slots=True) class SuggestionSource: source_id: str; version: str
  @dataclass(frozen=True, slots=True) class PromptSuggestion: suggestion_id: str; question: str; sources: tuple[SuggestionSource, ...]
  @dataclass(frozen=True, slots=True) class TopicSource: source_id: str; name: str; version: str; headings: tuple[str, ...]
  @dataclass(frozen=True, slots=True) class SuggestionInputs:
      topics: tuple[TopicSource, ...]; entities: tuple[str, ...]
      personal_questions: tuple[str, ...]; personal_source_ids: tuple[str, ...]
      popular_sources: tuple[tuple[str, int], ...]
  @dataclass(frozen=True, slots=True) class Candidate: question: str; source_ids: tuple[str, ...]
  def next_due_at(reason: RefreshReason, *, now: datetime, last_refreshed_at: datetime | None, current_due_at: datetime | None) -> datetime
  ```
  `next_due_at`：节流类（`TURN_COMPLETED`、`FILTERED`）为 `max(now, last_refreshed_at + REFRESH_THROTTLE)`（无 `last_refreshed_at` 时为 `now`），其余为 `now`；若 `current_due_at` 非空取两者较早者。`SuggestionInputs` 故意没有任何“其他用户文本”字段。

- [ ] **Step 1: 写失败测试**

```python
def test_turn_completion_within_throttle_is_deferred():
    assert next_due_at(RefreshReason.TURN_COMPLETED, now=T0, last_refreshed_at=T0 - timedelta(minutes=3), current_due_at=None) == T0 + timedelta(minutes=7)
def test_published_knowledge_is_due_now():
    assert next_due_at(RefreshReason.KNOWLEDGE_PUBLISHED, now=T0, last_refreshed_at=T0 - timedelta(minutes=1), current_due_at=None) == T0
def test_earlier_pending_due_at_wins():
    assert next_due_at(RefreshReason.FILTERED, now=T0, last_refreshed_at=None, current_due_at=T0 - timedelta(seconds=5)) == T0 - timedelta(seconds=5)
def test_throttled_reason_never_moves_due_earlier_than_throttle():
    last = T0 - timedelta(minutes=1)
    first = next_due_at(RefreshReason.FILTERED, now=T0, last_refreshed_at=last, current_due_at=None)
    assert next_due_at(RefreshReason.FILTERED, now=T0 + timedelta(minutes=2), last_refreshed_at=last, current_due_at=first) == last + REFRESH_THROTTLE
```

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat/test_suggestion_domain.py -q`。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: Commit**：`feat: add prompt suggestion domain rules`

---

### Task 2: 迁移、表与 MySQL store

**Files:**
- Create: `migrations/versions/00NN_prompt_suggestions.py`、`src/tap/modules/chat/application/suggestion_ports.py`（仅 `SuggestionStore`，其余端口在 Task 3 补齐）、`src/tap/modules/chat/adapters/mysql_suggestions.py`、`src/tap/modules/chat/adapters/memory_suggestions.py`、`tests/integration/test_prompt_suggestion_store_mysql.py`、`tests/unit/chat/test_memory_suggestion_store.py`
- Modify: `src/tap/platform/db/registry.py`（`BUSINESS_TABLES`）、`tests/architecture/test_migration_metadata.py`（`EXPECTED_TABLES`）

**Interfaces:**
- Consumes: Task 1 全部类型与 `next_due_at`。
- 表（均含 `scope_values` 的五个 scope 列，查询带 `scope_predicates`）：
  - `prompt_suggestion_refresh`：`locale CHAR(2)`；唯一键 `(enterprise_id, project_id, actor_id, locale)`；`due_at DATETIME(6) NULL`、`last_reason VARCHAR(32) NULL`、`last_refreshed_at DATETIME(6) NULL`、`lease_owner VARCHAR(128) NULL`、`lease_token CHAR(32) NULL`、`lease_expires_at DATETIME(6) NULL`、`attempt_count INT NOT NULL DEFAULT 0`、`failure_code VARCHAR(64) NULL`、`created_at`、`updated_at`；索引 `(project_id, due_at)`。
  - `prompt_suggestion`：主键 `suggestion_id CHAR(32)`；`locale CHAR(2)`、`position SMALLINT`、`question VARCHAR(500)`、`sources_json JSON`（`[{"sourceId","version"}]`）、`generated_at DATETIME(6)`；索引 `(project_id, actor_id, locale, position)`。
- Produces（`suggestion_ports.py`）：
  ```python
  @dataclass(frozen=True, slots=True) class ClaimedRefresh: key: SuggestionKey; lease_token: str; attempt_count: int
  class SuggestionStore(Protocol):
      async def load(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...] | None  # None = 从未刷新
      async def request_refresh(self, key: SuggestionKey, reason: RefreshReason, *, now: datetime) -> None
      async def request_refresh_for_actor(self, actor_id: str, reason: RefreshReason, *, now: datetime) -> int  # 只更新已存在的行
      async def request_refresh_for_project_in_transaction(self, session: AsyncSession, reason: RefreshReason, *, now: datetime) -> int
      async def request_daily_refresh(self, *, now: datetime) -> int  # last_refreshed_at < now - DAILY_REFRESH 且 due_at 为空的行
      async def claim_due(self, *, worker_id: str, now: datetime, limit: int, lease_duration: timedelta) -> tuple[ClaimedRefresh, ...]
      async def complete_refresh(self, claim: ClaimedRefresh, suggestions: tuple[PromptSuggestion, ...], *, now: datetime) -> bool
      async def fail_refresh(self, claim: ClaimedRefresh, failure_code: str, *, now: datetime) -> bool
  ```
  语义：`request_*` 以 `next_due_at` 更新 `due_at` 与 `last_reason`；`claim_due` 认领 `due_at <= now` 且未租约或租约过期的行，写 `lease_*`，并**记下认领时的 `due_at`**（放入 lease 行即可）；`complete_refresh` 在同一事务内按 lease_token 围栏：整体替换该 key 的 `prompt_suggestion` 行、`last_refreshed_at = now`、清租约，且仅当 `due_at` 仍等于认领时的值才清空 `due_at`（认领后又有请求则保留）；`fail_refresh` 清租约、`attempt_count + 1`、`due_at = now + REFRESH_THROTTLE`、写 `failure_code`，不动推荐行。`MysqlSuggestionStore(sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext)`；`InMemorySuggestionStore()` 实现同一 Protocol 供单测使用。

- [ ] **Step 1: 写失败测试**（集成测试用 `owned_project_mysql` 与 `VALIDATION_SCOPE`；内存 store 用同一组行为用例的精简版）：

```python
async def test_load_is_none_before_first_refresh(store): assert await store.load(KEY) is None
async def test_complete_replaces_all_suggestions_for_key(store): ...  # 两次 complete，load 只返回第二次的列表，顺序按 position
async def test_suggestions_are_isolated_by_actor_and_locale(store): ...
async def test_claim_only_returns_due_rows(store): ...  # FILTERED 在节流期内请求 → claim_due(now) 为空；now+10min → 认领到
async def test_request_during_running_refresh_keeps_new_due_at(store): ...  # claim → request_refresh(KNOWLEDGE_PUBLISHED) → complete → 再 claim_due 仍能认领
async def test_stale_lease_cannot_complete(store): ...  # 过期后被他人认领，旧 token complete 返回 False 且不写推荐
async def test_failed_refresh_keeps_rows_and_backs_off(store): ...
async def test_daily_refresh_only_marks_old_rows(store): ...
async def test_project_request_only_touches_existing_rows(store): ...  # 不为未读过的用户/语言新建行
```

- [ ] **Step 2: 运行确认失败**：`TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_prompt_suggestion_store_mysql.py apps/tap-ai-backend/tests/unit/chat/test_memory_suggestion_store.py -q`。
- [ ] **Step 3: 实现迁移、表定义、两种 store，登记表名**。
- [ ] **Step 4: 运行确认通过**，并运行 `tests/architecture` 与 `tests/integration/test_schema_drift.py`（后者需 `TAP_RUN_MYSQL_INTEGRATION=1`）。
- [ ] **Step 5: Commit**：`feat: persist prompt suggestions and refresh requests`

---

### Task 3: 读取过滤与刷新服务

**Files:**
- Modify: `src/tap/modules/chat/application/suggestion_ports.py`
- Create: `src/tap/modules/chat/application/suggestions.py`、`tests/unit/chat/test_prompt_suggestion_service.py`

**Interfaces:**
- Consumes: Task 1 类型，Task 2 `SuggestionStore`、`ClaimedRefresh`、`InMemorySuggestionStore`。
- Produces（端口，追加到 `suggestion_ports.py`）：
  ```python
  @dataclass(frozen=True, slots=True) class CurrentSource: source_id: str; name: str; version: str
  class SuggestionKnowledge(Protocol):
      async def current_sources(self, actor_id: str) -> Mapping[str, CurrentSource]  # 当前用户可访问的已发布来源
      async def topics(self, actor_id: str) -> tuple[TopicSource, ...]              # ≤30 个来源，每个 ≤8 个章节标题
      async def main_entities(self, actor_id: str, *, limit: int) -> tuple[str, ...]
  class SuggestionUsage(Protocol):
      async def personal(self, actor_id: str, *, limit: int) -> tuple[tuple[str, ...], tuple[str, ...]]  # (提问文本, 来源 ID)
      async def popular_sources(self, *, excluding_actor_id: str, limit: int) -> tuple[tuple[str, int], ...]
  class SuggestionGenerator(Protocol):
      async def generate(self, inputs: SuggestionInputs, locale: SuggestionLocale) -> tuple[Candidate, ...]
  class GroundingCheck(Protocol):
      async def is_grounded(self, actor_id: str, question: str, source_ids: tuple[str, ...]) -> bool
  ```
- Produces（`suggestions.py`）：
  ```python
  @dataclass(frozen=True, slots=True) class SuggestionView: suggestion_id: str; question: str; sources: tuple[CurrentSource, ...]
  class PromptSuggestionService:
      def __init__(self, *, store: SuggestionStore, knowledge: SuggestionKnowledge, usage: SuggestionUsage, generator: SuggestionGenerator, grounding: GroundingCheck, id_factory: Callable[[], str], clock: Callable[[], datetime])
      async def list(self, key: SuggestionKey) -> tuple[SuggestionView, ...]
      async def refresh(self, key: SuggestionKey) -> tuple[PromptSuggestion, ...]
  class SuggestionRefreshWorker:
      def __init__(self, *, store: SuggestionStore, service: PromptSuggestionService, worker_id: str, clock: Callable[[], datetime], lease_duration: timedelta = timedelta(minutes=5))
      async def run_once(self, *, limit: int) -> int
  ```
  - `list`：`load` 为 `None` → `request_refresh(MISSING)`，返回 `()`；否则逐条保留“全部来源都在 `current_sources` 中且 `version` 相等”的推荐，来源名取当前名称；有被过滤的 → `request_refresh(FILTERED)`。
  - `refresh`：`topics` 为空 → 返回 `()`，不调用 generator；否则组装 `SuggestionInputs`（主要实体 20 个、个人 20 轮、热门 10 个），`generate` 后校验：问题去首尾空白后非空且 ≤ 500 字符、按小写去重、来源 1–3 个且全部属于 `topics`、最多保留前 `MAX_CANDIDATES` 个；再逐个 `is_grounded`，通过者按原顺序生成 `PromptSuggestion`（版本取 `topics` 中的 `version`）。异常向上抛出。
  - `run_once`：先 `request_daily_refresh(now)`，再 `claim_due`；每个认领调用 `refresh`，成功 → `complete_refresh`，异常 → `fail_refresh(claim, type(exc).__name__)` 并继续下一个；返回处理数。worker 不记录推荐文本以外的新日志。

- [ ] **Step 1: 写失败测试**（用 `InMemorySuggestionStore` 与手写 fake 端口）：

```python
async def test_first_read_requests_refresh(): ...                 # list → () 且 store 中该 key due_at == now
async def test_read_drops_deleted_or_unpublished_sources(): ...    # current_sources 缺少来源 → 不返回，且排入 FILTERED
async def test_read_drops_sources_with_newer_version(): ...
async def test_read_drops_sources_the_user_cannot_access(): ...    # fake knowledge 对该 actor 不返回该来源
async def test_read_returns_current_source_names(): ...
async def test_refresh_keeps_only_grounded_candidates(): ...       # grounding 对第 2 个返回 False → 结果不含它
async def test_refresh_drops_invalid_candidates(): ...             # 未知来源 ID、4 个来源、空问题、重复问题 → 丢弃，其余保留
async def test_refresh_without_knowledge_skips_the_model(): ...    # topics 为空 → generator 未被调用，结果 ()
async def test_generator_inputs_contain_only_counts_from_other_users(): ...  # usage.popular_sources 返回计数；断言传给 generator 的 inputs 仅来自端口返回值，且 repr 不含 fake 中其他用户提问 "OTHER-USER-SECRET"
async def test_failed_refresh_keeps_previous_suggestions(): ...    # 先成功一次，再让 generator 抛错 → run_once 后 load 仍为旧列表
async def test_worker_marks_daily_refresh_before_claiming(): ...
```

- [ ] **Step 2: 运行确认失败**：`uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/chat/test_prompt_suggestion_service.py -q`。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**，并运行 `tests/architecture`（确认 chat 未引入 Knowledge 内部依赖）。
- [ ] **Step 5: Commit**：`feat: filter and refresh prompt suggestions`

---

### Task 4: 模型生成适配器与确定性替身

**Files:**
- Create: `src/tap/modules/chat/adapters/model_gateway_suggestions.py`、`tests/unit/chat/test_model_gateway_suggestions.py`
- Modify: `src/tap/testing/deterministic_model_gateway.py`、其现有单测文件（`tests/unit` 中覆盖 `DeterministicModelGateway` 的文件）

**Interfaces:**
- Consumes: Task 1 `SuggestionInputs`、`Candidate`；Task 3 `SuggestionGenerator` Protocol；`ModelGateway.generate_structured(ModelRequest) -> ModelResult`（`modules/ai`）。
- Produces:
  ```python
  SUGGESTION_SCHEMA: dict[str, object]  # "title": "PromptSuggestions"；{"suggestions": [{"question": str, "sourceIds": [str, 1..3]}], maxItems 8}，additionalProperties false
  class ModelGatewaySuggestionGenerator:
      def __init__(self, gateway: ModelGateway, *, scope: ProjectScopeContext, alias: str, timeout_seconds: float)
      async def generate(self, inputs: SuggestionInputs, locale: SuggestionLocale) -> tuple[Candidate, ...]
  ```
  以 `ModelGatewayPlanner`（`chat/adapters/model_gateway_planner.py`）为模板：`ModelOperation.STRUCTURED`、`schema=SUGGESTION_SCHEMA`、`schema_digest`、`prompt_digest`、`allow_retries=False`。提示词为固定英文指令 + `"\n\nINPUT:\n"` + `json.dumps(payload, ensure_ascii=False, sort_keys=True)`；payload 键：`locale`、`sources`（`id`、`name`、`headings`）、`entities`、`recentQuestions`、`recentSourceIds`、`popularSources`（`id`、`count`）。指令要求：按 `locale` 语言写问题；每个问题必须能仅凭所列来源回答；只引用所给来源 ID；优先贴近 `recentQuestions` 与 `popularSources`，但不复述原问题。输出不符合 schema 时抛出现有网关的校验异常。
- 确定性替身：`request.schema` 的 `title == "PromptSuggestions"` 时，解析 `INPUT:` 之后的 JSON，按 `sources` 顺序为每个来源生成一个问题（en `"What does {name} cover?"`，zh `"《{name}》主要包含哪些内容？"`），最多 8 个。

- [ ] **Step 1: 写失败测试**

```python
async def test_generator_requests_structured_output_with_schema(): ...  # fake gateway 记录 request：operation STRUCTURED、schema title "PromptSuggestions"、allow_retries False
async def test_prompt_contains_inputs_and_locale(): ...                 # INPUT JSON 的 locale == "zh"，popularSources == [{"id": "src_…", "count": 3}]
async def test_generator_maps_output_to_candidates(): ...
async def test_deterministic_gateway_answers_suggestion_schema(): ...   # 两个来源 → 两个问题，sourceIds 各为自身
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**，并运行 `tests/architecture/test_model_gateway_composition.py`。
- [ ] **Step 5: Commit**：`feat: generate prompt suggestion candidates through the model gateway`

---

### Task 5: Knowledge 与使用统计适配器

**Files:**
- Create: `src/tap/entrypoints/prompt_suggestion_knowledge.py`、`src/tap/modules/chat/adapters/mysql_suggestion_usage.py`、`tests/integration/test_prompt_suggestion_inputs_mysql.py`、`tests/unit/entrypoints/test_prompt_suggestion_grounding.py`

**Interfaces:**
- Consumes: Task 3 的 `SuggestionKnowledge`、`SuggestionUsage`、`GroundingCheck`、`CurrentSource`；`MysqlReadySources.list_sources()`（`knowledge/adapters/mysql_ready_sources.py`，与其“就绪”判定一致）；`AnswerService.answer(AnswerRequest) -> AnswerResponse`；`ResourceRef(SourceFamily.DOC, source_id, ResourceMode.SCOPE)`。
- Produces:
  ```python
  class KnowledgeSuggestionSources:  # 实现 SuggestionKnowledge
      def __init__(self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext)
  class AnswerGroundingCheck:        # 实现 GroundingCheck
      def __init__(self, answers: AnswerService)
  class SuggestionReadyProjection:   # 实现 knowledge ReadyRevisionProjection
      def __init__(self, store: MysqlSuggestionStore)
  class CompositeReadyProjection:
      def __init__(self, *projections: ReadyRevisionProjection)
  class MysqlSuggestionUsage:        # 实现 SuggestionUsage
      def __init__(self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext)
  ```
  - `current_sources` / `topics`：以 `list_sources()` 的就绪来源为准；`version` = `f"{revision_id}:{managed_version}"`（`knowledge_managed_document.version`，无则 `0`）；`topics` 的章节取该修订 `knowledge_chunk_manifest.anchor_json.headingPath` 的末级标题，去重、保持文档顺序、最多 8 个。当前运行时只有一个演示授权范围，`actor_id` 参数用于端口语义，暂不改变结果。
  - `main_entities`：`graph_node` 经 `graph_node_evidence.source_revision_id` 关联当前就绪修订，按证据数降序取 `label`，去重。
  - `AnswerGroundingCheck.is_grounded`：调用 `AnswerService.answer`，返回 `not response.abstained`；`actor_id` 由 `AnswerService` 绑定的 scope 决定。
  - `SuggestionReadyProjection.after_ready(session, scope, revision, *, now, ingestion_job_id)` 调用 `store.request_refresh_for_project_in_transaction(session, RefreshReason.KNOWLEDGE_PUBLISHED, now=now)`；`CompositeReadyProjection.after_ready` 依次调用各投影。
  - `MysqlSuggestionUsage.personal`：该用户最近 `limit` 个结果为 `completed` 的轮次（`chat_turn.state = 'completed'` 或证据快照 `outcome`），提问取 `turn_input_snapshot.snapshot.message`，来源取其 `resolved_resources[].source_id`（去重）。`popular_sources`：其他用户 `completed` 轮次的来源 ID 计数，SQL 只选出来源 ID 与次数，不读取 `message`。

- [ ] **Step 1: 写失败测试**

```python
# 集成（owned_project_mysql，按现有 knowledge / conversation 集成测试的方式造数据）
async def test_current_sources_exclude_deleted_and_unready_documents(): ...
async def test_source_version_changes_with_new_revision_and_chunk_edit(): ...
async def test_topics_list_headings_in_document_order(): ...
async def test_main_entities_rank_by_evidence(): ...
async def test_personal_usage_reads_only_completed_turns_of_actor(): ...
async def test_popular_sources_count_other_actors_without_their_questions(): ...  # 其他用户提问 "OTHER-USER-SECRET"：结果仅 (source_id, count)，且 personal() 不含它
async def test_ready_projection_requests_refresh_for_existing_readers(): ...      # 先为 KEY request_refresh → 走一次 READY → 该行 due_at == now, last_reason == knowledge_published
# 单元
async def test_grounding_uses_scope_resources_and_abstention(): ...  # fake AnswerService 记录 AnswerRequest：resource_refs 均为 DOC/SCOPE；abstained=True → False
async def test_composite_projection_calls_every_projection(): ...
```

- [ ] **Step 2: 运行确认失败**（集成测试加 `TAP_RUN_MYSQL_INTEGRATION=1`）。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**，并运行 `tests/architecture`。
- [ ] **Step 5: Commit**：`feat: read prompt suggestion inputs from knowledge and usage`

---

### Task 6: HTTP 接口与契约

**Files:**
- Create: `src/tap/interfaces/http/routes/prompt_suggestions.py`、`tests/contract/test_prompt_suggestions_http.py`
- Modify: `src/tap/contracts/http.py`、`src/tap/interfaces/http/dependencies.py`、`src/tap/interfaces/http/app.py:101-128`；仓库根 `contracts/openapi/api.json`、`apps/tap-ai-frontend/src/shared/api/generated/schema.ts`（由 `make contracts` 生成）

**Interfaces:**
- Consumes: Task 3 `PromptSuggestionService.list(SuggestionKey) -> tuple[SuggestionView, ...]`。
- Produces:
  - 契约模型（`ContractModel`，camelCase）：`PromptSuggestionSource(source_id: src_ 模式, name: 1–255)`、`PromptSuggestionItem(id: ShortIdentifier, question: 1–500, sources: list[PromptSuggestionSource] 1–3)`、`PromptSuggestionPage(items: list[PromptSuggestionItem])`。
  - 路由：`GET /api/v1/projects/{project_id}/prompt-suggestions`，`operation_id="prompt_suggestion_list"`，`dependencies=[Depends(project_authorization("knowledge.answer"))]`，查询参数 `locale: Literal["en", "zh"] = Query(...)`，`actor_id` 取 `request.state.project_scope.actor_id`；非法 locale 返回 422 problem。
  - `HttpServices.prompt_suggestions: PromptSuggestionService | None = None` 与访问器 `prompt_suggestion_service(request)`（未配置时抛 `KnowledgeRuntimeUnavailable`，与现有访问器一致）。

- [ ] **Step 1: 写失败契约测试**（参照 `tests/contract/test_conversation_history_http.py` 的 `replace(validation_http_services(), …)` + `TestClient`）：

```python
def test_prompt_suggestions_are_listed_for_locale(): ...       # fake service 返回 1 条 → JSON {"items":[{"id","question","sources":[{"sourceId","name"}]}]}，service 收到 SuggestionKey(actor_id=VALIDATION_SCOPE.actor_id, locale="zh")
def test_prompt_suggestions_reject_unknown_locale(): ...       # locale=fr → 422 problem+json
def test_prompt_suggestions_return_empty_list(): ...
def test_prompt_suggestions_operation_is_published(): ...      # openapi paths[...]["get"]["operationId"] == "prompt_suggestion_list"
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现，并在仓库根运行 `make contracts`**，确认 `schema.ts` 出现 `/api/v1/projects/{project_id}/prompt-suggestions`。
- [ ] **Step 4: 运行契约测试与 `tests/contract/test_generated_contracts.py` 通过**。
- [ ] **Step 5: Commit**：`feat: expose prompt suggestions over http`

---

### Task 7: 触发、worker 与运行时组装

**Files:**
- Create: `src/tap/entrypoints/tapper_suggestion_worker.py`、`tests/unit/entrypoints/test_suggestion_worker_runtime.py`
- Modify: `src/tap/entrypoints/tapper_runtime.py`（`_build_document_repository` L891 的 `ready_projection`、`_assemble_http_services` L1418、新增 `create_suggestion_worker_runtime`）、`src/tap/entrypoints/tapper_generation_worker.py`（`run_once` 中 `complete_evidence` 之后，约 L494）、仓库根 `scripts/run-tapper-dev.sh:202-212`、`tests/unit/chat/test_turn_processor.py` 或生成 worker 的现有单测
- Test: `tests/integration/test_prompt_suggestions_flow.py`

**Interfaces:**
- Consumes: Task 2–6 全部产物；`run(runtime_factory=..., settings=...)` 与 `WorkerRuntime`（`tapper_ingestion_worker.py`）。
- Produces:
  - `create_suggestion_worker_runtime(settings: TapperSettings) -> WorkerRuntime`：组装 `SuggestionRefreshWorker`；模型网关与 `_create_embeddings` 同源（e2e 模式为 `DeterministicModelGateway`），alias 取 `models.chat_alias`；没有专用 Redis 事件，`wakeups` 使用本文件内的 `IdleWakeups`（`wait` 睡眠 `max_wait_seconds` 后返回 `None`，`ack` 为空操作），轮询间隔沿用 worker 设置。
  - 生成 worker 新增可选依赖 `suggestion_refresh: SuggestionStore | None`；证据 `outcome == "completed"` 时调用 `request_refresh_for_actor(actor_id, RefreshReason.TURN_COMPLETED, now=...)`，该调用的异常被捕获并忽略，不影响轮次完成。
  - `ready_projection` 改为 `CompositeReadyProjection(MysqlGraphReadyProjection(...), SuggestionReadyProjection(store))`。
  - `run-tapper-dev.sh` 在 graph worker 之后以同样方式启动 `python -m tap.entrypoints.tapper_suggestion_worker`，并纳入其进程清理。

- [ ] **Step 1: 写失败测试**

```python
async def test_completed_turn_requests_actor_refresh(): ...       # 生成 worker 单测：completed → store 收到 TURN_COMPLETED；abstained → 不请求
async def test_refresh_request_failure_does_not_fail_the_turn(): ...
def test_suggestion_worker_runtime_uses_idle_wakeups(): ...
# 集成：触发 → 生成 → 校验 → 读取
async def test_publish_generate_verify_and_read(): ...  # 确定性网关 + 真实 MySQL：GET 首次为空并排入 MISSING → worker.run_once → GET 返回每个就绪来源一个问题；删除一个来源 → GET 不再返回它且该 key 排入 FILTERED；该来源 READY 新修订 → 旧推荐被过滤
```

- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**；`make demo-up && make demo-dev` 后 `curl -s 'http://127.0.0.1:<api 端口>/api/v1/projects/tapper-demo/prompt-suggestions?locale=en'` 首次为 `{"items":[]}`，数秒后返回推荐。
- [ ] **Step 5: Commit**：`feat: refresh prompt suggestions from publishes, turns and a daily sweep`

---

### Task 8: 真实模型冒烟与架构文档

**Files:**
- Create: `tests/smoke/test_prompt_suggestions_real_model.py`
- Modify: 仓库根 `docs/architecture.md`（§2 worker 列表、§4 新增“推荐问题”数据流、§5 如有差距）

**Interfaces:**
- Consumes: Task 3 `PromptSuggestionService.refresh`、Task 4 `ModelGatewaySuggestionGenerator`、Task 5 适配器。

- [ ] **Step 1: 写冒烟测试**：`TAP_RUN_TAPPER_REAL_MODEL_SMOKE != "1"` 时 `pytest.skip`；否则参照 `tests/smoke/test_tapper_real_model.py` 用 `TapperSettings.from_mapping(os.environ)` 与 `_create_embeddings(settings, max_retries=0)`，断言网关类型为 `LiteLLMModelGateway`，对已就绪的演示知识执行一次 `refresh`：结果至少 1 条、每条问题非空且与 locale 语言一致（zh 含 CJK 字符）、全部通过依据校验（即出现在结果中）。
- [ ] **Step 2: 运行**：未设置变量时预期 1 skipped；设置后预期 PASS。
- [ ] **Step 3: 更新 `docs/architecture.md`**：§4 新增“推荐问题”块（触发 → 刷新队列 → 生成 → 依据校验 → 缓存 → 读取过滤，注明接口不调用模型、只用其他用户的来源计数）；§2 列出新 worker。预览 Markdown。
- [ ] **Step 4: `make check`、`make test`、`git diff --check` 通过。**
- [ ] **Step 5: Commit**：`docs: describe the prompt suggestion data flow`
