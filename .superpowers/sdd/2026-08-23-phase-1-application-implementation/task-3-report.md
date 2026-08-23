# Task 3 Report: Trusted Policy Context and Knowledge Public API

## Status

`DONE_WITH_CONCERNS`. The local Task 3 slice is implemented and locally verified. The real Azure AI Search ACL gate was not run because this machine has neither an Azure CLI login nor the twelve sanitized fixture settings required by the opt-in test. Ordinary runs explicitly skip that one external test; setting `TAP_RUN_AZURE_INTEGRATION=1` fails closed when the configuration is absent.

## Produced Interfaces

```python
def build_retrieval_policy_context(
    subject: VerifiedSubjectFacts,
    policy: ProjectPolicy | None,
    *,
    requested_tenant_id: str,
    requested_project_id: str,
) -> RetrievalPolicyContext: ...

class KnowledgeAPI:
    async def search(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> SearchResponse: ...

    async def answer(
        self,
        request: AnswerRequest,
        policy: RetrievalPolicyContext,
    ) -> AnswerResponse: ...
```

`RetrievalPolicyContext` has a guarded constructor and is produced from verified subject facts intersected with current server-side Project Policy. `KnowledgeAPI` is the exported Knowledge application boundary. HTTP mapping functions explicitly translate the existing Pydantic DTO graph to and from framework-free application models; the Knowledge domain, application, and ports do not import FastAPI, Pydantic, HTTP clients, Azure SDKs, or LiteLLM/provider types.

Every internal and public Retrieval/Answer response carries opaque `traceId`, `queryPlanId`, `contextSnapshotId`, `corpusVersion`, and `retrievalProfileId` values. Policy/plan/snapshot contents remain internal.

## Behavior and Security Boundaries

- Policy construction rejects unverified subjects, unavailable policy, revoked permission, every tenant/project mismatch, empty group intersection, and unsafe `search.in` group delimiters. It expands the classification ceiling through an explicit enum set and carries only server-authorized environments, families, corpus, ACL digest/version/decision, and immutable resource grants.
- Closed framework-free values cover four source families, three resource modes, quick/deep profiles, immutable source revisions, five structural-anchor variants, evidence, citations, claims, content roles, and structured abstention reasons.
- Search/answer requests validate a non-empty bounded query, at most four families, at most twenty resources, and an exact non-boolean integer `top_k` in `1..100`. Requested environment, corpus, family, source, revision, and anchor can only narrow the policy. Unauthorized requests fail before embedding or Search ports are called.
- Quick/deep select fixed versioned profiles and bound candidate/final results. Cross-index merging uses deterministic per-index rank/RRF and never compares Azure raw scores. The returned score is the TAP RRF score; physical index, source revision/hash, schema/corpus, embedding model version, ACL decision ID, and allowlisted provider request ID remain available as internal provenance.
- Successful generated claims must map every evidence label to a current citation. Missing required resources, revision mismatch, missing evidence, or unsupported claim labels produce structured abstention.
- Public schemas reject unknown fields and contain no `tenantId`, `projectId`, `allowedGroupIds`, `classification`, `filter`, `rawFilter`, or physical-index property. Search hits expose `indexFamily`, safe score components, schema/model versions, and opaque ACL-decision provenance, but not the physical index identity.

## Azure AI Search Adapter

- Uses the stable REST API `2026-04-01` and POST search fields `filter`, `vectorFilterMode=preFilter`, `vectorQueries`, `select`, and bounded `top`.
- Derives mandatory tenant, project, intersected group, explicit classification, `global OR requested environment`, and active-corpus clauses on the server. OData literals escape apostrophes; group/classification/environment `search.in` values use an explicit validated `|` delimiter. Browser raw filters, fields, and index names never enter the adapter.
- Adds source/revision scope filters before retrieval and exact revision/hash/anchor trimming after retrieval. Direct port calls are revalidated against the trusted policy and fail closed instead of widening.
- Strictly bounds fan-out, per-index candidates/vector `k`, global concurrent queries, connections, query/vector sizes, connect/read/outer deadlines, retries, and final application results. Boolean/fractional integers and non-finite durations/vectors are rejected.
- Uses one retry owner (`httpx` transport retries are zero), a stable TAP `x-ms-client-request-id` across the adapter retry, and captures only `request-id` with documented compatibility fallbacks.
- Stamps the server-selected physical index plus source/corpus/schema/model provenance into each internal hit. A selected-index error, malformed/partial page, or retry/deadline exhaustion rejects the chosen fan-out; interactive search does not follow pagination.
- `api_key` is omitted from configuration representation and never appears in errors.

