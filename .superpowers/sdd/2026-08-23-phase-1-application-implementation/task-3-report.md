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

## Fix Round 2

### Status and Accepted Review Items

Fix Round 2 closes all five accepted blockers in the controller disposition. Search and answer provider results are now consumed only after a fresh authoritative policy verification; immutable provenance is canonical and bound to an explicit Azure index route; architecture guards reject parent-module-object bypasses; every public score is strict and finite; and LiteLLM response labels are bound to disjoint embedding/answer routes. No Task 4+ runtime wiring was added.

1. **Post-provider revocation:** `AuthorizedRetrieval` refreshes current policy immediately after `SearchPort.search`, validates the existing immutable `QueryPlan`/`ContextSnapshot`, and only then inspects or materializes hits. The refreshed context is persisted in `_RetrievalRun`, so empty-evidence, missing-required, conflict, and normal answer paths all derive from the post-Search decision. It refreshes and revalidates again immediately after `ModelPort.answer`, before reading generated claims or text. A revoked, unavailable, or changed decision discards the provider result and fails closed.
2. **Canonical Azure provenance:** policy grants, resolved resources, source revisions, evidence, and citations require exactly `sha256:` plus 64 lowercase hexadecimal characters. Git revisions require a 40- or 64-character lowercase hexadecimal commit ID; blob/MySQL revisions remain explicit non-empty bounded strings. `AzureIndexTarget` now owns an immutable, non-empty, bounded source-type allowlist, and every selected row must match it. A malformed hash/revision or cross-route source type rejects the entire selected page.
3. **Architecture parent packages:** recursive AST checks now reject Chat access through `tap`, `tap.modules`, `tap.modules.knowledge`, the Knowledge API module object, non-allowlisted API symbols, and non-private aliases. The Access construction lint similarly recognizes policy-domain/access/module parent objects and permits the private factory only in `access/application/authorize.py`. Literal expectations are independent of repository import discovery.
4. **Strict public scores:** `exact`, `bm25`, `vector`, `rrf`, and `rerank` use one strict finite Pydantic numeric type. Booleans, numeric strings, `NaN`, and both infinities are rejected rather than coerced or serialized as null.
5. **LiteLLM route labels:** configuration now has separate, required, disjoint embedding-route and answer-route label sets. Both the response body `model` and the bounded `x-litellm-model-id` header are checked against the active route. Unknown/cross-route metadata produces a stable `ModelUnavailable` message without echoing credentials or untrusted values, while configured, gateway, provider, completion, and request identities remain separate.

### Files and Dependencies

Modified production/contract files:

