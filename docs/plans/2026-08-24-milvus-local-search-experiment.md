---
id: PLAN-MILVUS-LOCAL-SEARCH-EXPERIMENT
status: planned
date: 2026-08-24
related-rfcs:
  - RFC-004
related-adrs:
  - ADR-002
  - ADR-005
  - ADR-012
---

# Milvus Local Search Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个本地 Docker 优先、只含脱敏 `doc` fixture 的 Milvus 纵向实验，使现有 Knowledge application 能在不改变公共领域契约的前提下选择 Milvus 或 Azure AI Search，并以真实数据库门禁验证 ACL、hybrid、provenance、删除、重建与 alias 切换。

**Architecture:** 保持 `KnowledgeAPI -> AuthorizedRetrieval -> SearchPort` 的依赖方向。共享错误和授权边界位于 port/domain 层；Milvus 的配置、filter、alias 绑定、SDK transport 与严格映射位于 adapter；只有 bootstrap 读取 provider 配置。fixture publisher、RBAC bootstrap、健康探针和 embedding research 是独立运维入口，不进入读取应用身份。

**Tech Stack:** Python 3.13.12、FastAPI、Pydantic、pytest、PyMilvus 2.6.17、Milvus Standalone 2.6.22、etcd 3.5.25、MinIO `RELEASE.2024-12-18T13-15-44Z`、Docker Compose、LiteLLM。

**Spec:** [RFC-004：以 Milvus 为实验默认的可替换检索后端](../proposals/2026-08-24-rfc-004-provider-neutral-search-backends.md)

## Global Constraints

- 严格按测试驱动顺序执行每个任务：先写测试并观察预期失败，再写最小实现，最后运行该任务的聚焦测试和全量 `make check && make test`。
- 每个任务通过审查并提交后才进入下一任务；提交主题使用计划中给出的 Conventional Commit subject。
- 不改变 `SearchPort.search(SearchExecution) -> tuple[SearchHit, ...]`、公共 Search/Answer DTO、Citation 或现有 Policy/QueryPlan 语义。
- 不实现 `code`、`bdd`、`failure` ingestion，不实现完整 Phase 1 Task 4 worker，不增加 provider 自动 failover，不关闭 Azure 或 Entra/Project-Policy 的真实外部门禁。
- RFC-004 在实验 review 前保持 `draft`；本计划授权的是可回退 research slice，不把提案写成已接受架构或生产能力。
- 首个配置只允许 `doc`；空 source 请求展开成 `doc`，显式请求未配置 family 必须在 embedding、alias 或 search I/O 前失败。
- 所有 Milvus 查询必须使用可信 Policy/Plan 编译的 filter。不得接受原始 filter、不得截断超限 principal/scope、不得把 provider 故障转成成功 abstention。
- 运行身份固定为 reader、writer、provisioner；读取应用不得持有写入、DDL、alias 或 RBAC 权限。
- 固定 Milvus/PyMilvus/etcd/MinIO 版本，禁止浮动 tag。若 Python 3.13.12 或所需 SDK/API 行为探针失败，停止该任务并修订 RFC/计划，不得静默换版本。
- Schema digest 继续只使用 fields/functions/indexes/consistency 的 canonical 表示；每个 canonical index 严格为 `index_name`、`field_name`、`index_type`、`metric_type`、嵌套 `params`。Pinned transport 的兼容修正只能归一化到这一表示，不能改变 digest 语义或产生第二套 publisher-only 摘要。
- Reader 的 Describe privileges 只存在于 bootstrap `Global/*` base inventory；publisher 的 target-scoped reader set 严格只有 `Collection` + exact database/name 的 `Search`、`Query`。任何 `Global` target record 或不同 database/object type/role 的同名 grant 都不能冒充 target grant。
- Provisioner bootstrap base grants 只接受 pinned live inventory/denial 的闭合二分：`Global/*` 精确为 `CreateAlias`、`CreateCollection`、`DescribeAlias`、`DropAlias`、`DropCollection`、`ManageOwnership`、`SelectOwnership`，`Collection/*` 精确为 `CreateIndex`、`GetLoadState`、`GetLoadingProgress`、`IndexDetail`、`Load`、`Release`；不得泛化到其他 privilege、版本、API 或 resource level。`SelectOwnership` 只允许 publisher 使用 provisioner 身份执行安全 `describe_role`/grant inventory，`ManageOwnership` 保留既有 grant mutation 用途；reader/writer 不获得前者，publisher 也不得用 root/admin 旁路。Bootstrap 只拥有 `Global/*` 与 wildcard base namespace，必须精确纠错且保留 publisher 拥有的合法 concrete target grants；异常或所有权不明的 concrete record 必须 fail closed，不能静默接受或宽泛删除。
- 日常 CI 使用仓库内脱敏预计算 vectors，不调用付费 API。实验报告前只运行一次有界真实 LiteLLM embedding profile；默认最多 100 chunks/20 queries，绝对上限 500/100。
- Milvus 本地端口及现有本地服务宿主端口统一绑定 `127.0.0.1`；共享非生产部署、TLS、HA、SLO、备份和生产容量不在本计划范围。
- 启动真实 Milvus 前验证 Docker 至少可用 2 vCPU 与 8 GiB memory；不足时门禁失败并报告资源，不降低数据库配置冒充有效实验。
- 任何真实凭据、provider 请求正文、group IDs、raw filter 或 vectors 不进入日志、报告、git 或异常响应。
- 不改写用户已有变更。每次提交前运行 `git diff --check` 并检查暂存范围。

---

## Final File Responsibilities

| 路径 | 单一职责 |
| --- | --- |
| `knowledge/ports/errors.py` | provider-neutral 搜索失败类型 |
| `knowledge/adapters/milvus/config.py` | 已验证的 Milvus target/runtime 配置 |
| `knowledge/adapters/milvus/filter.py` | 从可信 `SearchExecution` 编译封闭表达式 |
| `knowledge/adapters/milvus/transport.py` | PyMilvus SDK 隔离、deadline 与返回值归一化 |
| `knowledge/adapters/milvus/targets.py` | alias 到可信 physical collection 的单请求绑定 |
| `knowledge/adapters/milvus/mapping.py` | provider row 到 `SearchHit` 的严格映射 |
| `knowledge/adapters/milvus/search.py` | 两通道 hybrid 编排并实现 `SearchPort` |
| `knowledge/adapters/milvus/readiness.py` | reader-only alias/schema/canary readiness；不承担 CRUD 健康编排 |
| `knowledge/adapters/milvus/audit.py` | 固定字段的脱敏 search audit event/sink；不保存 filter、groups 或 vectors |
| `tap/entrypoints/knowledge_bootstrap.py` | 显式 provider 选择；不做请求级切换 |
| `tap/operations/milvus/` | schema、fixture 发布、RBAC 与行为健康编排 |
| `tap/operations/milvus/client.py` | provisioner/writer/admin 的 PyMilvus SDK 适配；不被读取应用导入 |
| `tap/operations/milvus/activation.py` | 本地实验 active-corpus marker 的原子写入；不是生产 Project Policy store |
| `scripts/milvus_*.py` | 薄 CLI；只调用 `tap.operations.milvus` |
| `tests/fixtures/milvus/` | 脱敏 manifest、queries 与预计算 vectors |
| `.local/milvus-*` | ignored embedding cache 与实验报告原始输出 |

### Task 1: 提升共享搜索错误并固定 HTTP 503 语义

**Files:**

- Create: `apps/backend/src/tap/modules/knowledge/ports/errors.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/azure_ai_search.py`
- Modify: `apps/backend/src/tap/interfaces/http/app.py`
- Modify: `apps/backend/tests/contract/test_azure_search_strict.py`
- Modify: `apps/backend/tests/contract/test_knowledge_api.py`
- Modify: `apps/backend/tests/contract/test_http_problem_details.py`
- Create: `apps/backend/tests/contract/test_search_errors.py`

**Interfaces:**

- Consumes: `AzureAISearchAdapter.search(SearchExecution)`、`create_app() -> FastAPI`、`KnowledgeAPI.answer(AnswerRequest, RetrievalPolicyContext)`。
- Produces: `SearchError`、`SearchUnavailable`、`SearchBoundsExceeded`，以及固定的两个 RFC 9457 type URL；后续所有 provider 只从 port 层导入这些类型。

```python
class SearchError(Exception):
    """A search provider could not complete a contract-valid execution."""


class SearchUnavailable(SearchError):
    """The selected provider is unavailable or returned invalid data."""


class SearchBoundsExceeded(SearchError):
    """A trusted execution exceeds provider-neutral safety bounds."""

SEARCH_UNAVAILABLE_TYPE = "https://tap.example/problems/search-unavailable"
SEARCH_EXECUTION_REJECTED_TYPE = "https://tap.example/problems/search-execution-rejected"
```

**Steps:**

- [ ] **Step 1:** 写测试证明两个异常从 `knowledge.ports.errors` 导入，Azure adapter 抛出的是同一 class object，而不是 adapter 私有类型。

  ```python
  from tap.modules.knowledge.adapters.azure_ai_search import (
      SearchUnavailable as AzureSearchUnavailable,
  )
  from tap.modules.knowledge.ports.errors import SearchBoundsExceeded, SearchUnavailable


  def test_azure_uses_provider_neutral_search_errors() -> None:
      assert AzureSearchUnavailable is SearchUnavailable
      assert issubclass(SearchBoundsExceeded, Exception)
  ```
- [ ] **Step 2:** 在 HTTP contract test 增加仅供测试的 endpoint，分别抛出两个异常；核心断言如下，第二个异常使用 `/search-execution-rejected` 重复同一组断言。

  ```python
  async def fail_search() -> None:
      raise SearchUnavailable("milvus://reader:secret@127.0.0.1 raw-filter")

  app = create_app()
  app.add_api_route("/_test/search-unavailable", fail_search, methods=["GET"])
  response = TestClient(app).get("/_test/search-unavailable")
  assert response.status_code == 503
  assert response.headers["content-type"].startswith("application/problem+json")
  assert response.json()["type"].endswith("/search-unavailable")
  assert "secret" not in response.text
  assert "raw-filter" not in response.text
  ```
- [ ] **Step 3:** 在 `test_knowledge_api.py` 增加失败路径：`SearchPort` 抛错后 `KnowledgeAPI.answer` 继续抛共享错误，model 未调用且没有成功 response/citation。
- [ ] **Step 4:** 运行 `uv run --project apps/backend pytest apps/backend/tests/contract/test_search_errors.py apps/backend/tests/contract/test_http_problem_details.py apps/backend/tests/contract/test_knowledge_api.py -v`，确认因模块/handler 缺失失败。
- [ ] **Step 5:** 新建共享错误模块，迁移 Azure import，并在 `create_app()` 注册以下两个 handler；不得把 `_error` 插入响应。

  ```python
  @app.exception_handler(SearchUnavailable)
  async def search_unavailable_problem(
      _request: Request, _error: SearchUnavailable
  ) -> JSONResponse:
      return problem_response(SEARCH_UNAVAILABLE_PROBLEM)

  @app.exception_handler(SearchBoundsExceeded)
  async def search_execution_rejected_problem(
      _request: Request, _error: SearchBoundsExceeded
  ) -> JSONResponse:
      return problem_response(SEARCH_EXECUTION_REJECTED_PROBLEM)
  ```
- [ ] **Step 6:** 重跑聚焦测试，再运行 `make check && make test`。
- [ ] **Step 7:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/modules/knowledge/ports/errors.py apps/backend/src/tap/modules/knowledge/adapters/azure_ai_search.py apps/backend/src/tap/interfaces/http/app.py apps/backend/tests/contract/test_azure_search_strict.py apps/backend/tests/contract/test_knowledge_api.py apps/backend/tests/contract/test_http_problem_details.py apps/backend/tests/contract/test_search_errors.py
  git diff --cached --check
  git commit -m "refactor: share search provider errors"
  ```

### Task 2: 在共同授权边界收紧 group bounds 与 `doc` family

**Files:**

- Modify: `apps/backend/src/tap/modules/access/domain/policy.py`
- Modify: `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- Modify: `apps/backend/tests/unit/access/test_policy_context.py`
- Modify: `apps/backend/tests/contract/test_authorized_execution.py`

**Interfaces:**

- Consumes: `VerifiedSubjectFacts`、`ProjectPolicy`、`AuthorizedActor` 和现有 `AuthorizedRetrieval._source_families`。
- Produces: 共同的 `MAX_POLICY_GROUP_IDS=128`、`MAX_POLICY_STRING_LENGTH=256`；Milvus/Azure adapter 可依赖已验证的有界 group set。

```python
MAX_POLICY_GROUP_IDS = 128
MAX_POLICY_STRING_LENGTH = 256

def _string_set(
    name: str,
    value: object,
    *,
    allow_empty: bool,
    max_items: int | None = None,
    max_item_length: int = MAX_POLICY_STRING_LENGTH,
) -> None: ...
```

**Steps:**

