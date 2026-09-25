# Owned E2E citation preview investigation

## Result and scope

The published answer citation and its HTTP preview now carry the same approved `inventoryItemId`. The owned `make demo-e2e` journey advanced beyond the original citation comparison and its restart-state anchor hash. The full journey remains red on a later, separate browser-console check: 21 `GET /conversations/{id}/events` responses returned HTTP 503 (`knowledge-runtime-unavailable`). Restart and persistence phases were not reached. This does not establish real-model or real-reviewer acceptance.

## Root cause evidence

- The prior owned run returned HTTP 200 for citation preview, but its document anchor contained `inventoryItemId: null` while the published answer citation contained an approved `pi_…` item. The browser comparison failed at `tests/e2e/tapper.spec.ts:424`.
- `CitationResolver._document_anchor` parses `inventoryItemId` from the immutable citation anchor and places it in its domain `DocumentAnchor`. The public HTTP `DocumentAnchor` DTO also has the field. `KnowledgeHttpService._citation_preview` copied heading, page, bounds, and offsets into that DTO but omitted `inventory_item_id`. This is the exact boundary where the value became `null`; publication authorization had already used the approved item ID.
- An existing real HTTP route test was changed to provide `pi_approved` in the citation result and require that exact value in the response. Before the production change, the response still had HTTP 200 and `inventoryItemId: null`, establishing the omission independently of the browser fixture.

## RED, minimal fix, GREEN

1. RED: `UV_CACHE_DIR=/private/tmp/tap-citation-uv-cache UV_NO_SYNC=1 uv run --project apps/tap-ai-backend pytest -q apps/tap-ai-backend/tests/integration/test_knowledge_answer_http.py::test_http_normalizes_selection_and_maps_answer_and_citation_dtos` exited 1. The only differing response field was `anchor.inventoryItemId`: expected `pi_approved`, received `None`.
2. The one-line server fix passes `anchor.inventory_item_id` into the public preview `DocumentAnchor`. No resolver, publication, persistence, or generated contract changed.
3. GREEN: the full knowledge HTTP test file plus citation resolver unit file passed, `51 passed in 0.95s`. Ruff check and format check passed for the two changed Python files.
4. First owned `make demo-e2e` after the server fix passed the old citation comparison, then failed with `invalid document anchor` while building the journey's restart evidence. Its test-only `canonicalAnchorHash` rejected the newly present `inventoryItemId` as an unknown key. This run is the RED behavior for that directly affected fixture helper.
5. The fixture helper now validates and hashes the approved item ID along with the rest of the anchor. Frontend `tsc --noEmit` and Prettier check passed. The second owned `make demo-e2e` reached the final browser error assertions, beyond both the citation comparison and anchor hashing; it failed there for the separate 503s below.

## Remaining blocker and isolation

- The second browser trace recorded 21 console errors, each a failed resource load for `GET /api/v1/projects/tapper-demo/conversations/{id}/events` with HTTP 503. A captured problem response had type `knowledge-runtime-unavailable` and detail `The knowledge runtime is not configured.` The failing assertion was the end-of-journey `consoleFailures` empty check at `tests/e2e/tapper.spec.ts:714`. No citation request failed at that point. This task did not modify conversation runtime wiring or suppress the assertion.
- Both full runs used the repository's guarded `tap-tapper-e2e` Compose project and its private database and volumes. The first run could not start inside the filesystem sandbox because loopback `bind` returned `EPERM`; the same guarded command ran with approved host execution. It removed only its owned Compose volumes during cleanup. No shared/default services or volumes were targeted.
- `git diff --check` passed. Browser restart/persistence, real-model, real-reviewer, production authorization, and production-scale evidence remain not run or pending.