- `apps/backend/src/tap/contracts/http.py`
- `apps/backend/src/tap/modules/access/domain/policy.py`
- `apps/backend/src/tap/modules/knowledge/domain/models.py`
- `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- `apps/backend/src/tap/modules/knowledge/adapters/azure_ai_search.py`
- `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`

Modified behavior/architecture/external-gate tests:

- `apps/backend/tests/unit/access/test_policy_context.py`
- `apps/backend/tests/contract/test_authorized_execution.py`
- `apps/backend/tests/contract/test_azure_search_strict.py`
- `apps/backend/tests/contract/test_knowledge_api.py`
- `apps/backend/tests/contract/test_litellm_strict.py`
- `apps/backend/tests/contract/test_public_retrieval_contract_strict.py`
- `apps/backend/tests/contract/test_resource_modes.py`
- `apps/backend/tests/architecture/test_module_boundaries.py`
- `apps/backend/tests/integration/test_search_acl.py`

The resource/LiteLLM/Azure test fixtures were migrated from descriptive hash placeholders to fixed canonical SHA-256 values so constructor validation cannot mask behavior assertions. The real Azure fixture now requires a sanitized `TAP_AZURE_SEARCH_ALLOWED_SOURCE_TYPES_JSON` route allowlist. There was no dependency or lockfile change, and regenerated public contracts remained byte-identical.

### TDD RED Evidence

Each accepted finding received focused behavior tests before its production change:

- Post-provider sequencing (`test_authorized_execution.py`, selector covering Search/answer-generation revocation and all early abstentions): `5 failed, 22 deselected`. Search inspected the poison result (`AssertionError: revoked Search results must not be inspected`); three early paths and answer generation reported `DID NOT RAISE AuthorizationDenied`.
- Canonical policy grants (`test_policy_context.py`, resource-policy selector): `7 failed, 4 passed, 17 deselected`; each noncanonical Git/hash mutation reported `DID NOT RAISE`.
- Canonical framework-free provenance (`test_knowledge_api.py`, revision/hash selector): `7 failed, 45 deselected`; invalid source revisions, source hashes, resolved-resource facts, and evidence hash all reported `DID NOT RAISE`.
- Azure selected-page provenance/route tests (`test_azure_search_strict.py`, malformed/hash/revision/source-type selector): `7 failed, 16 passed, 94 deselected`. Three non-empty malformed fields reported `DID NOT RAISE`, and the route allowlist did not yet exist (`unexpected keyword argument 'allowed_source_types'`).
- Parent-package architecture literals (`test_module_boundaries.py`, new Chat/policy literal selectors): `19 failed, 5 deselected`, because both decision helpers were absent (`NameError`).
- Strict public scores (`test_public_retrieval_contract_strict.py -k every_public_score`): `30 failed, 63 deselected`; all five fields accepted every boolean/string/non-finite mutation and reported `DID NOT RAISE ValidationError`.
- LiteLLM route labels (`test_litellm_strict.py`, cross-route/allowlist selector): `7 failed, 22 deselected`. Cross-route body/header labels were accepted, an unknown gateway label escaped as raw `ValueError`, and route-specific configuration fields did not yet exist.

After the canonical constructors became strict, one existing adapter test fixture that used `dataclasses.replace` to create invalid domain values failed during collection at the new constructor boundary. It was changed to an explicit post-construction `object.__setattr__` runtime-annotation bypass, preserving the original adapter fail-before-egress mutation rather than weakening production validation.

### GREEN and Repository Verification

Focused GREEN evidence:

```text
post-provider sequencing: 5 passed, 22 deselected
canonical policy/domain/Azure/resource suites: 127 passed
architecture boundary suite: 27 passed
strict public-score selector: 30 passed, 63 deselected
LiteLLM/Knowledge route selector: 31 passed, 50 deselected
complete focused Task 3 plus ordinary external gates: 303 passed, 2 skipped in 0.41s
```

Task 2 relay regressions:

```text
28 passed in 0.97s
```

Repository gates on the final code/test tree:

```text
make check
  ruff check: All checks passed!
  ruff format --check: 40 files already formatted
  mypy: Success: no issues found in 25 source files
  deterministic contract check: exit 0

make test
  337 passed, 2 skipped in 3.99s

make bootstrap
  uv sync --frozen --all-groups: Audited 32 packages
  pnpm install --frozen-lockfile: Already up to date