- [ ] **Step 1:** 增加 actor 与 project policy 的边界测试：128 个 group 和 256 字符成员通过；129 个、257 字符、空白成员失败；错误不得包含完整 group 值。

  ```python
  def test_verified_subject_rejects_more_than_128_groups() -> None:
      groups = frozenset(f"group-{index:03d}" for index in range(129))
      with pytest.raises(ValueError, match="group_ids must contain at most 128 values"):
          VerifiedSubjectFacts(
              tenant_id="tenant-a",
              user_id="user-a",
              group_ids=groups,
              roles=frozenset({"reader"}),
              token_verified=True,
          )
  ```
- [ ] **Step 2:** 增加执行顺序测试：实验 policy 精确允许 `frozenset({"doc"})` 时，空 source 请求只产生 `doc` plan；显式 `code` 请求在 redaction、embedding、alias/search recorder 被调用前失败。
- [ ] **Step 3:** 运行 `uv run --project apps/backend pytest apps/backend/tests/unit/access/test_policy_context.py apps/backend/tests/contract/test_authorized_execution.py -v`，确认边界测试失败。
- [ ] **Step 4:** 给 `_string_set` 添加以下精确检查，并在 subject、project policy 与 authorized actor 的 group 字段传入 `max_items=128`、`max_item_length=256`；roles 与闭合 enum 集合不传 `max_items`。

  ```python
  if max_items is not None and len(value) > max_items:
      raise ValueError(f"{name} must contain at most {max_items} values")
  if any(len(item) > max_item_length for item in value):
      raise ValueError(f"{name} values must contain at most {max_item_length} characters")
  ```
- [ ] **Step 5:** 保持 `_source_families` 的 policy 语义不变，只补足显式未授权 family 的 pre-I/O 断言与测试 double 观测。
- [ ] **Step 6:** 重跑聚焦测试和 `make check && make test`。
- [ ] **Step 7:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/modules/access/domain/policy.py apps/backend/src/tap/modules/knowledge/application/retrieve.py apps/backend/tests/unit/access/test_policy_context.py apps/backend/tests/contract/test_authorized_execution.py
  git diff --cached --check
  git commit -m "fix: bound retrieval policy groups"
  ```

### Task 3: 固定并验证 Milvus/PyMilvus 兼容矩阵

**Files:**

- Modify: `apps/backend/pyproject.toml`
- Modify: `uv.lock`
- Create: `apps/backend/tests/contract/test_pymilvus_compatibility.py`
- Modify: `docs/reference/2026-08-20-source-notes.md`

**Interfaces:**

- Consumes: repository Python pin `==3.13.12` and uv lock workflow.
- Produces: importable PyMilvus `2.6.17` surface used only by Task 5 transport; exact server/dependency versions used by Task 7 Compose.

**Required versions:**

| Component | Version |
| --- | --- |
| Milvus | `2.6.22` |
| PyMilvus | `2.6.17` |
| etcd | `3.5.25` |
| MinIO | `RELEASE.2024-12-18T13-15-44Z` |

执行时只用以下固定版本官方资料更新 source notes：[Milvus v2.6.22 release](https://github.com/milvus-io/milvus/releases/tag/v2.6.22)、[PyMilvus 2.6.17](https://pypi.org/project/pymilvus/2.6.17/)、[Standalone Compose](https://milvus.io/docs/v2.6.x/install_standalone-docker-compose.md)、[Docker prerequisites](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)、[full-text search](https://milvus.io/docs/v2.6.x/full-text-search.md)、[language identifier](https://milvus.io/docs/v2.6.x/language-identifier.md)、[FLAT](https://milvus.io/docs/v2.6.x/flat.md)、[INVERTED](https://milvus.io/docs/v2.6.x/inverted.md) 与 [GrantPrivilegeV2](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Authentication/grant_privilege_v2.md)。

**Steps:**

- [ ] **Step 1:** 写 compatibility test，断言 PyMilvus 版本与所需 surface。

  ```python
  import pymilvus
  from pymilvus import AnnSearchRequest, Function, FunctionType, MilvusClient, RRFRanker


  def test_pymilvus_surface_is_pinned_for_python_313() -> None:
      assert pymilvus.__version__ == "2.6.17"
      for name in (
          "describe_alias",
          "describe_collection",
          "describe_index",
          "hybrid_search",
          "create_schema",
          "alter_alias",
          "grant_privilege_v2",
          "run_analyzer",
      ):
          assert callable(getattr(MilvusClient, name))
      assert AnnSearchRequest is not None
      assert RRFRanker is not None
      assert Function is not None
      assert FunctionType.BM25 is not None
  ```
- [ ] **Step 2:** 运行 `uv run --project apps/backend pytest apps/backend/tests/contract/test_pymilvus_compatibility.py -v`；预期 FAIL 为 `ModuleNotFoundError: No module named 'pymilvus'`。
- [ ] **Step 3:** 执行 `uv add --project apps/backend 'pymilvus==2.6.17'`，提交生成的 lockfile；不得手工放宽 Python `==3.13.12`。
- [ ] **Step 4:** 运行 `uv sync --frozen --all-groups` 与上一步 pytest 命令；预期 PASS。安装、import 或 API surface 任一失败即停止执行并记录阻断证据。
- [ ] **Step 5:** 在 source notes 记录官方 release、SDK compatibility、Compose 安装和最低本地资源来源链接及访问日期。
- [ ] **Step 6:** 运行 `make check && make test`。
- [ ] **Step 7:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/pyproject.toml uv.lock apps/backend/tests/contract/test_pymilvus_compatibility.py docs/reference/2026-08-20-source-notes.md
  git diff --cached --check
  git commit -m "build: pin milvus client compatibility"
  ```

### Task 4: 实现可信 target 配置与封闭 ACL filter 编译器

**Files:**

- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/__init__.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/config.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/filter.py`
- Create: `apps/backend/tests/contract/test_milvus_config.py`
- Create: `apps/backend/tests/contract/test_milvus_filter.py`

**Interfaces:**

- Consumes: Task 1 `SearchBoundsExceeded`、Task 2 有界 `RetrievalPolicyContext`、现有 `SearchExecution` 与 `SourceFamily`。
- Produces: `MilvusIndexTarget`、`MilvusSearchConfig` 和 `compile_milvus_filter(...) -> str`；Task 5/6 不自行拼接 filter。

```python
@dataclass(frozen=True, slots=True)
class MilvusIndexTarget:
    family: SourceFamily
    alias: str
    physical_name_prefix: str
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int

@dataclass(frozen=True, slots=True)
class MilvusSearchConfig:
    uri: str
    database: str
    username: str
    password: SecretStr
    targets: Mapping[SourceFamily, MilvusIndexTarget]
    candidate_limit: int = 50
    timeout_seconds: float = 8.0
    max_connections: int = 4
    max_filter_bytes: int = 32_768

def compile_milvus_filter(
    execution: SearchExecution,
    family: SourceFamily,
    *,
    max_bytes: int,
) -> str: ...
```

**Steps:**

- [ ] **Step 1:** 写配置测试：`http://` 只允许 `127.0.0.1`/`localhost`，非 loopback 必须 `https://`；首轮只接受单个 `doc` target、canonical `schema_sha256`、正整数维度、候选 `1..50`、deadline `0 < n <= 30`、connections `1..16` 和安全 alias/prefix；`repr` 不出现 password。

  ```python
  def test_milvus_config_hides_password_and_accepts_only_doc_target() -> None:
      target = MilvusIndexTarget(
          family=SourceFamily.DOC,
          alias="kb_doc_active",
          physical_name_prefix="kb_doc_v1_",
          schema_version="doc-schema-v1",
          schema_sha256="sha256:" + "c" * 64,
          corpus_version="corpus-fixture-v1",
          embedding_model_version="research-embedding-v1",
          vector_dimension=1536,
      )
      config = MilvusSearchConfig(
          uri="http://127.0.0.1:19530",
          database="tap_local",
          username="tap_reader",
          password=SecretStr("reader-secret"),
          targets={SourceFamily.DOC: target},
      )
      assert "reader-secret" not in repr(config)
      assert tuple(config.targets) == (SourceFamily.DOC,)
  ```
- [ ] **Step 2:** 在 test module 定义以下 `doc_execution()` factory，并用其固定完整必需 clause；resource-scope cases 构造新的 Project Policy，不通过 `object.__setattr__` 篡改可信 context。

  ```python
  def doc_execution() -> SearchExecution:
      query = "How does the payment policy work?"
      query_hash = "sha256:" + hashlib.sha256(query.encode()).hexdigest()
      subject = VerifiedSubjectFacts(
          tenant_id="tenant-a",
          user_id="user-a",
          group_ids=frozenset({"group-one"}),
          roles=frozenset({"reader"}),
          token_verified=True,
      )
      project_policy = ProjectPolicy(
          tenant_id="tenant-a",
          project_id="project-a",
          permission_granted=True,
          allowed_group_ids=frozenset({"group-one"}),
          classification_ceiling=Classification.CONFIDENTIAL,
          allowed_environments=frozenset({"production"}),
          allowed_source_families=frozenset({"doc"}),
          active_corpus_version="corpus-fixture-v1",
          acl_digest="sha256:" + "a" * 64,
          policy_version="policy-v1",
          decision_id="decision-v1",
      )
      policy = build_retrieval_policy_context(
          subject,
          project_policy,
          requested_tenant_id="tenant-a",
          requested_project_id="project-a",
      )
      plan = QueryPlan(
          query_plan_id="plan-v1",
          operation_id="operation-v1",
          tenant_id="tenant-a",
          project_id="project-a",
          policy_decision_id="decision-v1",
          policy_version="policy-v1",
          acl_digest="sha256:" + "a" * 64,
          answer_mode=AnswerMode.QUICK,
          retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
          source_families=(SourceFamily.DOC,),
          resources=(),
          effective_environment="production",
          corpus_version="corpus-fixture-v1",
          candidate_limit=50,
          raw_request_hash="sha256:" + "b" * 64,
          sanitized_query=query,
          sanitized_query_hash=query_hash,
          redaction_version="redaction-v1",
          embedding_model_id="research-embedding-v1",
          embedding_dimension=1536,
      )
      snapshot = ContextSnapshot(
          context_snapshot_id="snapshot-v1",
          operation_id="operation-v1",
          tenant_id="tenant-a",
          project_id="project-a",
          policy_decision_id="decision-v1",
          policy_version="policy-v1",
          acl_digest="sha256:" + "a" * 64,
          layers=(
              ContextLayer(
                  kind=ContextLayerKind.CURRENT_TURN,
                  ref_ids=(),
                  content_hash=query_hash,
                  token_count=7,
              ),
          ),
      )
      return SearchExecution(
          policy=policy,
          plan=plan,
          context_snapshot=snapshot,
          query_vector=(0.0,) * 1536,
      )

  expression = compile_milvus_filter(
      doc_execution(), SourceFamily.DOC, max_bytes=32_768
  )
  for clause in (
      'tenant_id == "tenant-a"',
      'project_id == "project-a"',
      'ARRAY_CONTAINS_ANY(allowed_group_ids, ["group-one"])',
      'classification_rank in [0, 1, 2]',
      'environment in ["production", "global"]',
      'corpus_version == "corpus-fixture-v1"',
      "deleted == false",
  ):
      assert clause in expression
  ```
- [ ] **Step 3:** 增加 resource union 测试：只有当前 family 且 `ResourceMode.SCOPE` 的 resources 进入 provider filter；每个 resource 内 source/revision/hash 与 subtree 为 AND，多个 resource 为 OR。`REQUIRED`/`PREFERRED` 继续由 application 结果语义处理，不擅自缩窄 provider corpus；引号、反斜线和控制字符被专用 literal encoder 拒绝或安全编码。
- [ ] **Step 4:** 增加 fail-closed 测试：空 groups/classifications、超过 20 resources、32 locators、256 字符、32 KiB 以及非 `doc` family 都在 transport recorder 调用前抛 `SearchBoundsExceeded`。
- [ ] **Step 5:** 运行 `uv run --project apps/backend pytest apps/backend/tests/contract/test_milvus_config.py apps/backend/tests/contract/test_milvus_filter.py -v`；预期 FAIL 为无法导入 `tap.modules.knowledge.adapters.milvus`。
- [ ] **Step 6:** 实现不可变配置和只允许以下字段/运算的私有 expression builder；literal 编码使用 JSON 双引号形式并拒绝控制字符，最终以 `len(expression.encode("utf-8"))` 检查 32 KiB。

  ```python
  _FILTER_FIELDS = frozenset(
      {
          "tenant_id",
          "project_id",
          "allowed_group_ids",
          "classification_rank",
          "environment",
          "corpus_version",
          "deleted",
          "source_id",
          "source_revision",
          "source_content_hash",
          "root_id",
          "parent_id",
          "logical_chunk_id",
      }
  )

  def _literal(value: str) -> str:
      if any(ord(character) < 0x20 for character in value):
          raise SearchBoundsExceeded("filter literal contains a control character")
      return json.dumps(value, ensure_ascii=False)
  ```