## LiteLLM Adapter

- Uses server-fixed embedding, answer-model, and answer-profile identifiers; callers cannot select a provider/model route.
- Enforces finite connect/read/outer deadlines, bounded connections, one bounded adapter retry, and zero HTTP transport retries.
- Captures only allowlisted LiteLLM call/model IDs, provider request IDs, and body completion/model IDs. HTTP responses, headers, and provider types do not cross the port.
- The gateway credential is omitted from configuration representation and errors.

## Files and Dependencies

Created:

- `apps/backend/src/tap/modules/access/domain/policy.py`
- `apps/backend/src/tap/modules/access/application/authorize.py`
- `apps/backend/src/tap/modules/knowledge/api.py`
- `apps/backend/src/tap/modules/knowledge/domain/models.py`
- `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- `apps/backend/src/tap/modules/knowledge/ports/search.py`
- `apps/backend/src/tap/modules/knowledge/ports/models.py`
- `apps/backend/src/tap/modules/knowledge/adapters/azure_ai_search.py`
- `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- `apps/backend/tests/unit/access/test_policy_context.py`
- `apps/backend/tests/contract/test_knowledge_api.py`
- `apps/backend/tests/integration/test_search_acl.py`
- `apps/backend/tests/architecture/test_module_boundaries.py`

Modified as approved:

- `apps/backend/src/tap/contracts/http.py`
- `scripts/export_contracts.py`
- `contracts/openapi/api.json`
- `apps/backend/pyproject.toml`
- `uv.lock`

`httpx==0.28.1` moved from dev-only to a direct runtime dependency because both production adapters import it. `pytest-asyncio==1.1.0` was added as an exact direct dev dependency for the async contract/integration tests, and the root lock was regenerated.

The controller also approved the adjacent type-boundary-only update in `apps/backend/src/tap/entrypoints/relay_reconciler.py`: `Redis.from_url(...)` is explicitly cast to the declared `Redis` return type. It changes no runtime behavior, resolves the repository-wide strict mypy gate, and the complete existing relay test suite passed.

## TDD RED Evidence

The named authorization, contract, architecture, and gated-integration tests were created before the Task 3 production modules.

Initial command:

```sh
uv run --project apps/backend pytest \
  apps/backend/tests/unit/access \
  apps/backend/tests/contract/test_knowledge_api.py \
  apps/backend/tests/architecture/test_module_boundaries.py -v
```

Initial result: exit `2`, collection stopped with two expected missing-feature errors:

```text
ModuleNotFoundError: No module named 'tap.modules.access'
ImportError: cannot import name 'RetrievalAnswerRequest' from 'tap.contracts.http'
============================== 2 errors in ... ===============================
```

After the first GREEN, review-hardening tests were written before their corresponding changes. This command produced fourteen intended failures:

```sh
uv run --project apps/backend pytest apps/backend/tests/contract/test_knowledge_api.py \
  -k 'cross_index_fusion or internal_search_request or azure_config or preserves_only_allowlisted or escaped_mandatory' -v
```

The failures proved that non-integer/out-of-range `top_k`, absent per-index rank, absent client request ID, boolean/fractional/non-finite Azure bounds, credential representation leakage, and missing provider request-ID provenance were not yet handled. Separate RED runs also proved the LiteLLM credential representation leaked, direct boolean candidate limits reached the adapter, and server-selected physical-index identities could not yet be configured.