```

Three consecutive contract exports were stable. The final before/after checksums were identical: OpenAPI `1443310812/34532` and Chat stream schema `2876493190/27072`. The exporter `--check`, `git diff --exit-code -- contracts`, and `git diff --check` all exited `0`. Generated-schema inspection found both `queryPlanId` and `contextSnapshotId` in both Retrieval responses and zero public properties named `tenantId`, `projectId`, `allowedGroupIds`, `classification`, `filter`, `rawFilter`, `physicalIndex`, or `queryIndex`.

### External Gates: Exact Unrun Status

Ordinary local mode reported exactly `2 skipped`; these are limitations, not GREEN.

With `TAP_RUN_AZURE_INTEGRATION=1`, the Azure ACL gate failed closed before network access because all 18 sanitized settings were absent:

```text
TAP_AZURE_SEARCH_ENDPOINT, TAP_AZURE_SEARCH_API_KEY,
TAP_AZURE_SEARCH_INDEX, TAP_AZURE_SEARCH_PHYSICAL_INDEX,
TAP_AZURE_SEARCH_SCHEMA_VERSION, TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID,
TAP_AZURE_SEARCH_VECTOR_DIMENSION, TAP_AZURE_SEARCH_ALLOWED_SOURCE_TYPES_JSON,
TAP_AZURE_TEST_TENANT_ID, TAP_AZURE_TEST_PROJECT_ID,
TAP_AZURE_TEST_ALLOWED_GROUP_ID, TAP_AZURE_TEST_DENIED_GROUP_ID,
TAP_AZURE_TEST_CLASSIFICATION_CEILING, TAP_AZURE_TEST_ENVIRONMENT,
TAP_AZURE_TEST_CORPUS_VERSION, TAP_AZURE_TEST_EXPECTED_SOURCE_ID,
TAP_AZURE_TEST_QUERY_VECTOR_JSON, TAP_AZURE_TEST_DATASET_MARKER
```

With `TAP_RUN_ENTRA_POLICY_INTEGRATION=1`, the current-policy gate failed closed before network access because all eight sanitized settings were absent:

```text
TAP_POLICY_TEST_ACTIVE_URL, TAP_POLICY_TEST_REVOKED_URL,
TAP_POLICY_TEST_BEARER_TOKEN, TAP_POLICY_TEST_TENANT_ID,
TAP_POLICY_TEST_PROJECT_ID, TAP_POLICY_TEST_USER_ID,
TAP_POLICY_TEST_ACTIVE_DECISION_ID, TAP_POLICY_TEST_DATASET_MARKER
```

Therefore the real Azure authorized-positive/unauthorized-hit-zero contract and real Entra active-then-revoked contract remain **NOT RUN** on this machine.

### Fix-Round 2 Self-Review and Concerns

- Rechecked the five accepted findings against code and mutation assertions. Provider outputs have no inspection/response path before their post-call current-policy refresh and immutable binding check.
- Confirmed canonical hashes/revisions are enforced independently at policy, domain, adapter-row, evidence, and citation boundaries; source-type acceptance is per explicit server route, not inferred from a browser field.
- Confirmed cross-index order still uses family-local rank/RRF, final result caps are unchanged, and raw Azure scores never merge indexes.
- Confirmed LiteLLM route identities are disjoint, fixed configured IDs remain caller-inaccessible, only allowlisted metadata crosses the port, and failure messages contain neither credentials nor untrusted label values.
- Confirmed recursive architecture checks retain relative-import canonicalization and detect the accepted parent-package/module-object paths.
- Confirmed the ignored controller ledger and Fix Round 2 brief were neither modified nor staged.

Remaining concerns are operational only: Azure and Entra behavior still require controlled sanitized external fixtures, and later tasks must supply runtime configuration/wiring for the already-required verifier, redactor, auth, index routes, and model routes. Those items remain deliberately outside Task 3.

## Fix Round 3

### Status and Accepted Findings

Fix Round 3 closes all four accepted provenance-boundary findings without adding Task 4+ or Task 5 assembly.

1. **Architecture source scanner:** the recursive dependency lint now recognizes root `tap` module objects, star imports, every private policy symbol (including `_CONSTRUCTION_TOKEN`), literal dynamic imports through module/aliased `importlib.import_module`, direct/aliased `__import__`, and aliased `builtins.__import__`. An unresolved target through a recognized dynamic-import facility emits a fail-closed sentinel. Real source snippets flow through `ast.parse` and the same scanner used against repository files. This remains accurately scoped as a conservative dependency lint, not a Python security capability.
2. **Generic SearchPort provenance:** every hit is checked before evidence/citation materialization, whether or not resource scope exists. The closed mapping is `code -> git_commit + CodeAnchor`, `bdd -> git_commit + BddAnchor`, `doc -> blob_version + DocumentAnchor|OpenApiAnchor`, and `failure -> mysql_version + FailureAnchor`. Known source-type labels cannot contradict the family. Unknown DOC subtypes remain available to the Azure target's explicit server route allowlist, which is unchanged.
3. **LiteLLM evidence egress:** `_bounded_evidence` revalidates the current runtime `RevisionKind`, conditional canonical Git commit, canonical source SHA-256, and canonical chunk SHA-256 immediately before payload serialization. Runtime-mutated uppercase, non-hex, wrong-length, and open-enum lookalikes fail before HTTP with stable, secret-safe `ModelUnavailable` errors.
4. **Public citation provenance:** public source identifiers/types/revisions are strict and bounded; Git revisions receive conditional canonical validation; and source/chunk content hashes require exactly `sha256:` plus 64 lowercase hexadecimal characters. Both direct Pydantic DTO parsing and internal-to-HTTP mapping revalidate these facts. Opaque bounded Blob and MySQL revisions remain accepted.

### Files and Dependencies

Modified production/contract files:

- `apps/backend/src/tap/contracts/http.py`
- `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- `contracts/openapi/api.json`