- [ ] **Step 7:** 重跑聚焦测试和 `make check && make test`。
- [ ] **Step 8:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/modules/knowledge/adapters/milvus/__init__.py apps/backend/src/tap/modules/knowledge/adapters/milvus/config.py apps/backend/src/tap/modules/knowledge/adapters/milvus/filter.py apps/backend/tests/contract/test_milvus_config.py apps/backend/tests/contract/test_milvus_filter.py
  git diff --cached --check
  git commit -m "feat: compile bounded milvus acl filters"
  ```

### Task 5: 隔离 SDK transport、绑定 alias 并严格映射结果

**Files:**

- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/transport.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/targets.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/mapping.py`
- Create: `apps/backend/tests/contract/test_milvus_transport.py`
- Create: `apps/backend/tests/contract/test_milvus_target_binding.py`
- Create: `apps/backend/tests/contract/test_milvus_mapping.py`

**Interfaces:**

- Consumes: Task 4 immutable config/target and Task 1 shared errors.
- Produces: provider-free request/row values, `MilvusReader`, `BoundMilvusTarget`, `bind_target` and `map_milvus_hit`; Task 6 never handles SDK objects.

```python
@dataclass(frozen=True, slots=True)
class BoundMilvusTarget:
    configured: MilvusIndexTarget
    physical_collection: str

@dataclass(frozen=True, slots=True)
class MilvusChannelRequest:
    kind: Literal["bm25", "dense"]
    query: str | tuple[float, ...]
    filter_expression: str
    limit: int

@dataclass(frozen=True, slots=True)
class MilvusHybridRequest:
    collection_name: str
    channels: tuple[MilvusChannelRequest, MilvusChannelRequest]
    output_fields: tuple[str, ...]
    limit: int

@dataclass(frozen=True, slots=True)
class MilvusQueryRequest:
    collection_name: str
    filter_expression: str
    output_fields: tuple[str, ...]
    limit: int

@dataclass(frozen=True, slots=True)
class MilvusCollectionDescriptor:
    collection_name: str
    family: SourceFamily
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int
    dynamic_fields_enabled: bool
    consistency_level: str

class MilvusReader(Protocol):
    async def describe_alias(self, alias: str) -> str: ...
    async def describe_collection(
        self, collection_name: str
    ) -> MilvusCollectionDescriptor: ...
    async def hybrid_search(self, request: MilvusHybridRequest) -> tuple[Mapping[str, object], ...]: ...
    async def query(self, request: MilvusQueryRequest) -> tuple[Mapping[str, object], ...]: ...
    async def close(self) -> None: ...

async def bind_target(reader: MilvusReader, target: MilvusIndexTarget) -> BoundMilvusTarget: ...
def map_milvus_hit(row: Mapping[str, object], bound: BoundMilvusTarget, local_rank: int) -> SearchHit: ...
```

**Steps:**

- [ ] **Step 1:** 写 fake-reader test，使用一个明确记录调用的对象锁定 alias 与 metadata 行为。真实 transport 从 closed collection description 解析 family/version 声明，并从 `describe_collection` fields/functions 与 `describe_index` 结果独立计算 `schema_sha256`；声明 digest、计算 digest 与 target expected digest 三者必须相同。

  ```python
  def doc_target() -> MilvusIndexTarget:
      return MilvusIndexTarget(
          family=SourceFamily.DOC,
          alias="kb_doc_active",
          physical_name_prefix="kb_doc_v1_",
          schema_version="doc-schema-v1",
          schema_sha256="sha256:" + "c" * 64,
          corpus_version="corpus-fixture-v1",
          embedding_model_version="research-embedding-v1",
          vector_dimension=1536,
      )

  class AliasReader:
      def __init__(self) -> None:
          self.alias_calls: list[str] = []

      async def describe_alias(self, alias: str) -> str:
          self.alias_calls.append(alias)
          return "kb_doc_v1_corpus_fixture_v1"

      async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
          return MilvusCollectionDescriptor(
              collection_name=collection_name,
              family=SourceFamily.DOC,
              schema_version="doc-schema-v1",
              schema_sha256="sha256:" + "c" * 64,
              corpus_version="corpus-fixture-v1",
              embedding_model_version="research-embedding-v1",
              vector_dimension=1536,
              dynamic_fields_enabled=False,
              consistency_level="Strong",
          )

  bound = await bind_target(AliasReader(), doc_target())
  assert bound.physical_collection == "kb_doc_v1_corpus_fixture_v1"
  ```

  2026-08-26 live probe 发现固定 Milvus `2.6.22` + PyMilvus `2.6.17` 的 BM25 `describe_index` transport 使用扁平设置。先补以下 RED cases，再最小修改同一个 `_canonical_indexes` 路径；publisher 不得另建 normalization 或 digest：

  | Case | Expected |
  | --- | --- |
  | 原 canonical nested `params` | 继续产生完全相同的 canonical index 与 schema digest |
  | 精确 BM25 identity，顶层 `bm25_b="0.75"`、`bm25_k1="1.2"`、`inverted_index_algo="DAAT_MAXSCORE"` | 严格解析两个有限数值，归一化为同一 nested `params`，digest 与 canonical fixture 相同 |
  | numeric BM25 值为 JSON number 或合法 finite numeric string | 只在这两个已知 BM25 key 上归一化；结果逐字段匹配 canonical 值 |
  | 未知顶层 key、重复 index identity/setting | fail closed，不计算可信 digest |
  | nested `params` 与任一 flat BM25 setting 并存，即使值相同 | fail closed，拒绝双重来源 |
  | 空白包围、空串、`NaN`、`Infinity`、`-Infinity`、hex 或其他非规范/非有限 numeric string | fail closed |
  | flat BM25 setting 出现在 FLAT/INVERTED、错误 metric/type 或其他 index | fail closed |
  | 其他字段中的 numeric string，或 `inverted_index_algo` 非字符串 | 不做通用 coercion，fail closed |

  可接受的 transport keys 保持闭合：基础字段只有 `field_name`、`index_name`、`index_type`、可选 `metric_type`、可选 `params`、`total_rows`、`indexed_rows`、`pending_index_rows`、`state`；只有上述精确 BM25 分支可增加三个 flat setting。该分支是 pinned live observation 的兼容层，不是新的 provider-wide 输入格式。

  同日后续 live descriptor probe 还发现 canonical `content` field 的 `params.enable_analyzer` 以精确小写字符串 `"true"` 返回。继续先补 RED cases，再只修改现有 collection-field canonicalization 路径；publisher 仍复用该路径和唯一 digest：

  | Case | Expected |
  | --- | --- |
  | canonical `content.params.enable_analyzer=true` 原生布尔值 | 继续产生完全相同的 canonical field 与 schema digest |
  | canonical `content.params.enable_analyzer="true"` 精确小写字符串 | 只把该 exact field/path/value 归一化为布尔 `true`，digest 与 canonical fixture 相同 |
  | `enable_analyzer` 出现在其他 field、field identity 错误或 key 位于错误路径 | fail closed |
  | 值为 `"false"`、`"True"`、`"TRUE"`、空串、空白包围或其他 boolean-like 字符串 | fail closed，不做大小写或 truthy coercion |
  | 值为 list/mapping 等 nested type，或其他 field param 使用 boolean-like string | fail closed，不做通用 string-to-bool coercion |
  | field/params 中出现未知或重复来源 | 继续按原闭合 shape fail closed |

  该分支只解释 pinned live transport，不改变 canonical fields/functions/indexes/consistency、schema digest 或声明值；错误 path/key/type 不得因为值看似 `true` 而被接受。
- [ ] **Step 2:** 写竞争测试：alias 在 bind 后切换，hybrid request 仍查询已绑定 physical name；不得再次用 alias 发 search。
- [ ] **Step 3:** 写 mapping tests，使用以下完整 row/bound factory，再逐键篡改；`anchor_json` 只能解析为 `DocumentAnchor` 的闭合字段。

  ```python
  def bound_target() -> BoundMilvusTarget:
      return BoundMilvusTarget(
          configured=doc_target(),
          physical_collection="kb_doc_v1_corpus_fixture_v1",
      )

  def valid_doc_row() -> dict[str, object]:
      return {
          "chunk_id": "h_" + "1" * 64,
          "logical_chunk_id": "h_" + "2" * 64,
          "root_id": "h_" + "3" * 64,
          "parent_id": None,
          "title": "Payment policy",
          "content": "Refunds require an approved request.",
          "content_role": "source",
          "index_family": "doc",
          "physical_collection": "kb_doc_v1_corpus_fixture_v1",
          "schema_version": "doc-schema-v1",
          "corpus_version": "corpus-fixture-v1",
          "embedding_model_version": "research-embedding-v1",
          "source_id": "blob:handbook/payment-policy",
          "source_type": "doc",
          "revision_kind": "blob_version",
          "source_revision": "2026-08-24T00:00:00Z",
          "source_content_hash": "sha256:" + "4" * 64,
          "chunk_content_hash": "sha256:" + "5" * 64,
          "anchor_json": '{"type":"document","headingPath":["Payments"],"page":1}',
          "derived_from_chunk_ids": [],
          "score": 0.75,
          "provider_request_id": "milvus-request-v1",
      }

  hit = map_milvus_hit(valid_doc_row(), bound_target(), local_rank=1)
  assert hit.family is SourceFamily.DOC
  assert hit.index_revision.physical_index == bound_target().physical_collection

  forged = {**valid_doc_row(), "physical_collection": "kb_doc_v1_forged"}
  with pytest.raises(SearchUnavailable, match="row does not match bound target"):
      map_milvus_hit(forged, bound_target(), local_rank=1)
  ```
- [ ] **Step 4:** 写 transport tests，证明 SDK 异常、deadline 与 cancellation 被归一化为共享错误，异常和 repr 不泄漏 URI 密码、filter、groups 或 vector。
- [ ] **Step 5:** 运行 `uv run --project apps/backend pytest apps/backend/tests/contract/test_milvus_transport.py apps/backend/tests/contract/test_milvus_target_binding.py apps/backend/tests/contract/test_milvus_mapping.py -v`；预期 FAIL 为三个模块尚不存在。
- [ ] **Step 6:** 实现 narrow protocol 和读取侧 PyMilvus wrapper；Knowledge 模块中只有 `transport.py` 导入 `pymilvus`。所有 SDK 调用通过同一 helper 执行并归一化异常；Task 7 的独立运维 client 不被 Knowledge application 导入。

  ```python
  async def _bounded_call[T](timeout_seconds: float, call: Callable[[], T]) -> T:
      try:
          async with asyncio.timeout(timeout_seconds):
              return await asyncio.to_thread(call)
      except TimeoutError as error:
          raise SearchUnavailable("search provider deadline exceeded") from error
      except SearchError:
          raise
      except Exception as error:
          raise SearchUnavailable("search provider call failed") from error
  ```
- [ ] **Step 7:** 实现 target binding 和严格 mapper；output fields 只含内容/provenance/版本，不含 ACL array 或 vector。
- [ ] **Step 8:** 重跑聚焦测试和 `make check && make test`。Task 5 修正只有在 nested 与 pinned-flat index、原生布尔与 pinned exact-string field 两组输入都产生同一 canonical digest，且全部拒绝矩阵 GREEN 后才完成；静态/fake GREEN 不能代替 Task 8 的真实重跑。
- [ ] **Step 9:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/modules/knowledge/adapters/milvus/transport.py apps/backend/src/tap/modules/knowledge/adapters/milvus/targets.py apps/backend/src/tap/modules/knowledge/adapters/milvus/mapping.py apps/backend/tests/contract/test_milvus_transport.py apps/backend/tests/contract/test_milvus_target_binding.py apps/backend/tests/contract/test_milvus_mapping.py
  git diff --cached --check
  git commit -m "feat: bind and validate milvus targets"
  ```

### Task 6: 实现 Milvus SearchPort 与显式 provider 装配

**Files:**

- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/search.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/readiness.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/milvus/audit.py`
- Create: `apps/backend/src/tap/entrypoints/knowledge_bootstrap.py`
- Create: `apps/backend/tests/contract/test_milvus_search_strict.py`
- Create: `apps/backend/tests/contract/test_milvus_readiness.py`
- Create: `apps/backend/tests/contract/test_milvus_audit.py`
- Create: `apps/backend/tests/contract/search_provider_conformance.py`
- Create: `apps/backend/tests/contract/test_search_provider_conformance.py`
- Modify: `apps/backend/tests/contract/test_azure_search_strict.py`
- Create: `apps/backend/tests/contract/test_search_bootstrap.py`
- Modify: `apps/backend/tests/architecture/test_module_boundaries.py`

**Interfaces:**

- Consumes: Tasks 4–5 filter/config/binding/transport/mapping and existing Azure adapter factory.
- Produces: `MilvusSearchAdapter` satisfying the unchanged `SearchPort`, reader-only `MilvusReadinessProbe`, fixed-shape `MilvusSearchAuditEvent`, plus the only provider-selection function `build_search_port`.
- Milvus settings keys are fixed to `TAP_SEARCH_BACKEND`, `MILVUS_URI`, `MILVUS_DATABASE`, `MILVUS_READER_USERNAME`, `MILVUS_READER_PASSWORD`, `TAP_MILVUS_DOC_ALIAS`, `TAP_MILVUS_DOC_PHYSICAL_PREFIX`, `TAP_MILVUS_DOC_SCHEMA_VERSION`, `TAP_MILVUS_DOC_SCHEMA_SHA256`, `TAP_MILVUS_DOC_CORPUS_VERSION`, `TAP_MILVUS_DOC_EMBEDDING_MODEL`, and `TAP_MILVUS_DOC_VECTOR_DIMENSION`.