The scope-anchor regression initially failed with `DID NOT RAISE SearchUnavailable`, proving a direct family bypass/sibling-anchor result could widen the requested scope before the fix.

The final fusion/provenance RED command:

```sh
uv run --project apps/backend pytest apps/backend/tests/contract/test_knowledge_api.py \
  -k 'resolves_revision_caps or cross_index_fusion' -v
```

Result: two failures. Evidence lacked `embedding_model_version`, and the returned score was raw `0.01` instead of the hand-derived RRF `1/61`. After that fix, the explicit HTTP contract RED failed with `KeyError: 'indexFamily'`; the mapping then gained contract-shaped `indexFamily`/score/provenance fields while continuing to omit `physicalIndex`.

## GREEN and Verification Evidence

Focused Task 3 suite:

```text
33 passed in 0.15s
```

This includes seven access-policy cases, twenty-four Knowledge/provider contract cases, and two architecture-boundary cases.

Repository verification:

```text
make check
  ruff check: All checks passed!
  ruff format --check: 32 files already formatted
  mypy: Success: no issues found in 23 source files
  deterministic contract check: exit 0

make test
  67 passed, 1 skipped in 3.93s

make bootstrap
  uv sync --frozen --all-groups: Audited 32 packages
  pnpm install --frozen-lockfile: Already up to date

git diff --check
  exit 0, no output
```

`make contracts`, the exporter `--check`, a second `make contracts`, and `git diff --exit-code -- contracts/` all exited `0`. A separate schema inspection found all four Retrieval request/response components, required both opaque response IDs, confirmed `indexFamily`/structured scores, and found zero forbidden public property names.

The full suite includes every Task 2 relay entrypoint/recovery test, so the adjacent return-type cast retains its runtime behavior.

## Azure Integration Limitation

Ordinary local command:

```sh
uv run --project apps/backend pytest apps/backend/tests/integration/test_search_acl.py -v
```

Result: `1 skipped in 0.04s`, with an explicit message requiring `TAP_RUN_AZURE_INTEGRATION=1` and a sanitized Azure fixture.

Opt-in command:

```sh
TAP_RUN_AZURE_INTEGRATION=1 uv run --project apps/backend \
  pytest apps/backend/tests/integration/test_search_acl.py -v
```

Result: exit `1`, as designed. It failed rather than skipped and listed all missing sanitized settings:

```text
TAP_AZURE_SEARCH_ENDPOINT, TAP_AZURE_SEARCH_API_KEY, TAP_AZURE_SEARCH_INDEX,
TAP_AZURE_TEST_TENANT_ID, TAP_AZURE_TEST_PROJECT_ID,
TAP_AZURE_TEST_ALLOWED_GROUP_ID, TAP_AZURE_TEST_DENIED_GROUP_ID,
TAP_AZURE_TEST_CLASSIFICATION_CEILING, TAP_AZURE_TEST_ENVIRONMENT,
TAP_AZURE_TEST_CORPUS_VERSION, TAP_AZURE_TEST_EXPECTED_SOURCE_ID,
TAP_AZURE_TEST_DATASET_MARKER
```

The real positive-control/unauthorized-hit-zero Azure contract is therefore **NOT RUN** and is not reported as GREEN. When configured, the test additionally requires distinct allowed/denied groups, the exact marker `non-production-sanitized`, visibility of the expected authorized source, and exactly zero denied hits. It embeds and prints no credentials or tenant data.

## Self-Review

- Re-read the Task 3 brief and Retrieval/Knowledge Chat contracts, then checked each requested authorization, adapter, framework-boundary, public-schema, and external-gate behavior against implementation and tests.
- Confirmed cross-index ordering and returned RRF scores use family-local rank, final results remain capped by both server profile and smaller caller preference, and raw Azure scores are not used for cross-index ordering.
- Confirmed every Azure query is formed only after a trusted policy/execution validation and includes all mandatory ACL clauses with safe literal escaping.
- Confirmed no partial selected-index result can be returned and no interactive pagination is followed.
- Confirmed only allowlisted provider/gateway IDs cross ports and both adapter credentials are excluded from dataclass representations.
- Confirmed generated schemas contain no policy/filter/physical-index fields and include both required opaque IDs without exposing plan or snapshot contents.
- Confirmed no Task 4+ worker, route, Chat flow, persistence, trace store, or ingestion behavior was implemented.
- Confirmed the controller-owned ignored `progress.md` was not modified or staged.

