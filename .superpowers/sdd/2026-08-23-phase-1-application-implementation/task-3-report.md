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