```python
class MilvusSearchAdapter(SearchPort):
    def __init__(
        self, config: MilvusSearchConfig, reader: MilvusReader, audit_sink: SearchAuditSink
    ) -> None: ...
    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]: ...

def build_search_port(
    settings: Mapping[str, str],
    *,
    milvus_reader_factory: Callable[[MilvusSearchConfig], MilvusReader],
    azure_factory: Callable[[Mapping[str, str]], SearchPort],
    audit_sink: SearchAuditSink,
) -> SearchPort: ...

@dataclass(frozen=True, slots=True)
class MilvusReadinessCanary:
    chunk_id: str
    tenant_id: str
    project_id: str
    group_id: str
    corpus_version: str

class MilvusReadinessProbe:
    def __init__(
        self,
        target: MilvusIndexTarget,
        reader: MilvusReader,
        canary: MilvusReadinessCanary,
        timeout_seconds: float = 3.0,
    ) -> None: ...
    async def check(self) -> None: ...

@dataclass(frozen=True, slots=True)
class MilvusSearchAuditEvent:
    outcome: Literal["success", "failure"]
    provider: Literal["milvus"]
    query_plan_id: str
    acl_digest: str
    alias: str
    physical_collection: str | None
    schema_version: str
    corpus_version: str
    embedding_model_version: str
    provider_row_count: int
    rejected_row_count: int
    elapsed_milliseconds: int
    provider_request_ids: tuple[str, ...]
    error_code: Literal["unavailable", "bounds"] | None

class SearchAuditSink(Protocol):
    async def emit(self, event: MilvusSearchAuditEvent) -> None: ...

@dataclass(frozen=True, slots=True)
class ConformanceResult:
    channels: tuple[str, ...]
    outbound_filters: tuple[str, ...]
    expected_filter: str
    provider_rows: tuple[Mapping[str, object], ...]
    hits: tuple[SearchHit, ...]

class SearchProviderConformanceHarness(Protocol):
    provider_name: Literal["azure", "milvus"]
    async def run_case(self, case_id: str) -> ConformanceResult: ...
```

**Steps:**

- [ ] **Step 1:** 写 strict adapter test，reader 保存唯一 request；使用 Task 4 的 `doc_execution()` 与 Task 5 的 `valid_doc_row()`。

  ```python
  class RecordingReader:
      def __init__(self) -> None:
          self.requests: list[MilvusHybridRequest] = []

      async def describe_alias(self, alias: str) -> str:
          assert alias == "kb_doc_active"
          return "kb_doc_v1_corpus_fixture_v1"

      async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
          return MilvusCollectionDescriptor(
              collection_name=collection_name,
              family=SourceFamily.DOC,
              schema_version="doc-schema-v1",
              schema_sha256="sha256:" + "c" * 64,
              corpus_version="corpus-fixture-v1",
              embedding_model_version="research-embedding-v1",
              vector_dimension=1536,
              dynamic_fields_enabled=False,
              consistency_level="Strong",
          )

      async def hybrid_search(
          self, request: MilvusHybridRequest
      ) -> tuple[Mapping[str, object], ...]:
          self.requests.append(request)
          return (valid_doc_row(),)

  target = doc_target()
  config = MilvusSearchConfig(
      uri="http://127.0.0.1:19530",
      database="tap_local",
      username="tap_reader",
      password=SecretStr("tap-local-reader"),
      targets={SourceFamily.DOC: target},
  )
  reader = RecordingReader()
  audit = RecordingAuditSink()
  hits = await MilvusSearchAdapter(config, reader, audit).search(doc_execution())
  request = reader.requests.pop()
  bm25, dense = request.channels
  assert bm25.kind == "bm25"
  assert dense.kind == "dense"
  assert bm25.filter_expression == dense.filter_expression
  assert request.collection_name == "kb_doc_v1_corpus_fixture_v1"
  assert request.limit == 50
  assert tuple(hit.local_rank for hit in hits) == tuple(range(1, len(hits) + 1))
  ```
- [ ] **Step 2:** 覆盖空向量/维度错误、未配置 family、alias/metadata 漂移、extra/malformed row、超过返回上限与 transport failure；每项不得产生部分成功 hits。
- [ ] **Step 3:** 新建共享 conformance harness，把 Milvus fake reader 与 Azure controlled transport 归一化为上述 result。参数化用例至少包含 allowed、denied-group、wrong-tenant、wrong-project、over-classification、wrong-environment、wrong-corpus、resource-scope、unavailable；每个检索 channel 的 filter 必须与 harness 从同一 execution 生成的 `expected_filter` 字节一致。

  ```python
  @pytest.mark.asyncio
  @pytest.mark.parametrize("harness_factory", (azure_harness, milvus_harness), ids=("azure", "milvus"))
  @pytest.mark.parametrize(
      "case_id",
      ("denied-group", "wrong-tenant", "wrong-project", "over-classification",
       "wrong-environment", "wrong-corpus", "resource-scope"),
  )
  async def test_search_provider_acl_conformance(harness_factory, case_id: str) -> None:
      result = await harness_factory().run_case(case_id)
      assert result.outbound_filters
      assert set(result.outbound_filters) == {result.expected_filter}
      assert result.provider_rows == ()
      assert result.hits == ()
  ```
- [ ] **Step 4:** 写 bootstrap tests；factories 只递增各自计数，不连接外部服务。

  ```python
  settings = {
      "TAP_SEARCH_BACKEND": "milvus",
      "MILVUS_URI": "http://127.0.0.1:19530",
      "MILVUS_DATABASE": "tap_local",
      "MILVUS_READER_USERNAME": "tap_reader",
      "MILVUS_READER_PASSWORD": "tap-local-reader",
      "TAP_MILVUS_DOC_ALIAS": "kb_doc_active",
      "TAP_MILVUS_DOC_PHYSICAL_PREFIX": "kb_doc_v1_",
      "TAP_MILVUS_DOC_SCHEMA_VERSION": "doc-schema-v1",
      "TAP_MILVUS_DOC_SCHEMA_SHA256": "sha256:" + "c" * 64,
      "TAP_MILVUS_DOC_CORPUS_VERSION": "corpus-fixture-v1",
      "TAP_MILVUS_DOC_EMBEDDING_MODEL": "research-embedding-v1",
      "TAP_MILVUS_DOC_VECTOR_DIMENSION": "1536",
  }
  milvus_factory_calls: list[MilvusSearchConfig] = []
  azure_factory_calls: list[Mapping[str, str]] = []

  def recording_milvus_reader_factory(config: MilvusSearchConfig) -> MilvusReader:
      milvus_factory_calls.append(config)
      return RecordingReader()

  def unexpected_azure_factory(settings: Mapping[str, str]) -> SearchPort:
      azure_factory_calls.append(settings)
      raise AssertionError("Azure factory must not run for the Milvus selection")

  port = build_search_port(
      settings,
      milvus_reader_factory=recording_milvus_reader_factory,
      azure_factory=unexpected_azure_factory,
      audit_sink=RecordingAuditSink(),
  )
  assert isinstance(port, MilvusSearchAdapter)
  assert len(milvus_factory_calls) == 1
  assert azure_factory_calls == []

  with pytest.raises(ValueError, match="TAP_SEARCH_BACKEND"):
      build_search_port({}, milvus_reader_factory=recording_milvus_reader_factory,
                        azure_factory=unexpected_azure_factory,
                        audit_sink=RecordingAuditSink())
  ```
- [ ] **Step 5:** 写 architecture test，禁止 application/domain/contracts 导入 `pymilvus` 或 Milvus adapter。
- [ ] **Step 6:** 写 audit test，断言 success/failure 均恰好 emit 一次且 `asdict(event)` 键集合与上述 dataclass 相同；把 secret、group ID、compiled filter、query text、vector 放入异常/输入后，序列化 event 仍不含这些值。audit sink 写入失败时该 search 以共享 `SearchUnavailable` 失败，不返回 hits/citations；异常 detail 仍为通用文案。
- [ ] **Step 7:** 写 readiness test：只用 reader 调用一次 alias describe、一次 collection metadata 验证和一次按 canary chunk + 完整 ACL/corpus filter 的 bounded scalar query；缺失/多行/错误 chunk 或 timeout 均失败。测试同时断言 `create_app()` 的 liveness 构造不接收 Milvus client，provider 故障不触发 liveness 逻辑。
- [ ] **Step 8:** 运行 `uv run --project apps/backend pytest apps/backend/tests/contract/test_milvus_search_strict.py apps/backend/tests/contract/test_milvus_audit.py apps/backend/tests/contract/test_milvus_readiness.py apps/backend/tests/contract/test_search_bootstrap.py apps/backend/tests/contract/test_search_provider_conformance.py apps/backend/tests/architecture/test_module_boundaries.py -v`；预期 FAIL 为 search/audit/readiness/bootstrap/conformance 模块缺失。
- [ ] **Step 9:** 实现 adapter 的固定顺序，并在 provider I/O 前拒绝 target 数量不是 1 或 family 不是 `doc`。

  ```python
  if execution.plan.source_families != (SourceFamily.DOC,):
      raise SearchBoundsExceeded("source family is not configured")
  family = SourceFamily.DOC
  target = self._config.targets[family]
  filter_expression = compile_milvus_filter(
      execution, family, max_bytes=self._config.max_filter_bytes
  )
  bound = await bind_target(self._reader, target)
  rows = await self._reader.hybrid_search(
      _hybrid_request(execution, bound, filter_expression, self._config.candidate_limit)
  )
  return tuple(map_milvus_hit(row, bound, rank) for rank, row in enumerate(rows, 1))
  ```
- [ ] **Step 10:** 实现 audit、reader-only readiness probe 和独立 bootstrap factory；当前仓库没有 Knowledge HTTP composition root，因此本任务不伪造公开 route。实验 runner 使用 readiness，后续 composition root 必须把它接到 readiness 而不是 liveness。
- [ ] **Step 11:** 重跑聚焦测试和 `make check && make test`。
- [ ] **Step 12:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/modules/knowledge/adapters/milvus/search.py apps/backend/src/tap/modules/knowledge/adapters/milvus/readiness.py apps/backend/src/tap/modules/knowledge/adapters/milvus/audit.py apps/backend/src/tap/entrypoints/knowledge_bootstrap.py apps/backend/tests/contract/test_milvus_search_strict.py apps/backend/tests/contract/test_milvus_readiness.py apps/backend/tests/contract/test_milvus_audit.py apps/backend/tests/contract/search_provider_conformance.py apps/backend/tests/contract/test_search_provider_conformance.py apps/backend/tests/contract/test_azure_search_strict.py apps/backend/tests/contract/test_search_bootstrap.py apps/backend/tests/architecture/test_module_boundaries.py
  git diff --cached --check
  git commit -m "feat: add selectable milvus search adapter"
  ```

### Task 7: 建立本地 Compose、三角色 RBAC 与行为健康探针

**Files:**

- Modify: `compose.yaml`
- Modify: `.env.example`
- Create: `deploy/local/milvus/milvus.yaml`
- Create: `apps/backend/src/tap/operations/__init__.py`
- Create: `apps/backend/src/tap/operations/milvus/__init__.py`
- Create: `apps/backend/src/tap/operations/milvus/contracts.py`
- Create: `apps/backend/src/tap/operations/milvus/client.py`
- Create: `apps/backend/src/tap/operations/milvus/bootstrap.py`
- Create: `apps/backend/src/tap/operations/milvus/health.py`
- Create: `scripts/milvus_bootstrap.py`
- Create: `scripts/milvus_health_probe.py`
- Create: `apps/backend/tests/unit/operations/test_milvus_bootstrap.py`
- Create: `apps/backend/tests/unit/operations/test_milvus_health.py`
- Modify: `scripts/check-local-services.sh`
- Modify: `Makefile`

**Compose contract:**

```text
milvus-etcd  quay.io/coreos/etcd:v3.5.25
milvus-minio minio/minio:RELEASE.2024-12-18T13-15-44Z
milvus       milvusdb/milvus:v2.6.22, command: milvus run standalone
host ports   127.0.0.1:${MILVUS_PORT:-19530}:19530
              127.0.0.1:${MILVUS_HEALTH_PORT:-9091}:9091
```

**Interfaces:**

- Consumes: Task 3 exact server/SDK versions and Task 5 transport-compatible reader behavior.
- Produces: local `milvus` Compose profile, three least-privilege identities, idempotent `bootstrap_local_rbac(...)`, and destructive-isolated `run_health_probe(...)` used by Task 10.

```python
@dataclass(frozen=True, slots=True)
class MilvusRoleCredentials:
    rotated_root_password: SecretStr
    reader_username: str
    reader_password: SecretStr
    writer_username: str
    writer_password: SecretStr
    provisioner_username: str
    provisioner_password: SecretStr