## Concerns

1. Real Azure authentication and sanitized fixture configuration are absent on this machine. The production REST path and the unauthorized-hit-zero fixture contract remain externally unverified until that opt-in gate is run against a controlled Azure Search index.
2. Task 3 establishes the Knowledge application/API boundary and adapters but intentionally does not wire a FastAPI route, Chat worker, or runtime configuration assembly; those belong to later tasks.

## Fix Round 1

### Status and Review Disposition

Fix Round 1 resolves all accepted Critical and Important findings in the binding review disposition. Authorization is now checked against the authoritative current decision before any egress side effect, and provider execution consumes an immutable, policy-bound `QueryPlan`/`ContextSnapshot` pair rather than copied request fields. Local and repository-wide gates are green. The two real external gates remain **NOT RUN** because this machine has neither Azure credentials nor the required sanitized fixture settings.

### Review Item 1: Current Policy and Immutable Execution Binding

- Added the async, framework-free `CurrentPolicyVerificationPort` and made it plus the mandatory `EgressRedactionPort` required constructor dependencies of `KnowledgeAPI`.
- `AuthorizedRetrieval` compares every current `RetrievalPolicyContext` fact, including actor groups/roles and all resource grants, before redaction, refreshes again after redaction before embedding, after embedding before Search, and immediately before answer generation. Unavailable, revoked, or changed decisions fail before the next side effect.
- Added frozen `QueryPlan`, `ContextSnapshot`, and bounded `ContextLayer` values. They share an operation ID and bind tenant/project, decision/version/digest, effective family/environment/corpus/resource scope, profile/candidate cap, canonical raw-request hash, redaction version, sanitized-query hash, embedding vector space, and current-turn lineage.
- `SearchExecution` now contains only the current policy, immutable plan/snapshot, and query vector. Both application and Azure adapter independently validate their equality/binding.
- Runtime policy booleans use exact `bool`; required policy facts reject truthy non-strings. The private policy construction hook is treated as a lint boundary, and recursive architecture tests permit it only in Access authorization.
- Behavior coverage: `test_authorized_execution.py`, `test_policy_context.py`, and `test_module_boundaries.py` cover unavailable/stale/revoked policy before side effects, every changed policy fact, post-redaction and post-embedding revocation, redaction failure, plan/snapshot lineage, strict facts, and private-constructor imports.

### Review Item 2: Resource-Mode Semantics

- Scope resources form one global union. Once any scope exists, selected families without a matching scope are removed; direct Search executions with uncovered families fail closed.
- Every browser resource resolves against the current immutable policy grant. Azure rejects forged `ResolvedResourceRef` values and mismatched subtree grants.
- Scope OData is generated before retrieval and binds `sourceId`, revision, source hash, and server-resolved `rootId`/`parentId`/`logicalChunkId` locators. An anchored scope without a trusted filterable subtree is rejected before egress. Application and adapter also enforce the same scope after retrieval.
- Required coverage compares family, revision kind/value, source hash, anchor, and subtree. Preferred resources receive the fixed profile-owned boost after family-local RRF. A reachable same-logical-chunk/different-hash conflict abstains with `conflicting_sources`.
- Behavior coverage: `test_resource_modes.py` independently mutates family/revision/hash/anchor/subtree, verifies the hand-derived preferred ordering, global cross-family closure, forged/out-of-scope hits, and conflict abstention.

### Review Item 3: Strict Search Provenance and Vector Binding