Modified behavior/architecture tests:

- `apps/backend/tests/architecture/test_module_boundaries.py`
- `apps/backend/tests/contract/test_authorized_execution.py`
- `apps/backend/tests/contract/test_knowledge_api.py`
- `apps/backend/tests/contract/test_litellm_strict.py`
- `apps/backend/tests/contract/test_public_retrieval_contract_strict.py`
- `apps/backend/tests/contract/test_resource_modes.py`

The resource-mode and fusion fixtures were adjusted to carry family-compatible provenance, so they continue testing required-resource and cross-index RRF behavior after the new generic boundary moved incompatible family/revision combinations to an earlier fail-closed result. No dependency or lockfile change was needed.

### TDD RED Evidence

All accepted findings received mutation-sensitive tests before production changes:

- Architecture real-source scanner selector: `21 failed, 27 deselected in 0.10s`. Root/star/private-token cases produced no forbidden reference, while dynamic calls were absent from the import set.
- Generic SearchPort selector: `13 failed, 1 passed, 27 deselected in 0.20s`. The exact CODE + `document` + Blob + `DocumentAnchor` attack, eight family/revision/anchor mutations, and four known source-type contradictions all reported `DID NOT RAISE AuthorizationDenied`; only the deliberately unknown DOC subtype passed.
- LiteLLM runtime canonical selector: `10 failed, 29 deselected in 0.12s`. Every closed-kind/Git/source-hash/chunk-hash mutation reported `DID NOT RAISE ModelUnavailable`, proving it would reach the configured HTTP client.
- Direct public DTO selector: `15 failed, 5 passed, 93 deselected in 0.14s`. All noncanonical Git/source/chunk hashes and the three overlength provenance values reported `DID NOT RAISE ValidationError`; strict wrong-type cases and opaque Blob/MySQL positive cases already behaved correctly.
- Internal-to-HTTP mapping selector: `6 failed, 52 deselected in 0.18s`. Runtime-mutated source revisions, source hashes, hit chunk hashes, and citation chunk hashes all reported `DID NOT RAISE ValidationError`.

The first complete focused run after production GREEN reported `3 failed, 371 passed, 2 skipped`. Root-cause tracing showed no production widening/regression: two resource-mode fixtures intentionally paired CODE metadata with DOC/Blob values, and the cross-index fixture paired a DOC family with CODE provenance. Replacing those fixtures with valid family-local provenance retained their original required-resource/RRF assertions; no production check was weakened.

### GREEN and Repository Verification

Focused GREEN evidence:

```text
architecture boundary suite: 48 passed in 0.10s
generic SearchPort selector: 14 passed, 27 deselected in 0.11s
LiteLLM canonical egress selector: 10 passed, 29 deselected in 0.08s
public DTO + mapping selector: 26 passed, 145 deselected in 0.15s
complete focused Task 3 + ordinary external gates: 374 passed, 2 skipped in 0.47s
```

Task 2 relay regressions:

```text
28 passed in 1.08s
```

Repository gates on the final code/test tree:

```text
make check
  ruff check: All checks passed!
  ruff format --check: 40 files already formatted
  mypy: Success: no issues found in 25 source files
  deterministic contract check: exit 0

make test
  408 passed, 2 skipped in 4.40s

make bootstrap
  uv sync --frozen --all-groups: Audited 32 packages
  pnpm install --frozen-lockfile: Already up to date
```

Two consecutive `make contracts` runs produced identical checksums: OpenAPI `752896961/34857` and Chat stream schema `2876493190/27072`; the exporter `--check` exited `0`. Generated-schema inspection found the canonical SHA-256 pattern in source and both chunk-hash slots, both opaque response IDs in both Retrieval responses, and no public property named `tenantId`, `projectId`, `allowedGroupIds`, `classification`, `filter`, `rawFilter`, `physicalIndex`, or `queryIndex`. The diff secret scan and `git diff --check` exited `0`.

