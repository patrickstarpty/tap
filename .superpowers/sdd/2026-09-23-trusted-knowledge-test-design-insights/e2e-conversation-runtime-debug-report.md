# Owned E2E conversation events investigation

## Result and boundary

The conversation `GET /events` 503 is fixed at the public HTTP event contract. The owned `make demo-e2e` run passed all five browser journey specifications, then passed both application-restart and Compose-restart browser checks. Its final Python persistence verification remains red at a separate `citation-anchor-shape` check. This run is not a complete E2E pass and does not establish real-model or real-reviewer acceptance.

## Root cause evidence

- The prior Playwright network trace contained 21 HTTP 503 responses for `GET /api/v1/projects/tapper-demo/conversations/177ca5fc8f86e36329dc5a0b142e54c8/events`. The same conversation's list and detail requests returned HTTP 200. The browser's 503 body was `knowledge-runtime-unavailable`, detail `The knowledge runtime is not configured.`
- The API lifespan installs `create_api_runtime(...).http_services`; that runtime assembles a MySQL `ConversationService`. The events route resolves that service and loads the same conversation as the working detail route. Thus the 503 did not show missing runtime wiring.
- Test Plan generation atomically writes `test-plan.generation.result_ready` (and other lifecycle variants) to `chat_event`. The persisted `ConversationEvent` domain and `ChatEventEnvelope` SSE union accept all four variants, but `ConversationEventItem.event_type`, the HTTP `/events` response model, omitted them. Response validation failed when the Test Plan journey left such an event in a conversation. The generic exception handler maps unexpected exceptions to the same `knowledge-runtime-unavailable` problem, obscuring the contract mismatch.

## RED, minimal fix, GREEN

1. Added a parameterized real HTTP route test using an in-memory `ConversationService` with each of the four persisted Test Plan lifecycle events. Its conversation detail returned HTTP 200, while `/events` returned HTTP 503 for all four cases. Command: `UV_CACHE_DIR=/private/tmp/tap-conversation-uv-cache UV_NO_SYNC=1 uv run --project apps/tap-ai-backend pytest -q apps/tap-ai-backend/tests/contract/test_conversation_http.py::test_conversation_events_http_reads_persisted_test_plan_lifecycle --tb=line`; result: **4 failed** with the same problem type and detail as the browser.
2. Added only those four event literals to `ConversationEventItem.event_type` and regenerated OpenAPI and the frontend TypeScript schema. No browser-error suppression, retry change, or exception swallowing was added.
3. Re-ran the exact test: **4 passed**. The complete conversation HTTP contract and SSE resume test files passed: **14 passed**. OpenAPI export check, generated TypeScript contract check, Ruff check, Ruff format check, and `git diff --check` passed.

## Owned end-to-end validation and next blocker

- The guarded run used `TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e` through `scripts/run-tapper-e2e.sh`, with its fixed private ports, database, and Compose volumes. Browser journey: **5 passed**. Application restart: **1 passed**. Compose restart: **1 passed**. Evidence projections are in `/private/tmp/tap-conversation-runtime-e2e-evidence/phase-{journey,app-restart,compose-restart}.json`; their `cleanup: failed` reflects the overall run failure, not a browser failure.
- Final persistence pytest: **27 passed, 1 failed**. `test_exact_tapper_state_survives_application_and_compose_restarts` failed with `citation-anchor-shape`. Its `_canonical_anchor_hash` accepts only `bbox`, `endOffset`, `headingPath`, `page`, `startOffset`, and `type`; the approved citation preview now also carries `inventoryItemId` after the preceding citation fix. This is the next independent fixture/verifier mismatch. It was left unchanged here.
- The runner printed `Tapper E2E cleanup failed` after evidence validation failed. Read-only post-run checks showed no `tap-tapper-e2e` containers, no volumes with that Compose project label, and no owned lock directory. No shared/default project or volume was targeted.
- Broader `make tap-ai-check` passed backend boundary, 22 architecture tests, contract checks, and Ruff, then stopped at pre-existing Prettier differences in unchanged `tests/e2e/publicationFixture.ts` and `tests/e2e/tapper.spec.ts`. `make tap-ai-test` was stopped after sandbox-only UNIX-socket `EPERM` failures in supervisor tests at 18%; it was not used as evidence for this fix. Frontend Vitest did not run in that command.