- Added explicit immutable `AzureIndexTarget` values containing query identity, actual physical identity, schema version, expected embedding model, and vector dimension; configuration snapshots the mapping so later caller mutation cannot relabel provenance.
- Azure rejects the entire selected page for malformed/null/empty chunk or logical IDs, source/chunk hashes, revisions, anchors, content, family, corpus/schema/model provenance, scores, pagination, row/content/derived-ID bounds, or mismatched execution. It never coerces provenance with `str(...)`.
- Query plan, emitted embedding, configured index, query vector, and every returned row must agree on model ID and dimension before evidence construction. The application also rejects a Search-port hit from another vector space.
- Search evidence retains server-selected physical index/revision and allowlisted Azure request ID internally. `SearchResponse`/`AnswerResponse` retain configured/provider/gateway/model/completion provenance internally while HTTP mapping deliberately omits it.
- Behavior coverage: `test_azure_search_strict.py`, `test_authorized_execution.py`, and the existing fusion/mapping tests cover plan/snapshot mutation, forged scope, vector mismatch, malformed provenance table cases, explicit physical identity, immutable target mapping, and private-versus-public provenance.

### Review Item 4: Redaction and Hard Bounds

- Raw browser text is sent only to the mandatory redactor. Only the bounded sanitized text reaches LiteLLM embedding/answer calls; its version and digest are bound into the plan. Redactor unavailability fails closed.
- Azure and LiteLLM now stream response bodies into hard byte limits and bound requests, rows, connections, fan-out, candidates, vectors, IDs, content, anchors, evidence, claims, labels, tokens, and output. Integer configuration uses exact non-boolean `int`; durations/vectors reject non-finite values.
- Outer deadlines begin before filter/evidence construction and serialization and are checked through body read, JSON parsing, normalization, and sibling-fanout completion. Selected-index failure cancels sibling work. Each adapter owns one bounded retry budget and reuses one request identity across its retry.
- Azure bearer-token auth is the default production mode. Query-key auth requires an explicit compatibility/test opt-in. Both adapter secrets are excluded from representations and errors.
- Behavior coverage: the strict Azure/LiteLLM suites cover request/response bytes, row/content/evidence/output bounds, serialization-start and read/parse deadlines, fanout cancellation, stable retry identity, credential representation, and explicit auth mode.

### Review Item 5: Strict Public DTO and LiteLLM Behavior

- Public `topK` and all anchor integers now use strict non-boolean integers. Every browser-controlled string, list, resource, anchor, query, and Chat-turn field has a hard bound; ordered line/offset ranges are validated.
- Literal field tables and recursive schema walks verify closed Pydantic objects, every public request/response field, all five anchor variants, and wrong JSON types. Generated public schemas still omit tenant/project/ACL/filter/physical-index/provider fields and preserve opaque `queryPlanId` and `contextSnapshotId`.
- LiteLLM uses only configured server model/profile identifiers, validates retrieval profiles, embedding dimension/finiteness, allowlisted returned model labels/IDs, and a closed non-empty grounded-answer structure with bounded claims and evidence labels.
- Behavior coverage: `test_public_retrieval_contract_strict.py`, `test_litellm_strict.py`, and internal request-bound cases in `test_knowledge_api.py`.

### Review Item 6: Architecture and Mutation-Sensitive Tests

- Architecture scanning is recursive across every Knowledge/Access domain/application/port Python file and canonicalizes relative imports.
- Knowledge adapters may not import Chat. Chat imports only allowlisted symbols from `tap.modules.knowledge.api`, under private aliases, and cannot import the API module object or internals indirectly.
- Independent mutation tables cover policy facts, execution binding, resource modes, strict Azure rows, provider payloads, public DTO fields/types/bounds, internal model runtime bypasses, deadlines, and byte/count limits. Expected filters, payloads, and RRF order are hand-derived in tests rather than rebuilt with production helpers.

### Fix-Round Files and Dependencies

Added:

- `apps/backend/src/tap/modules/access/application/ports.py`
- `apps/backend/src/tap/modules/knowledge/ports/redaction.py`
- `apps/backend/tests/contract/test_authorized_execution.py`
- `apps/backend/tests/contract/test_resource_modes.py`
- `apps/backend/tests/contract/test_azure_search_strict.py`
- `apps/backend/tests/contract/test_litellm_strict.py`
- `apps/backend/tests/contract/test_public_retrieval_contract_strict.py`
- `apps/backend/tests/integration/test_current_policy_gate.py`