### External Gates: Exact Unrun Status

Ordinary local mode reported exactly `2 skipped in 0.05s`; these are limitations, not GREEN.

With `TAP_RUN_AZURE_INTEGRATION=1`, the Azure ACL gate failed closed before a network call because all 18 sanitized settings were absent:

```text
TAP_AZURE_SEARCH_ENDPOINT, TAP_AZURE_SEARCH_API_KEY,
TAP_AZURE_SEARCH_INDEX, TAP_AZURE_SEARCH_PHYSICAL_INDEX,
TAP_AZURE_SEARCH_SCHEMA_VERSION, TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID,
TAP_AZURE_SEARCH_VECTOR_DIMENSION, TAP_AZURE_SEARCH_ALLOWED_SOURCE_TYPES_JSON,
TAP_AZURE_TEST_TENANT_ID, TAP_AZURE_TEST_PROJECT_ID,
TAP_AZURE_TEST_ALLOWED_GROUP_ID, TAP_AZURE_TEST_DENIED_GROUP_ID,
TAP_AZURE_TEST_CLASSIFICATION_CEILING, TAP_AZURE_TEST_ENVIRONMENT,
TAP_AZURE_TEST_CORPUS_VERSION, TAP_AZURE_TEST_EXPECTED_SOURCE_ID,
TAP_AZURE_TEST_QUERY_VECTOR_JSON, TAP_AZURE_TEST_DATASET_MARKER
```

With `TAP_RUN_ENTRA_POLICY_INTEGRATION=1`, the current-policy gate failed closed before a network call because all eight sanitized settings were absent:

```text
TAP_POLICY_TEST_ACTIVE_URL, TAP_POLICY_TEST_REVOKED_URL,
TAP_POLICY_TEST_BEARER_TOKEN, TAP_POLICY_TEST_TENANT_ID,
TAP_POLICY_TEST_PROJECT_ID, TAP_POLICY_TEST_USER_ID,
TAP_POLICY_TEST_ACTIVE_DECISION_ID, TAP_POLICY_TEST_DATASET_MARKER
```

Therefore the real Azure authorized-positive/unauthorized-hit-zero contract and the real Entra active-then-revoked contract remain **NOT RUN** on this machine and are not claimed as GREEN.

### Fix-Round 3 Self-Review and Concerns

- Re-read the binding disposition and reviewed each changed stable layer, adapter egress, public DTO, generated schema, and mutation test against the four accepted findings.
- Confirmed the generic port mapping runs for every hit before any evidence/citation creation, while the Azure provider route still owns its exact immutable source-type allowlist. Cross-index fusion remains family-local rank/RRF and its final cap is unchanged.
- Confirmed LiteLLM validates only current runtime values, never echoes malformed values or credentials, and performs zero transport calls on every new mutation.
- Confirmed architecture tests execute source snippets through the real AST scanner, root/star/private/dynamic paths fail closed, and unrelated public policy imports remain permitted. The scanner documentation states its conservative lint-only threat model.
- Confirmed the public schema still exposes neither authorization facts nor physical indexes, and public mapping cannot serialize a post-construction canonical lookalike.
- The ignored controller ledger and Fix Round 3 brief were not modified or staged.

Remaining concerns are operational only: Azure and Entra/Project-Policy behavior still requires controlled sanitized external fixtures, and later tasks must provide runtime wiring for the already-defined verifier, redactor, credentials, index routes, and model routes. Those are deliberately outside Task 3.

## Fix Round 4

### Status and Accepted Findings

Fix Round 4 closes both accepted boundary bypass groups without adding Task 4+ or Task 5 assembly.