@dataclass(frozen=True, slots=True)
class MilvusGrant:
    resource_level: Literal["instance", "database", "collection"]
    resource_name: str
    privilege: str

READER_BASE_PRIVILEGES = frozenset({"DescribeAlias", "DescribeCollection"})
READER_TARGET_PRIVILEGES = frozenset({"Search", "Query"})
WRITER_PRIVILEGES = frozenset(
    {"Insert", "Upsert", "Delete", "Flush", "GetFlushState"}
)
PROVISIONER_PRIVILEGES = frozenset(
    {
        "CreateCollection", "DropCollection", "CreateIndex", "IndexDetail", "Load",
        "Release", "GetLoadState", "GetLoadingProgress", "CreateAlias", "DropAlias",
        "DescribeAlias", "ManageOwnership", "SelectOwnership",
    }
)
PROVISIONER_GLOBAL_BASE_PRIVILEGES = frozenset(
    {
        "CreateAlias", "CreateCollection", "DescribeAlias", "DropAlias",
        "DropCollection", "ManageOwnership", "SelectOwnership",
    }
)
PROVISIONER_COLLECTION_BASE_PRIVILEGES = frozenset(
    {
        "CreateIndex", "GetLoadState", "GetLoadingProgress",
        "IndexDetail", "Load", "Release",
    }
)

class MilvusAdmin(Protocol):
    async def ensure_user(self, username: str, password: SecretStr) -> None: ...
    async def ensure_role(self, role_name: str) -> None: ...
    async def replace_role_grants(
        self, role_name: str, grants: frozenset[MilvusGrant]
    ) -> None: ...
    async def rotate_root_password(self, password: SecretStr) -> None: ...

class MilvusProvisioner(Protocol):
    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None: ...
    async def create_indexes(self, name: str) -> None: ...
    async def grant_collection(self, name: str, role_name: str) -> None: ...
    async def revoke_collection(self, name: str, role_name: str) -> None: ...
    async def alter_alias(self, alias: str, collection_name: str) -> None: ...
    async def describe_alias(self, alias: str) -> str: ...
    async def drop_alias(self, alias: str) -> None: ...
    async def drop_collection(self, name: str) -> None: ...

class MilvusWriter(Protocol):
    async def insert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None: ...
    async def upsert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None: ...
    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None: ...
    async def flush(self, name: str) -> None: ...

@dataclass(frozen=True, slots=True)
class MilvusProbeClients:
    admin: MilvusAdmin
    provisioner: MilvusProvisioner
    writer: MilvusWriter
    reader: MilvusReader

@dataclass(frozen=True, slots=True)
class MilvusPublishClients:
    provisioner: MilvusProvisioner
    writer: MilvusWriter
    reader: MilvusReader

@dataclass(frozen=True, slots=True)
class MilvusHealthReport:
    probe_id: str
    allowed_hits: int
    denied_hits: int
    cleanup_complete: bool

async def bootstrap_local_rbac(admin: MilvusAdmin, credentials: MilvusRoleCredentials) -> None:
    """Create/rotate the three local roles idempotently."""

async def run_health_probe(clients: MilvusProbeClients, probe_id: str) -> MilvusHealthReport:
    """Exercise an isolated probe collection and remove only that collection."""