Modified:

- Access policy values; Knowledge API/domain/application/ports; Azure and LiteLLM adapters
- Public HTTP DTOs and generated `contracts/openapi/api.json`
- Existing Knowledge, policy, architecture, and real Azure ACL tests

No dependency or lockfile change was needed in this round: the exact direct `httpx==0.28.1` runtime dependency and async-test dependency were already present in base Task 3. The controller-approved Redis return-type cast is also already in base `98ac4cd`; all Task 2 integration regressions remain green.

### Fix-Round TDD RED Evidence

Focused tests were added before their production behavior. The material RED results were:

- Authorized execution first stopped at collection with `ImportError: cannot import name 'RedactionResult'`; after the port values existed, strict policy tests had four `DID NOT RAISE` failures for `"false"`, `"true"`, `0`, and `1`. Stale/revoked current-policy cases then proved redaction/model/Search side effects still occurred until current verification and immutable binding were added.
- Resource semantics began with six behavior failures: required family/revision/hash/anchor mismatches returned `abstained=False`, preferred ordering was unchanged, and conflicting sources returned a normal answer. Subtree scope first failed collection with missing `ResourceSubtreeGrant`, then failed with `abstained is False`; an application out-of-scope hit later failed with `DID NOT RAISE AuthorizationDenied`.
- Azure strict tests first failed collection with missing `AzureIndexTarget`. Subsequent focused RED cases reported `DID NOT RAISE` for context-lineage mutation, outer filter deadline, malformed pagination markers, widened anchors, excess per-index rows, mutable index relabeling, and empty content; the failed-fanout test showed `blocking.cancelled is False`.
- LiteLLM's first strict run collected 18 cases with `13 failed, 5 passed`; missing embedding dimension, finite/bounded route values, byte/deadline limits, and closed output behavior accounted for the failures. Malformed evidence then produced one `TypeError` and two `DID NOT RAISE` failures; serialization-start deadline also initially `DID NOT RAISE`.
- Public DTO mutation tests initially reported `30 failed, 31 passed`: booleans/floats reached integer fields and browser-controlled fields/anchors were unbounded. Ordered document/code ranges added two further `DID NOT RAISE` failures.
- Architecture helper tests initially failed with missing `parsed_imports` and `recursive_python_files`. Internal framework-free model mutation tables then had `21 failed`, proving direct runtime construction could bypass type/size bounds. A wrong Search-hit embedding model also initially `DID NOT RAISE AuthorizationDenied`.
- Final self-review added a race-window test for revocation after redaction. Its exact RED showed `model.embedding_queries == ['card [REDACTED]']` instead of `[]`, proving the second authoritative refresh occurred only after model egress. Moving that refresh before embedding made the targeted test pass while retaining a separate post-embedding/pre-Search revocation check.

The one test-fixture-only interruption was an HTTPX construction error for an in-memory NaN JSON response (`ValueError: Out of range float values are not JSON compliant`). Replacing that fixture with literal malformed response bytes let the adapter, rather than HTTPX's fixture serializer, own and pass the intended non-finite JSON rejection test.

### Fix-Round GREEN and Verification Evidence

Final focused Task 3 command, including both ordinary external gates:

```text
214 passed, 2 skipped in 0.36s
```

The two skips are only the explicitly gated real Azure and current-policy probes. Focused provider/application/public-contract tests alone reported `192 passed in 0.28s`.

All Task 2 integration regressions (`test_turn_outbox.py`, `test_relay_recovery.py`, and `test_relay_entrypoint.py`):

```text
28 passed in 0.98s
```

Repository verification on the final production/test tree:

```text
make check
  ruff check: All checks passed!
  ruff format --check: 40 files already formatted
  mypy: Success: no issues found in 25 source files
  deterministic contract check: exit 0

make test
  248 passed, 2 skipped in 3.98s

make bootstrap
  uv sync --frozen --all-groups: Audited 32 packages
  pnpm install --frozen-lockfile: Already up to date
```