1. **Conservative dynamic-import and authorizer lint:** the real AST scanner now follows simple and annotated assignment aliases of recognized `importlib.import_module` and `builtins.__import__` callables to a fixed point, including alias-to-alias chains. Literal relative `import_module` targets are resolved with a statically known keyword or second positional package; missing, conflicting, nonliteral, invalid, and built-in-relative targets emit the fail-closed sentinel. Ordinary modules cannot import the Access application/authorizer module object, dynamically reach those parents, or statically/dynamically re-export the private factory, while the exact public `build_retrieval_policy_context` symbol remains allowed. The test documentation continues to describe this as conservative dependency lint rather than a Python sandbox.
2. **Family-compatible public/internal provenance:** one shared public compatibility rule derives a single family from each closed anchor and requires its revision kind to agree: Code/BDD use Git, Document/OpenAPI use Blob, and Failure uses MySQL. Known source-type labels must match that family; unknown route-specific labels remain allowed only with compatible revision/anchor facts and the outer hit family. `RetrievalHit.indexFamily` is bound to the derived source family. Internal `Citation` retains its family without exposing a new public property, and both Evidence and Citation mapping reconstruct and revalidate their current runtime values before HTTP serialization.

### Files and Dependencies

Modified production/contract files:

- `apps/backend/src/tap/contracts/http.py`
- `apps/backend/src/tap/modules/knowledge/api.py`
- `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- `apps/backend/src/tap/modules/knowledge/domain/models.py`

Modified behavior/architecture tests:

- `apps/backend/tests/architecture/test_module_boundaries.py`
- `apps/backend/tests/contract/test_knowledge_api.py`
- `apps/backend/tests/contract/test_public_retrieval_contract_strict.py`

Two legacy positive public fixtures were corrected to pair their Blob/MySQL revision and anchor facts with the matching `document`/`failure` source-type labels. Their bounded opaque-revision assertions were retained; the production rule was not weakened. There was no dependency or lockfile change, and regenerated public contracts remained byte-identical.

### TDD RED Evidence

Both accepted groups received mutation-sensitive tests before production changes:

- Assignment aliases, unresolved targets, and Access authorizer re-export selector: `15 failed, 1 passed, 51 deselected in 0.10s`. Assigned dynamic calls were absent from the discovered references, unresolved assigned targets did not emit the sentinel, and the authorizer paths were not classified as construction exposure; only the explicit public builder positive control passed.
- Relative dynamic-import resolution selector: `3 failed, 64 deselected in 0.03s`. The scanner retained the relative dotted targets instead of producing the hand-derived guarded absolute module names.
- Direct public provenance selector: `19 failed, 1 passed, 113 deselected in 0.17s`. Every revision/anchor mismatch, known source-type contradiction, outer hit-family mismatch, and incompatible nested Citation source reported `DID NOT RAISE ValidationError`; the deliberately unknown DOC subtype positive control passed.
- Internal mapping selector: `6 failed, 58 deselected in 0.20s`. Runtime-mutated Evidence family/revision/anchor/source combinations and Citation family/source combinations all reported `DID NOT RAISE ValidationError`.

The first combined public DTO/Knowledge mapping run after production GREEN reported `2 failed, 195 passed`. Root-cause tracing showed both failures were the legacy positive fixtures described above: they inherited `sourceType=code` while explicitly constructing valid DOC/Blob and FAILURE/MySQL provenance. Correcting only those hand-derived fixtures produced `197 passed in 0.23s`.

### GREEN and Repository Verification

Focused GREEN evidence:

```text
new architecture selector: 19 passed, 48 deselected in 0.03s
complete architecture boundary suite: 67 passed in 0.12s
direct public provenance selector: 20 passed, 113 deselected in 0.09s
internal mapping selector: 6 passed, 58 deselected in 0.13s
complete public DTO + Knowledge mapping suites: 197 passed in 0.23s
complete focused Task 3 + ordinary external gates: 419 passed, 2 skipped in 0.55s
```

Task 2 relay regressions:

```text
28 passed in 1.06s
```

Repository gates on the final code/test tree:

```text
make check
  ruff check: All checks passed!
  ruff format --check: 40 files already formatted
  mypy: Success: no issues found in 25 source files
  deterministic contract check: exit 0

make test
  453 passed, 2 skipped in 4.50s

make bootstrap
  uv sync --frozen --all-groups: Audited 32 packages
  pnpm install --frozen-lockfile: Already up to date