```

**Steps:**

- [ ] **Step 1:** 写 unit tests，以 recording admin client 断言 bootstrap 创建三个身份且授权矩阵不交叉；reader 的两个 Describe privilege 精确落在 `Global/*` base grants；provisioner 的 `Global/*` 精确为 `CreateAlias`、`CreateCollection`、`DescribeAlias`、`DropAlias`、`DropCollection`、`ManageOwnership`、`SelectOwnership`，`Collection/*` 精确为 `CreateIndex`、`GetLoadState`、`GetLoadingProgress`、`IndexDetail`、`Load`、`Release`，任何缺项、额外项或互换 dimension 都失败，不能泛化。新增 provider-like tests：缺少 `SelectOwnership` 时 provisioner `describe_role` 必须得到明确 denial；加入后只允许安全 grant inventory，reader/writer 仍 denial；精确七项/六项 inventory 重复 reconcile 必须 idle，每个 wrong scope、missing/extra/duplicate record 都按闭合 contract 精确纠正或 fail closed。`replace_role_grants` 只收敛 base namespace：合法的 reader concrete `Search`/`Query` 和临时 writer concrete grants 必须原样保留；provisioner concrete、越权 concrete 或所有权不明记录必须 fail closed 且不得被宽泛删除。reader legacy `Collection/*` `Search`/`Query` wildcard 必须被精确撤销且不是新 base/target contract。第二次 bootstrap 不得产生 grant mutation，并增加“带合法在途/已发布 target grants 重跑 bootstrap”的 idempotence case。

  ```python
  await bootstrap_local_rbac(admin, local_role_credentials())
  assert admin.role_base_privileges("tap_reader") == {
      "DescribeAlias", "DescribeCollection"
  }
  assert admin.role_scoped_privileges("tap_reader") == set()
  assert READER_TARGET_PRIVILEGES == {"Search", "Query"}
  assert admin.role_privileges("tap_writer") == {
      "Insert", "Upsert", "Delete", "Flush", "GetFlushState"
  }
  assert admin.role_privileges("tap_provisioner") == PROVISIONER_PRIVILEGES
  assert admin.role_global_base_privileges("tap_provisioner") == {
      "CreateAlias", "CreateCollection", "DescribeAlias",
      "DropAlias", "DropCollection", "ManageOwnership", "SelectOwnership",
  }
  assert admin.role_collection_base_privileges("tap_provisioner") == {
      "CreateIndex", "GetLoadState", "GetLoadingProgress",
      "IndexDetail", "Load", "Release",
  }
  assert "Search" not in admin.role_privileges("tap_writer")
  assert "Insert" not in admin.role_privileges("tap_reader")
  assert admin.password_rotations == [("root", "tap-local-rotated-root")]
  ```
- [ ] **Step 2:** 写 health orchestration test：provisioner 创建隔离 collection/alias 并授权，writer insert/flush/delete，reader describe + filtered hybrid/query，最后 provisioner 清理。

  ```python
  report = await run_health_probe(probe_clients(), "probe_20260824_001")
  assert report.allowed_hits == 1
  assert report.denied_hits == 0
  assert report.cleanup_complete is True
  assert all(name.startswith("tap_health_probe_") for name in admin.dropped_collections)
  ```
- [ ] **Step 3:** 运行 `uv run --project apps/backend pytest apps/backend/tests/unit/operations/test_milvus_bootstrap.py apps/backend/tests/unit/operations/test_milvus_health.py -v`；预期 FAIL 为 operations package 缺失。
- [ ] **Step 4:** 增加 `milvus` profile、命名 volumes、auth 配置和以下精确服务镜像；etcd/MinIO 不发布宿主端口。把 MySQL、Redis、Azurite、LiteLLM 的现有宿主端口也改为 `127.0.0.1`。

  ```yaml
  milvus-etcd:
    profiles: [milvus]
    image: quay.io/coreos/etcd:v3.5.25
    environment:
      ETCD_AUTO_COMPACTION_MODE: revision
      ETCD_AUTO_COMPACTION_RETENTION: "1000"
      ETCD_QUOTA_BACKEND_BYTES: "4294967296"
      ETCD_SNAPSHOT_COUNT: "50000"
    command: etcd --advertise-client-urls=http://127.0.0.1:2379 --listen-client-urls=http://0.0.0.0:2379 --data-dir=/etcd
    volumes: [milvus-etcd-data:/etcd]
  milvus-minio:
    profiles: [milvus]
    image: minio/minio:RELEASE.2024-12-18T13-15-44Z
    environment:
      MINIO_ROOT_USER: ${MILVUS_MINIO_ROOT_USER:-tap-local-minio}
      MINIO_ROOT_PASSWORD: ${MILVUS_MINIO_ROOT_PASSWORD:-tap-local-minio-password}
    command: minio server /minio_data
    volumes: [milvus-minio-data:/minio_data]
  milvus:
    profiles: [milvus]
    image: milvusdb/milvus:v2.6.22
    command: [milvus, run, standalone]
    environment:
      ETCD_ENDPOINTS: milvus-etcd:2379
      MINIO_ADDRESS: milvus-minio:9000
      MQ_TYPE: woodpecker
    depends_on: [milvus-etcd, milvus-minio]
    ports:
      - "127.0.0.1:${MILVUS_PORT:-19530}:19530"
      - "127.0.0.1:${MILVUS_HEALTH_PORT:-9091}:9091"
    volumes:
      - milvus-data:/var/lib/milvus
      - ./deploy/local/milvus/milvus.yaml:/milvus/configs/milvus.yaml:ro
    healthcheck:
      test: [CMD, curl, -f, http://127.0.0.1:9091/healthz]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s
  ```

  `deploy/local/milvus/milvus.yaml` 的安全开关固定为：

  ```yaml
  common:
    security:
      authorizationEnabled: true
  ```
- [ ] **Step 5:** `.env.example` 增加 `MILVUS_MINIO_ROOT_USER=tap-local-minio`、`MILVUS_MINIO_ROOT_PASSWORD=tap-local-minio-password`、`MILVUS_INITIAL_ROOT_PASSWORD=Milvus`、`MILVUS_ROOT_PASSWORD=tap-local-rotated-root`、`TAP_ALLOW_INITIAL_MILVUS_ROOT=0`，以及 `MILVUS_READER_*`、`MILVUS_WRITER_*`、`MILVUS_PROVISIONER_*` 的 `tap-local-*` 用户名/密码。首次创建 volume 只能在命令行显式设 `TAP_ALLOW_INITIAL_MILVUS_ROOT=1`；bootstrap 先尝试 rotated root，只有该开关为 1 才尝试初始 root，成功后立即轮换。root/MinIO 变量只供 Compose/bootstrap，reader 配置不继承 root、MinIO、writer 或 provisioner。
- [ ] **Step 6:** 实现可注入 client 的 bootstrap/health orchestration 与薄 CLI。CLI 只解析参数、构造 client、调用 operation，并用返回码 `0/1` 表示通过/失败；普通 service check 仅在 `TAP_SEARCH_BACKEND=milvus` 或 `--milvus` 时运行 bounded reader canary。
- [ ] **Step 7:** 对上述每项 privilege 运行真实 allow probe，并对 reader 的 Insert/Delete、writer 的 Search/Query、provisioner 的实体读写运行真实 deny probe。真实 inventory 必须证明 reader 的 `DescribeAlias`/`DescribeCollection` 只有 `Global/*` bootstrap base records；provisioner 必须精确呈现 `Global/*` 的 `CreateAlias`、`CreateCollection`、`DescribeAlias`、`DropAlias`、`DropCollection`、`ManageOwnership`、`SelectOwnership` 与 `Collection/*` 的 `CreateIndex`、`GetLoadState`、`GetLoadingProgress`、`IndexDetail`、`Load`、`Release`，不得接受其他二分。真实 probe 必须证明 `SelectOwnership` 仅使 provisioner 的 `describe_role`/grant inventory 成功，缺少该项时得到明确 permission denial，reader/writer 仍不能读取 grant inventory；`ManageOwnership` 的 grant/revoke mutation 行为保持独立。每个 probe/fixture target 的 reader records 只有 `Search`/`Query` 且同时匹配 `object_type=Collection`、exact database/name/role。同名不同 database/object type/role、以 target 名称出现的 `Global` record 或额外 privilege 都失败。初始只读 preflight 必须证明 zero concrete grants/resources；若只缺少已裁决的 `SelectOwnership`，把该十二项 provisioner base 记录为待 bootstrap 精确补齐的 known legacy state。bootstrap 后 base counts 必须为 reader/writer/provisioner `2/5/13`，并以合法 concrete reader/writer grants 存在的状态重跑，证明 base reconciliation 不撤销 publisher-owned grants，且第二次运行无 grant mutation。注入错误 base 与异常 concrete records，分别证明精确纠错和 fail-closed ownership。`alter_alias` 必须由 `CreateAlias` grant 的实际 probe 证明；若 v2.6.22 行为不同，停止并更新计划，禁止附加 `CollectionAdmin`、`ClusterAdmin` 或 `All` 绕过。
- [ ] **Step 8:** 增加 `milvus-up`、`milvus-down`、`milvus-bootstrap`、`milvus-health` Make targets；`milvus-up` 先检查 2 vCPU/8 GiB，`milvus-down` 默认保留 volumes。
- [ ] **Step 9:** 运行 `docker compose config`、unit tests、`make check && make test`。在全新本地 volume 上再运行 `make milvus-up && TAP_ALLOW_INITIAL_MILVUS_ROOT=1 make milvus-bootstrap && make milvus-health`；已有 rotated root 的 volume 运行时不得设置该开关。
- [ ] **Step 10:** 只暂存本任务文件并提交。

  ```sh
  git add compose.yaml .env.example deploy/local/milvus/milvus.yaml apps/backend/src/tap/operations/__init__.py apps/backend/src/tap/operations/milvus/__init__.py apps/backend/src/tap/operations/milvus/contracts.py apps/backend/src/tap/operations/milvus/client.py apps/backend/src/tap/operations/milvus/bootstrap.py apps/backend/src/tap/operations/milvus/health.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py apps/backend/tests/unit/operations/test_milvus_bootstrap.py apps/backend/tests/unit/operations/test_milvus_health.py scripts/check-local-services.sh Makefile
  git diff --cached --check
  git commit -m "infra: add authenticated local milvus"
  ```

### Task 8: 建立脱敏 fixture、可重建 publisher 与 alias 发布

**Files:**

- Create: `apps/backend/src/tap/operations/milvus/fixtures.py`
- Create: `apps/backend/src/tap/operations/milvus/activation.py`
- Create: `apps/backend/src/tap/operations/milvus/publish.py`
- Create: `scripts/milvus_fixture.py`
- Create: `apps/backend/tests/fixtures/milvus/doc-fixture-v1.json`
- Create: `apps/backend/tests/fixtures/milvus/query-cases-v1.json`
- Create: `apps/backend/tests/unit/operations/test_milvus_fixtures.py`
- Create: `apps/backend/tests/unit/operations/test_milvus_publish.py`

**Fixture contract:**

**Interfaces:**

- Consumes: Task 7 writer/provisioner clients and Task 4 target metadata contract.
- Produces: strict fixture manifest/query schema, deterministic IDs/hashes, `publish_fixture(...) -> PublishReceipt`, and an alias-safe rebuild path consumed by Tasks 9–10.

```python
@dataclass(frozen=True, slots=True)
class DocFixtureChunk:
    chunk_id: str
    logical_chunk_id: str
    root_id: str
    parent_id: str | None
    title: str
    content: str
    content_role: Literal["source"]
    tenant_id: str
    project_id: str
    allowed_group_ids: tuple[str, ...]
    classification_rank: int
    environment: str
    deleted: bool
    source_id: str
    source_revision: str
    source_content_hash: str
    chunk_content_hash: str
    anchor_json: str

@dataclass(frozen=True, slots=True)
class QueryCase:
    case_id: str
    query: str
    tenant_id: str
    project_id: str
    group_ids: tuple[str, ...]
    classification_ceiling: Classification
    environment: str | None
    expected_source_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class DocFixtureManifest:
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int
    physical_collection: str
    alias: str
    chunks: tuple[DocFixtureChunk, ...]

@dataclass(frozen=True, slots=True)
class PublishReceipt:
    physical_collection: str
    alias: str
    row_count: int
    manifest_sha256: str
    corpus_version: str
    activation_id: str

class PublishRejected(Exception):
    """Fixture validation, reconciliation, or safety probes rejected publication."""

class CorpusActivator(Protocol):
    async def activate(
        self,
        corpus_version: str,
        physical_collection: str,
        manifest_sha256: str,
    ) -> str: ...

async def publish_fixture(
    clients: MilvusPublishClients,
    manifest: DocFixtureManifest,
    vectors_by_chunk_id: Mapping[str, tuple[float, ...]],
    activator: CorpusActivator,
) -> PublishReceipt:
    """Publish only after reconciliation and positive/negative probes pass."""
```

`doc-schema-v1` 关闭 dynamic fields、使用 `Strong` consistency，字段定义固定如下；除 `title`、`parent_id` 外均 non-null：

| Field | Milvus type and bound |
| --- | --- |
| `chunk_id` | `VARCHAR(66)`, primary, no auto ID |
| `logical_chunk_id`, `root_id` | `VARCHAR(66)` |
| `parent_id` | nullable `VARCHAR(66)` |
| `title` | nullable `VARCHAR(1024)` |
| `content` | `VARCHAR(32768)`, analyzer enabled |
| `content_role` | `VARCHAR(32)` |
| `tenant_id`, `project_id` | `VARCHAR(256)` |
| `allowed_group_ids` | `ARRAY<VARCHAR(256)>`, capacity 128 |
| `classification_rank` | `INT8` constrained by loader to `0..3` |
| `environment` | `VARCHAR(128)` |
| `deleted` | `BOOL` |
| `index_family` | `VARCHAR(16)`, value exactly `doc` |
| `physical_collection` | `VARCHAR(255)` |
| `corpus_version`, `schema_version`, `embedding_model_version` | `VARCHAR(256)` |
| `source_id` | `VARCHAR(1024)` |
| `source_type` | `VARCHAR(32)`, value exactly `doc` |
| `revision_kind` | `VARCHAR(32)`, value exactly `blob_version` |
| `source_revision` | `VARCHAR(512)` |
| `source_content_hash`, `chunk_content_hash` | `VARCHAR(71)`, canonical `sha256:` digest |
| `anchor_json` | `VARCHAR(16384)`, closed `DocumentAnchor` JSON |
| `derived_from_chunk_ids` | `ARRAY<VARCHAR(66)>`, capacity 256 |
| `bm25_sparse` | `SPARSE_FLOAT_VECTOR`, generated by `content_bm25_v1` |
| `dense_vector` | `FLOAT_VECTOR(1536)` |

Analyzer 与 index 参数必须逐字写入 schema builder 和 schema digest：

```python
CONTENT_ANALYZER = {
    "tokenizer": {
        "type": "language_identifier",
        "identifier": "whatlang",
        "analyzers": {
            "default": {"tokenizer": "standard"},
            "English": {"type": "english"},
            "Mandarin": {"tokenizer": "jieba", "filter": ["cnalphanumonly"]},
        },
    }
}
BM25_FUNCTION = {
    "name": "content_bm25_v1",
    "function_type": "BM25",
    "input_field_names": ["content"],
    "output_field_names": ["bm25_sparse"],
}
INDEXES = {
    "dense_vector": {"index_type": "FLAT", "metric_type": "COSINE", "params": {}},
    "bm25_sparse": {
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "BM25",
        "params": {"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
    },
    **{
        field: {"index_type": "INVERTED"}
        for field in (
            "tenant_id", "project_id", "allowed_group_ids", "classification_rank",
            "environment", "corpus_version", "deleted",
        )
    },
}

def collection_description(manifest: DocFixtureManifest) -> str:
    metadata = {
        "family": "doc",
        "schemaVersion": manifest.schema_version,
        "schemaSha256": manifest.schema_sha256,
        "corpusVersion": manifest.corpus_version,
        "embeddingModelVersion": manifest.embedding_model_version,
        "vectorDimension": manifest.vector_dimension,
    }
    return "tap-collection-metadata-v1:" + json.dumps(
        metadata, sort_keys=True, separators=(",", ":")
    )
```

Task 7 bootstrap 的真实行为探针先对 4 段固定中英文文本运行 `run_analyzer`，Task 10 再以 8 个 query cases 验证 BM25。若 analyzer 输出或 BM25 API 与上述固定配置不兼容，停止并把 schema version 改动送回计划/RFC 审查，不在运行时回退到另一 analyzer。

2026-08-26 的首次 Task 8 live publish 在 insert/flush 后、safety query 与 alias mutation 前，被 Task 5 对 pinned `describe_index` 扁平 BM25 transport 的严格拒绝所停止。Task 5 按闭合 TDD 矩阵修正后，第二次 live publish 通过 analyzer/create/index，但在 insert 前被 reader scoped-grant reconciliation 停止：固定组合把错误重复授予 target 的 `DescribeAlias`/`DescribeCollection` 表达为 `Global`，而 publisher 当时要求全部 target grants 为 `Collection`。只读 descriptor 诊断同时发现 canonical `content.params.enable_analyzer` 的布尔真值以精确字符串 `"true"` 返回。第三次只读 zero-state preflight 又发现 provisioner base inventory 的精确六项 `Global/*`/六项 `Collection/*` 二分超出当时只允许 `DescribeAlias` 为 Global 的契约；preflight 同时证明 zero collections、aliases、concrete grants、marker，reader 的 legacy `Collection/*` `Search`/`Query` wildcard 只是待 bootstrap 纠正的旧状态。第四次 live rerun 已由 bootstrap 收敛 reader 并复验 `2/5/12`，但 publisher 在 insert 前调用 provisioner `describe_role` 时得到 permission denied，错误明确要求 `PrivilegeSelectOwnership`；exact-name finalizer 再次恢复 zero resources/concrete grants/marker。上述行为都必须先回到 RFC/计划形成闭合规则；它们不是官方 transport 或 privilege 层级声明。

Task 8 live acceptance 继续保持 BLOCKED，直到 Task 5 的 exact field normalization、Task 7/8 的 reader base/target 分离，以及 provisioner 精确七项 `Global/*`/六项 `Collection/*` base 二分都完成 TDD 修正。随后必须从已验证的零 collection、零 alias、零 concrete/scoped grant、无 active marker 状态开始：先让 bootstrap 补齐 `SelectOwnership` 并收敛 base counts 到 `2/5/13`，再以 provisioner 而非 root/admin 运行安全 grant inventory，最后重跑本 Task 的完整 publish/rebuild/alias/manifest/grant 对账；不得复用任何部分发布结果，也不得绕过 publisher 自身的 descriptor 或 grant inventory。若真实返回超出 RFC-004 的闭合映射，再次停止并回到 RFC/计划审查，不能扩大版本、字段、权限或 coercion。

**Steps:**

- [ ] **Step 1:** 创建 12 个完全虚构的中文/英文 `doc` chunks 与 8 条 query cases，覆盖两个 tenant、两个 project、多个 groups、classification、environment/global、subtree、撤权与删除。使用以下规范计算稳定 ID/hash。

  ```python
  def sha256_id(value: str) -> str:
      normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
      return "h_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()

  def content_hash(value: str) -> str:
      normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
      return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
  ```

  Manifest 的 12 条输入固定如下；`classification_rank` 映射为 public=0、internal=1、confidential=2、restricted=3，publisher 为每行注入 manifest 的 family/physical/schema/corpus/model 字段：

  | source ID | title/content summary | tenant/project | groups | rank | environment | deleted |
  | --- | --- | --- | --- | --- | --- | --- |
  | `blob:fixture/payment/refund` | 退款须由付款组审批；中文 | `tenant-a/project-a` | `group-payments` | 1 | `production` | false |
  | `blob:fixture/payment/limit` | 大额付款需要双人复核；中文 | `tenant-a/project-a` | `group-payments,group-audit` | 2 | `global` | false |
  | `blob:fixture/payment/archive` | 旧版付款规则；中文 | `tenant-a/project-a` | `group-payments` | 1 | `production` | true |
  | `blob:fixture/payment/root` | 付款手册根章节；中文 | `tenant-a/project-a` | `group-payments` | 0 | `global` | false |
  | `blob:fixture/payment/card` | 根章节下银行卡子章节；中文 | `tenant-a/project-a` | `group-payments` | 1 | `production` | false |
  | `blob:fixture/payment/wire` | 根章节下电汇子章节；中文 | `tenant-a/project-a` | `group-treasury` | 2 | `production` | false |
  | `blob:fixture/release/rollback` | Rollback requires a recorded approval; English | `tenant-a/project-a` | `group-release` | 1 | `staging` | false |
  | `blob:fixture/release/canary` | Canary rollout steps; English | `tenant-a/project-a` | `group-release` | 0 | `global` | false |
  | `blob:fixture/security/keys` | Key rotation procedure; English | `tenant-a/project-a` | `group-security` | 3 | `production` | false |
  | `blob:fixture/other-project/budget` | Project Atlas budget; English | `tenant-a/project-b` | `group-payments` | 1 | `production` | false |
  | `blob:fixture/other-tenant/refund` | Tenant B refund policy; English | `tenant-b/project-a` | `group-payments` | 1 | `production` | false |
  | `blob:fixture/public/support` | Public support contact procedure; English | `tenant-a/project-a` | `group-support` | 0 | `global` | false |

  Query cases 固定为：`refund-allowed`、`payment-global-allowed`、`payment-wrong-group`、`payment-wrong-project`、`payment-wrong-tenant`、`security-over-classification`、`release-wrong-environment`、`payment-subtree-card-only`。每项保存 query、可信 policy inputs 和精确 `expected_source_ids`；六个 negative/scope case 的期望集合可为空或只含 card source，不使用模糊 relevance label。
- [ ] **Step 2:** 写 manifest/schema tests：拒绝重复/额外 ID、错误 `h_` SHA-256、hash/provenance 不匹配、动态字段、非 `doc` source type、错误 physical/index family、混合模型或维度。将 fields/functions/indexes/consistency 的 canonical JSON 独立计算为 `schema_sha256`；manifest、collection description 和实际 describe 结果的 digest 任一不一致都拒绝发布，description 出现额外 key 也拒绝。
- [ ] **Step 3:** 写 recording-client publisher tests，证明流程固定且验证失败时不切 alias。reader bootstrap base inventory 必须已经精确为 `Global/*` 的 Describe grants；publisher 只为新 target 授予 `Search`/`Query`，并按 `Collection` + exact database/name/role 对账，拒绝 `Global` target record、冲突维度或额外 privilege。增加所有权/idempotence cases：bootstrap 重跑保留 publisher-owned reader/writer concrete grants；publisher 相同 manifest 重跑仍精确收敛；provisioner concrete 或越权/歧义 concrete inventory 不能由 bootstrap 或 publisher 静默合法化。

  ```python
  receipt = await publish_fixture(clients, manifest(), unit_vectors(), activator)
  assert clients.events == [
      "create_collection", "create_indexes", "grant_writer", "grant_reader", "insert",
      "flush", "reconcile", "positive_probe", "negative_probe", "alter_alias",
      "verify_alias", "activate_corpus", "revoke_writer",
  ]
  assert receipt.row_count == 12

  clients.fail_negative_probe = True
  with pytest.raises(PublishRejected):
      await publish_fixture(clients, manifest(), unit_vectors(), activator)
  assert "alter_alias" not in clients.events_after_reset
  ```
- [ ] **Step 4:** 增加 ACL 收紧测试：先 upsert metadata/`deleted=true`，用 strong query 证明旧主体零命中后才产生 receipt；physical delete 不是授权生效条件。
- [ ] **Step 5:** 增加 rebuild/rollback-window 测试：相同 manifest 生成相同 collection schema、IDs、hashes、ACL/provenance counts；alias verify 后用 temp-file + `os.replace` 原子写 `.local/milvus-active-corpus.json`，随后立即撤销新 physical 的 writer 权限。旧 reader 权限与 collection 保留到显式 `finalize --old-physical <exact-name>`，该命令先撤 reader 再删除；普通 publish/down 不清理旧 collection 或 volume。
- [ ] **Step 6:** 运行 `uv run --project apps/backend pytest apps/backend/tests/unit/operations/test_milvus_fixtures.py apps/backend/tests/unit/operations/test_milvus_publish.py -v`；预期 FAIL 为 fixture/publish 模块缺失。随后实现严格 JSON loader、schema builder、reconciler 和 publisher CLI。
- [ ] **Step 7:** 重跑聚焦测试和 `make check && make test`；确认 Task 5 exact field normalization 与 Task 7/8 base/target grant inventory TDD 均 GREEN 后，从零 collection、零 alias、零 concrete/scoped grant、无 active marker 状态开始完整 live workflow。只读 preflight 可以把仅缺少 `SelectOwnership` 的 provisioner 十二项 base 识别为待 bootstrap 补齐的 known legacy state；bootstrap 后必须对账 reader/writer/provisioner exact base counts `2/5/13`，重复 bootstrap 无 grant mutation，并由 provisioner 自身成功执行 `describe_role`/grant inventory，reader/writer 保持 denial。随后重跑真实 analyzer、publish、相同 manifest idempotence、rebuild、alias/marker activation、reader/writer grant reconciliation、bootstrap-with-concrete-grants idempotence 与显式旧 target cleanup。live evidence 必须记录 canonical schema digest 不变、reader base grants 只有 `Global/*` Describe、provisioner 精确七项 `Global/*` 与六项 `Collection/*`、active target grants 只有 exact `Collection` Search/Query、bootstrap 保留合法 concrete reader/writer grants、writer 最终撤销和 final zero-state reconciliation。任一 runtime binding、transport shape、grant dimension 或所有权不一致都保持 BLOCKED。
- [ ] **Step 8:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/src/tap/operations/milvus/fixtures.py apps/backend/src/tap/operations/milvus/activation.py apps/backend/src/tap/operations/milvus/publish.py scripts/milvus_fixture.py apps/backend/tests/fixtures/milvus/doc-fixture-v1.json apps/backend/tests/fixtures/milvus/query-cases-v1.json apps/backend/tests/unit/operations/test_milvus_fixtures.py apps/backend/tests/unit/operations/test_milvus_publish.py
  git diff --cached --check
  git commit -m "feat: publish deterministic milvus fixtures"
  ```

### Task 9: 生成有界真实 embedding 与版本化 CI vector snapshot

**Files:**

- Modify: `deploy/local/litellm/config.yaml`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/models.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- Create: `apps/backend/src/tap/operations/milvus/embeddings.py`
- Create: `scripts/milvus_embedding_research.py`
- Create: `apps/backend/tests/fixtures/milvus/vectors-research-embedding-v1.json`
- Create: `apps/backend/tests/unit/operations/test_milvus_embeddings.py`
- Modify: `apps/backend/tests/contract/test_litellm_strict.py`
- Modify: `Makefile`

**Embedding contract:**

**Interfaces:**

- Consumes: Task 8 sanitized manifest/query cases and existing `ModelPort.embed(str) -> Embedding` through LiteLLM.
- Produces: content-addressed ignored cache, redacted research report, and committed `VectorSnapshot` in the exact 1536-dimensional model space consumed by Task 10.

```python
EMBEDDING_ALIAS = "research-embedding-v1"
EMBEDDING_DIMENSION = 1536
DEFAULT_MAX_CHUNKS = 100
DEFAULT_MAX_QUERIES = 20
HARD_MAX_CHUNKS = 500
HARD_MAX_QUERIES = 100

def embedding_cache_key(model_id: str, dimension: int, content_hash: str) -> str:
    payload = f"{model_id}\x00{dimension}\x00{content_hash}".encode("utf-8")
    return "h_" + hashlib.sha256(payload).hexdigest()

@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    item_id: str
    text: str
    content_hash: str

@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    input_tokens: int
    total_tokens: int
    response_cost_usd: Decimal | None

@dataclass(frozen=True, slots=True)
class VectorRecord:
    input_hash: str
    vector: tuple[float, ...]

@dataclass(frozen=True, slots=True)
class VectorSnapshot:
    model_id: Literal["research-embedding-v1"]
    dimension: Literal[1536]
    chunks: Mapping[str, VectorRecord]
    queries: Mapping[str, VectorRecord]

@dataclass(frozen=True, slots=True)
class EmbeddingResearchReport:
    model_id: str
    dimension: int
    chunk_count: int
    query_count: int
    cache_hits: int
    cache_misses: int
    input_tokens: int
    response_cost_usd: Decimal
    provider_request_ids: tuple[str, ...]
    started_at: str
    finished_at: str

class EmbeddingResearchRejected(Exception):
    """The requested paid embedding run violates the bounded research profile."""

class EmbeddingCache(Protocol):
    def get(self, key: str) -> tuple[float, ...] | None: ...
    def put(self, key: str, vector: tuple[float, ...]) -> None: ...

async def generate_snapshot(
    model: ModelPort,
    chunks: tuple[EmbeddingInput, ...],
    queries: tuple[EmbeddingInput, ...],
    cache: EmbeddingCache,
) -> tuple[VectorSnapshot, EmbeddingResearchReport]:
    """Generate a bounded snapshot after validating every input and cache miss."""
```

**Steps:**

- [ ] **Step 1:** 写 tests：在第一次 model call 前统计全部 cache misses；默认/绝对 cap、8000 字符、模型 ID、1536 维和内容 hash 任一不符即失败；cache key 精确绑定三项。

  ```python
  def embedding_input(prefix: str, index: int) -> EmbeddingInput:
      text = f"{prefix} sanitized text {index}"
      digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
      return EmbeddingInput(item_id=f"{prefix}-{index}", text=text, content_hash=digest)

  assert embedding_cache_key("research-embedding-v1", 1536, "sha256:" + "a" * 64) == (
      "h_" + hashlib.sha256(
          ("research-embedding-v1\x001536\x00sha256:" + "a" * 64).encode()
      ).hexdigest()
  )
  oversized_chunks = tuple(embedding_input("chunk", index) for index in range(501))
  oversized_queries = tuple(embedding_input("query", index) for index in range(101))
  with pytest.raises(EmbeddingResearchRejected, match="500 chunks and 100 queries"):
      await generate_snapshot(model, oversized_chunks, oversized_queries, cache)
  assert model.calls == []
  ```
- [ ] **Step 2:** 写 fake-model tests：相同输入命中 cache，模型/维度/hash 变化 miss；报告 schema 使用显式 allowlist。

  ```python
  class FakeEmbeddingModel:
      embedding_model_id = "research-embedding-v1"
      embedding_dimension = 1536

      def __init__(self) -> None:
          self.calls: list[str] = []

      async def embed(self, query: str) -> Embedding:
          self.calls.append(query)
          return Embedding(
              vector=(0.001,) * 1536,
              model_id=self.embedding_model_id,
              provider_request_id=f"request-{len(self.calls)}",
              usage=EmbeddingUsage(
                  input_tokens=4,
                  total_tokens=4,
                  response_cost_usd=Decimal("0.000001"),
              ),
          )

  class MemoryEmbeddingCache:
      def __init__(self) -> None:
          self.values: dict[str, tuple[float, ...]] = {}

      def get(self, key: str) -> tuple[float, ...] | None:
          return self.values.get(key)

      def put(self, key: str, vector: tuple[float, ...]) -> None:
          self.values[key] = vector

  model = FakeEmbeddingModel()
  cache = MemoryEmbeddingCache()
  chunks = tuple(embedding_input("chunk", index) for index in range(12))
  queries = tuple(embedding_input("query", index) for index in range(8))
  first, _first_report = await generate_snapshot(model, chunks, queries, cache)
  first_call_count = len(model.calls)
  second, second_report = await generate_snapshot(model, chunks, queries, cache)
  assert second == first
  assert len(model.calls) == first_call_count
  assert set(asdict(second_report)) == {
      "model_id", "dimension", "chunk_count", "query_count", "cache_hits",
      "cache_misses", "input_tokens", "response_cost_usd", "provider_request_ids",
      "started_at", "finished_at",
  }
  ```
- [ ] **Step 3:** 在 LiteLLM strict test 增加 standard `usage.prompt_tokens/total_tokens` 与 non-streaming `x-litellm-response-cost` header 的解析测试；负数、NaN、超大值、缺 usage 均 fail closed。`Embedding` 新增可选 `usage: EmbeddingUsage | None = None`，现有调用保持兼容；真实 research profile 要求每次调用 usage 与 cost 均存在。配置同时只新增对 `http://127.0.0.1:<port>` 与 `http://localhost:<port>` 的本地例外，其他 HTTP URL 继续拒绝。百炼适配的 TDD 必须另外证明 embedding request（包括 runner 的全部调用）使用固定 alias 并显式发送 `dimensions=1536`，provider 返回默认 1024 维时拒绝；answer/其他 model call 的 request body 不得被泛化修改。运行 `uv run --project apps/backend pytest apps/backend/tests/unit/operations/test_milvus_embeddings.py apps/backend/tests/contract/test_litellm_strict.py -v`，预期 FAIL 为 embeddings module/usage 字段、loopback rule 或显式维度缺失。
- [ ] **Step 4:** 在 LiteLLM 增加以下固定 alias。2026-08-26 用户选择百炼 `text-embedding-v4`：环境中的 model 必须是百炼官方原始值 `text-embedding-v4`，OpenAI-compatible transport 由独立的 `custom_llm_provider: openai` 选择；不得用 `openai/text-embedding-v4` 伪造 provider model。provider key 与 workspace API base 只从环境读取，Compose 透传三项 provider 环境变量。API base 必须使用 HTTPS、无 userinfo/query/fragment 且 path 精确为 `/compatible-mode/v1`；missing、HTTP、错误 path 或 secret-bearing URL 在 marker/model side effect 前失败，异常/repr 不回显 endpoint/key。实际 provider model 必须返回 1536 维。

  ```yaml
  model_list:
    - model_name: default-chat
      litellm_params:
        model: os.environ/LITELLM_MODEL
        api_key: os.environ/OPENAI_API_KEY
    - model_name: research-embedding-v1
      litellm_params:
        model: os.environ/LITELLM_EMBEDDING_MODEL
        custom_llm_provider: openai
        api_key: os.environ/LITELLM_EMBEDDING_API_KEY
        api_base: os.environ/LITELLM_EMBEDDING_API_BASE
  ```

  `.env.example` 增加 `LITELLM_BASE_URL=http://127.0.0.1:4000`、非 secret 原始 model `LITELLM_EMBEDDING_MODEL=text-embedding-v4`、空的 `LITELLM_EMBEDDING_API_KEY=` 与 `LITELLM_EMBEDDING_API_BASE=`；workspace endpoint/key 只写未跟踪的 `.env` 或 secret store。固定 LiteLLM `v1.76.1-stable` 的 `get_llm_provider` 在显式 `custom_llm_provider=openai` 时选择 OpenAI embedding transport，并在上游 handler 前恢复 raw model，因此 TDD 必须拒绝 env provider prefix、固定独立 routing 字段并对账上游 body model 仍为 `text-embedding-v4`。该版本 cost map 没有 `text-embedding-v4`，且百炼官方价格不是美元，故不得猜测 `base_model`、USD 换算或自定义 `input_cost_per_token`；cost 仅接受真实响应的严格 header。
- [ ] **Step 5:** 实现 research runner，默认读取 Task 8 的 12 chunks/8 queries；cache/report 分别写入 `.local/milvus-embedding-cache/` 与 `.local/milvus-research/` 并加入 `.gitignore`。
- [ ] **Step 6:** 增加 `research-embeddings` Make target，要求显式 `TAP_RUN_PAID_EMBEDDING_RESEARCH=1`；未设置时失败而不是 skip。
- [ ] **Step 7:** 在未跟踪配置中注入百炼 workspace-specific `/compatible-mode/v1` HTTPS endpoint 与 key，使用真实 LiteLLM 运行一次 profile。先验证 gateway route 确为 `research-embedding-v1`、请求显式为 1536 维、provider 返回 `text-embedding-v4` 的 1536 维 vectors，且 standard usage 与 canonical `x-litellm-response-cost` 均存在；任一缺失、1024 默认维、route label 漂移或成本单位无法证明都停止，不伪造 metadata。验证正向 query 的预期 source 进入 top 10 后，把仅含脱敏 input hash、模型/维度和 vectors 的 snapshot 写入仓库；不得提交本地 cache、cost report、workspace endpoint 或 provider secret。
- [ ] **Step 8:** 重跑 unit tests、snapshot hash validation 和 `make check && make test`。
- [ ] **Step 9:** 只暂存本任务文件并提交。

  ```sh
  git add deploy/local/litellm/config.yaml compose.yaml .env.example .gitignore apps/backend/src/tap/modules/knowledge/ports/models.py apps/backend/src/tap/modules/knowledge/adapters/litellm.py apps/backend/src/tap/operations/milvus/embeddings.py scripts/milvus_embedding_research.py apps/backend/tests/fixtures/milvus/vectors-research-embedding-v1.json apps/backend/tests/unit/operations/test_milvus_embeddings.py apps/backend/tests/contract/test_litellm_strict.py Makefile
  git diff --cached --check
  git commit -m "test: add bounded embedding research fixture"
  ```

### Task 10: 将真实 Milvus correctness gate 加入日常 CI

**Files:**

- Create: `apps/backend/tests/integration/test_milvus_search_acl.py`
- Create: `apps/backend/tests/integration/test_milvus_rebuild_alias.py`
- Create: `apps/backend/tests/integration/milvus_runtime.py`
- Modify: `.github/workflows/contracts.yml`
- Modify: `Makefile`

**Interfaces:**

- Consumes: Task 6 adapter/bootstrap, Task 7 Compose/RBAC/health, Task 8 publisher and Task 9 committed vector snapshot.
- Produces: a non-skippable `make test-milvus` gate and CI job that exercises a real Milvus server without paid embedding calls.

```python
@dataclass(frozen=True, slots=True)
class MilvusCaseResult:
    provider_rows: tuple[Mapping[str, object], ...]
    search_hits: tuple[SearchHit, ...]
    citations: tuple[Citation, ...]

class PublishedFixture:
    async def run_case(self, case_id: str) -> MilvusCaseResult:
        """Run one versioned query case through real provider and Knowledge surfaces."""
```

**Steps:**

- [ ] **Step 1:** 写真实 integration module gate；日常 `make test` 未选择 suite 时 module-level skip，`make test-milvus` 设置开关后任何缺项调用 `pytest.fail`。

  ```python
  RUN_REAL_MILVUS = os.getenv("TAP_RUN_MILVUS_INTEGRATION") == "1"
  if not RUN_REAL_MILVUS:
      pytest.skip("real Milvus suite is run by make test-milvus", allow_module_level=True)

  REQUIRED_ENV = (
      "MILVUS_URI", "MILVUS_DATABASE", "MILVUS_READER_USERNAME",
      "MILVUS_READER_PASSWORD", "MILVUS_WRITER_USERNAME", "MILVUS_WRITER_PASSWORD",
      "MILVUS_PROVISIONER_USERNAME", "MILVUS_PROVISIONER_PASSWORD",
  )
  missing = tuple(name for name in REQUIRED_ENV if not os.getenv(name))
  if missing:
      pytest.fail("missing required real Milvus settings: " + ", ".join(missing), pytrace=False)
  ```
- [ ] **Step 2:** ACL test 使用预计算 vectors 建表/发布，验证 allowed top 10 与所有 negative 维度。

  ```python
  @pytest.mark.asyncio
  @pytest.mark.parametrize(
      "case_id",
      ("denied-group", "wrong-tenant", "wrong-project", "over-classification",
       "wrong-environment", "wrong-corpus"),
  )
  async def test_real_milvus_denied_cases_return_no_rows_hits_or_citations(
      published_fixture: PublishedFixture, case_id: str
  ) -> None:
      result = await published_fixture.run_case(case_id)
      assert result.provider_rows == ()
      assert result.search_hits == ()
      assert result.citations == ()
  ```
- [ ] **Step 3:** 覆盖 BM25、dense、hybrid、resource/subtree、撤权、`deleted=true` 与 direct physical query；用 spy/transport evidence 证明每个 channel/补充 read 都使用同一 filter。
- [ ] **Step 4:** rebuild/alias test 先验证普通容器重启后持久可见与并发 alias 切换只看到单一 physical/corpus version；再由 `test-milvus-rebuild-empty` 在 `TAP_ALLOW_MILVUS_VOLUME_RESET=1` 时只删除经 `^[a-z0-9][a-z0-9_-]{2,62}$` 验证的 `TAP_MILVUS_COMPOSE_PROJECT` 所属 Compose volumes，重新启动并从 manifest/snapshot 发布，断言 ID/hash/ACL/provenance 与删除前 digest 完全一致。未设开关或 project name 无效时必须在 `down -v` 前失败。
- [ ] **Step 5:** 先运行 `TAP_RUN_MILVUS_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_milvus_search_acl.py apps/backend/tests/integration/test_milvus_rebuild_alias.py -v`；预期 FAIL 明确列出缺少的 real Milvus settings，或在资源齐全时显示尚未满足的真实行为断言。
- [ ] **Step 6:** 增加 `test-milvus` target，精确顺序为 up -> bootstrap -> health -> fixture publish -> integration tests；失败时保留诊断日志，本地不自动删除 volumes。

  ```make
  TAP_MILVUS_COMPOSE_PROJECT ?= tap-milvus-local-experiment

  test-milvus: milvus-up milvus-bootstrap milvus-health
	uv run --project apps/backend python scripts/milvus_fixture.py publish --manifest apps/backend/tests/fixtures/milvus/doc-fixture-v1.json --vectors apps/backend/tests/fixtures/milvus/vectors-research-embedding-v1.json
	TAP_RUN_MILVUS_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_milvus_search_acl.py apps/backend/tests/integration/test_milvus_rebuild_alias.py -v

  test-milvus-rebuild-empty:
	@test "$${TAP_ALLOW_MILVUS_VOLUME_RESET:-0}" = 1
	@printf '%s' "$(TAP_MILVUS_COMPOSE_PROJECT)" | rg -q '^[a-z0-9][a-z0-9_-]{2,62}$$'
	docker compose -p "$(TAP_MILVUS_COMPOSE_PROJECT)" --profile milvus down -v --remove-orphans
	$(MAKE) test-milvus TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
  ```
- [ ] **Step 7:** 给 workflow 增加独立 `milvus-integration` job，先使用现有 `ubuntu-latest` 免费/标准 runner，并设置唯一 `TAP_MILVUS_COMPOSE_PROJECT=tap-milvus-ci-${{ github.run_id }}-${{ github.run_attempt }}`。preflight 读取 Docker/cgroup 可用资源，少于 2 vCPU 或 8 GiB 直接 fail（不 skip、不自动购买 larger runner）；资源满足时使用固定 images/预计算 vectors并显式设置 `TAP_RUN_MILVUS_INTEGRATION=1`。先运行 `make test-milvus`，再以 `TAP_ALLOW_MILVUS_VOLUME_RESET=1 make test-milvus-rebuild-empty` 验证删卷重建；job 中任何 skip 视为失败，cleanup 只删除该 project 的 volumes。若标准 runner 资源不足，将证据写入 review 并由用户另行批准 self-hosted/付费 runner，不能弱化门禁。
- [ ] **Step 8:** 保留真实 Azure test 的 opt-in 条件；不得用 Milvus GREEN 修改 Azure-backed 发布门禁或 Task 3 外部验收状态。
- [ ] **Step 9:** 本地运行 `make test-milvus`，再运行 `make check && make test`。
- [ ] **Step 10:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/tests/integration/test_milvus_search_acl.py apps/backend/tests/integration/test_milvus_rebuild_alias.py apps/backend/tests/integration/milvus_runtime.py .github/workflows/contracts.yml Makefile
  git diff --cached --check
  git commit -m "ci: require real milvus search gates"
  ```

### Task 11: 消除公共 physical target 漂移并形成实验决策报告

**Files:**

- Modify: `apps/backend/tests/contract/test_generated_contracts.py`
- Modify: `docs/reference/2026-08-20-contracts.md`
- Create: `docs/reviews/2026-08-24-milvus-local-search-experiment.md`
- Modify: `docs/reviews/index.md`
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `docs/plans/2026-08-24-milvus-local-search-experiment.md`

**Interfaces:**

- Consumes: generated Pydantic/OpenAPI schema and the raw evidence produced by Tasks 9–10.
- Produces: public contract/reference agreement, reproducible experiment review, and exactly one lifecycle recommendation without altering accepted ADRs.

**Steps:**

- [ ] **Step 1:** 先加强 generated contract test，断言 public `RetrievalHit` schema 不含 provider physical target。

  ```python
  def test_public_retrieval_hit_omits_physical_target() -> None:
      schema = RetrievalHit.model_json_schema(by_alias=True)
      properties = schema["properties"]
      assert "physicalIndex" not in properties
      assert "physicalCollection" not in properties
  ```
- [ ] **Step 2:** 从 reference contract 的公共 hit 删除 `physicalIndex`，把 physical target 说明移到授权运维 Retrieval Trace；不得暴露 alias、collection 或 provider 内情给普通客户端。
- [ ] **Step 3:** 用已保存的真实输出写 review，表头固定为 `Gate | Result | Evidence`。六行依次为 ACL negative matrix、8-case hybrid top 10、restart persistence、rebuild parity、alias single-version binding、embedding budget；`Result` 只能写当次证据支持的 `pass` 或 `fail`，`Evidence` 必须写 artifact 文件名与 SHA-256。没有测量值就保持计划 `active`，不得填估算值。
- [ ] **Step 4:** review 必须明确给出且只给出 `continue`、`fix-and-repeat`、`stop` 之一，并列出证据；不得自动接受 RFC、修改 ADR-002/005/012 或批准共享非生产。
- [ ] **Step 5:** README 只描述可复现实验命令和“实验性”状态，不把 Milvus 写成已投产。同步 docs/reviews 与 docs 根索引。
- [ ] **Step 6:** 将本计划 `status` 改为 `completed` 的条件固定为：Tasks 1–10 全部提交，`make check`、`make test`、`make test-milvus`、真实 `research-embeddings` 均有当次证据，且 review 已记录结果。否则保持 `active` 并写明未通过项。
- [ ] **Step 7:** 运行：

   ```sh
   rg --files README.md docs
   git diff --check
   make check
   make test
   make test-milvus
   rg -n 'TO[D]O|TB[D]|placeholde[r]' README.md docs apps/backend/tests/contract
   rg -n 'physicalInde[x]|physicalCollectio[n]' docs/reference/2026-08-20-contracts.md
   ```

- [ ] **Step 8:** 用兼容 renderer 预览所有变更 Markdown/Mermaid，检查相对链接、frontmatter、术语与 phase 状态。
- [ ] **Step 9:** 请求独立代码审查，修复审查发现后重跑全部门禁。
- [ ] **Step 10:** 只暂存本任务文件并提交。

  ```sh
  git add apps/backend/tests/contract/test_generated_contracts.py docs/reference/2026-08-20-contracts.md docs/reviews/2026-08-24-milvus-local-search-experiment.md docs/reviews/index.md README.md docs/index.md docs/plans/2026-08-24-milvus-local-search-experiment.md
  git diff --cached --check
  git commit -m "docs: report milvus local search experiment"
  ```

## Execution Checkpoints

- **Checkpoint A — shared contract:** Tasks 1–3 后审查公共错误、Policy bounds 与版本兼容；不启动数据库。
- **Checkpoint B — adapter conformance:** Tasks 4–6 后审查 filter、alias race、严格 mapping 与 provider selection；全部使用 fake transport。
- **Checkpoint C — operational safety:** Tasks 7–9 后审查 loopback、RBAC、fixture 可重建性、付费上限与 snapshot provenance。
- **Checkpoint D — real evidence:** Tasks 10–11 后审查真实 Milvus、现有全量回归与实验决策；只有此处允许把计划标记 completed。

## Follow-up Boundary

实验结论若为 `continue`，先按治理流程接受 RFC-004，再创建两份完整重述保留语义的 superseding ADR，分别替代 ADR-005 与 ADR-012；同步旧/new ADR metadata、decisions index、README、RAG Foundation、Azure-specific index design、reference contracts、RFC-003 基础设施表、Phase 1 roadmap 与 Task 3/4 gate。该决策同步是独立计划，不夹进实验结果提交。

随后另写实施计划处理共享非生产 Milvus、TLS/secret/monitoring/backup/SLO，以及剩余 `code`、`bdd`、`failure` families 和完整 ingestion。让 Milvus 成为共享非生产或企业默认还需要独立 RFC；Azure 企业发布仍按 ADR-002 与真实 Azure gate 单独验收。Entra/Project-Policy 的真实撤权门禁与 Task 7 citation/history/trace 重新授权也不由本计划关闭。