`make contracts` produced byte-identical files across consecutive runs (`api.json` checksum/size `1443310812/34532`; chat event schema `2876493190/27072`), and `uv run --project apps/backend python scripts/export_contracts.py --check` exited `0`. `git diff --check` exited `0`. A generated-schema scan found no `tenantId`, `projectId`, `allowedGroupIds`, `classification`, raw filter, filter, or physical-index property, and found both opaque response IDs in both Retrieval responses. A diff scan found no embedded private key, account key, or shared-access-signature material.

### External Gates: Exact Unrun Status

Ordinary local gate mode:

```text
2 skipped in 0.05s
```

With `TAP_RUN_AZURE_INTEGRATION=1`, the Azure gate failed closed before any network call because all 17 sanitized settings were absent:

```text
TAP_AZURE_SEARCH_ENDPOINT, TAP_AZURE_SEARCH_API_KEY,
TAP_AZURE_SEARCH_INDEX, TAP_AZURE_SEARCH_PHYSICAL_INDEX,
TAP_AZURE_SEARCH_SCHEMA_VERSION, TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID,
TAP_AZURE_SEARCH_VECTOR_DIMENSION, TAP_AZURE_TEST_TENANT_ID,
TAP_AZURE_TEST_PROJECT_ID, TAP_AZURE_TEST_ALLOWED_GROUP_ID,
TAP_AZURE_TEST_DENIED_GROUP_ID, TAP_AZURE_TEST_CLASSIFICATION_CEILING,
TAP_AZURE_TEST_ENVIRONMENT, TAP_AZURE_TEST_CORPUS_VERSION,
TAP_AZURE_TEST_EXPECTED_SOURCE_ID, TAP_AZURE_TEST_QUERY_VECTOR_JSON,
TAP_AZURE_TEST_DATASET_MARKER
```

With `TAP_RUN_ENTRA_POLICY_INTEGRATION=1`, the current Entra/Project-Policy gate failed closed before any network call because all eight sanitized settings were absent:

```text
TAP_POLICY_TEST_ACTIVE_URL, TAP_POLICY_TEST_REVOKED_URL,
TAP_POLICY_TEST_BEARER_TOKEN, TAP_POLICY_TEST_TENANT_ID,
TAP_POLICY_TEST_PROJECT_ID, TAP_POLICY_TEST_USER_ID,
TAP_POLICY_TEST_ACTIVE_DECISION_ID, TAP_POLICY_TEST_DATASET_MARKER
```

Therefore the real Azure authorized-positive/unauthorized-zero contract and the real active-then-revoked Entra/Project-Policy contract are **NOT RUN** and are not claimed as GREEN.

### Fix-Round Self-Review and Concerns

- Re-read the binding fix disposition and reviewed every changed stable layer, adapter, public mapping, generated schema, and test against its six accepted findings. No accepted Critical or Important item remains open in the local slice.
- Confirmed authorization refresh precedes each provider phase; raw query text never reaches model egress; immutable plan/snapshot equality is rechecked at both application and adapter boundaries; global scope cannot widen across families; and Azure selected-page failures cannot return partial evidence.
- Confirmed one retry owner/stable identity per adapter operation, explicit physical/vector provenance, family-local RRF, bounded final results, strict public/internal runtime inputs, allowlisted provider metadata, credential-safe representations, and recursive dependency enforcement.
- Per the task's no-subagent constraint, the requested code-review discipline was applied as a solo diff-and-test self-review rather than dispatching a reviewer agent.
- No Task 4+ ingestion/index writer or Task 5 route/worker/runtime assembly was added. The controller-owned ignored `progress.md` and `task-3-fix-round-1.md` were not modified or staged.

Remaining concerns are operational only: real Azure and Entra/Project-Policy behavior still requires the two sanitized opt-in environments above, and later runtime assembly must supply current-policy verification, redaction, bearer-token, fixed-model, and explicit physical-index configuration. Those wiring tasks are deliberately outside Task 3.