```

Two consecutive contract generations were stable. The final generated checksums remained OpenAPI `752896961/34857` and Chat stream schema `2876493190/27072`; the exporter `--check` and `git diff --exit-code -- contracts` both exited `0`. Generated-schema property inspection found zero public properties named `tenantId`, `projectId`, `allowedGroupIds`, `classification`, `filter`, `rawFilter`, `physicalIndex`, or `queryIndex`. The added-line private-key/bearer/credential scan and `git diff --check` exited `0`.

### External Gates: Exact Unrun Status

Ordinary local mode reported exactly `2 skipped in 0.05s`; these are limitations, not GREEN.

With `TAP_RUN_AZURE_INTEGRATION=1`, the Azure ACL gate failed closed before a network call because all 18 sanitized settings were absent:

```text
TAP_AZURE_SEARCH_ENDPOINT, TAP_AZURE_SEARCH_API_KEY,
TAP_AZURE_SEARCH_INDEX, TAP_AZURE_SEARCH_PHYSICAL_INDEX,
TAP_AZURE_SEARCH_SCHEMA_VERSION, TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID,
TAP_AZURE_SEARCH_VECTOR_DIMENSION, TAP_AZURE_SEARCH_ALLOWED_SOURCE_TYPES_JSON,
TAP_AZURE_TEST_TENANT_ID, TAP_AZURE_TEST_PROJECT_ID,
TAP_AZURE_TEST_ALLOWED_GROUP_ID, TAP_AZURE_TEST_DENIED_GROUP_ID,
TAP_AZURE_TEST_CLASSIFICATION_CEILING, TAP_AZURE_TEST_ENVIRONMENT,
TAP_AZURE_TEST_CORPUS_VERSION, TAP_AZURE_TEST_EXPECTED_SOURCE_ID,
TAP_AZURE_TEST_QUERY_VECTOR_JSON, TAP_AZURE_TEST_DATASET_MARKER
```

With `TAP_RUN_ENTRA_POLICY_INTEGRATION=1`, the current-policy gate failed closed before a network call because all eight sanitized settings were absent:

```text
TAP_POLICY_TEST_ACTIVE_URL, TAP_POLICY_TEST_REVOKED_URL,
TAP_POLICY_TEST_BEARER_TOKEN, TAP_POLICY_TEST_TENANT_ID,
TAP_POLICY_TEST_PROJECT_ID, TAP_POLICY_TEST_USER_ID,
TAP_POLICY_TEST_ACTIVE_DECISION_ID, TAP_POLICY_TEST_DATASET_MARKER
```

Therefore the real Azure authorized-positive/unauthorized-hit-zero contract and real Entra active-then-revoked contract remain **NOT RUN** on this machine and are not claimed as GREEN.

### Fix-Round 4 Self-Review and Concerns

- Re-read the binding disposition and reviewed every changed source, behavior test, architecture snippet, generated schema, and mapping boundary against both accepted findings.
- Confirmed scanner tests feed executable source snippets through `ast.parse` and the repository scanner. Direct aliases, alias chains, keyword/positional relative packages, missing/nonliteral packages, static module imports, and dynamic parent/authorizer imports have independent assertions. The exact public builder remains a positive control.
- Confirmed public validation derives family independently from the closed anchor, then checks revision kind, known source type, and outer hit family. Unknown DOC subtypes remain possible for Azure's explicit route allowlist; this change does not widen the adapter route.
- Confirmed runtime-mutated Evidence and Citation values are reconstructed at the HTTP boundary. Citation's retained family is internal only and does not alter the closed public DTO or generated schema.
- Confirmed all earlier-round policy refresh, immutable bindings, generic SearchPort checks, finite scores, Azure route/canonical provenance, LiteLLM route labels/canonical egress, cross-index RRF, and architecture selectors remain GREEN through the focused and full suites.
- Confirmed the ignored controller ledger and Fix Round 4 brief were neither modified nor staged.

Remaining concerns are operational only: Azure and Entra/Project-Policy behavior still requires controlled sanitized external fixtures, and later tasks must supply runtime configuration/wiring for the already-defined verifier, redactor, credentials, index routes, and model routes. Those items remain deliberately outside Task 3.
