# LCA real recording, editing and debug replay

## Scope and approval

The approved first milestone is a real web flow: record → edit steps and assertions → debug replay → inspect evidence. Flows can be saved. This milestone does not claim formal published test assets, Test Plan integration, mobile automation, arbitrary website compatibility or production deployment. OpenCode + Playwright is the current coordinated browser direction; deterministic LCA generation does not require an LLM.

This document is the architectural review artifact. Implementation begins after review of this written design. No commits or pushes are part of this work without further authorization.

## Existing foundations and constraints

- TAP non-AI backend owns real browser sessions, action receipts, frozen bundles, execution receipts and evidence. TAP AI consumes its project-scoped API.
- ADR-024 keeps TAP/MySQL authoritative and generated Playwright a derived artifact. Recorder/manual/AI inputs must not become competing executable truths.
- ADR-037 permits debug browser execution before formal asset publication. Saved debug flows are explicitly not Published Automation Revisions.
- Existing LCA local-storage artifacts are prototypes. Keep them accessible and labelled simulated; do not silently migrate their results into real execution history.
- Existing browser API credentials identify a backend service, not a frontend user. Never place the service credential in browser JavaScript or local storage.
- Coordinate shared session control with AA. LCA must not introduce an independent controller or bypass its ownership fencing.

## Approach

Use a TAP-controlled isolated browser, rather than an installed browser extension or full test-asset migration as a prerequisite. A shared human-control surface provides live observation and explicit acknowledged interactions. LCA records these interactions into a draft; editing and generating code do not execute them again.

UiPath informs recording, target inspection and visual step editing. Robot Framework informs named operations, typed arguments, explicit assertions and structured results. Neither becomes a second storage authority or a required runtime dependency.

Full asset migration would delay the first real flow. An extension would add installation permissions and exposure to unrelated pages. Both are outside this increment.

## Ownership and interfaces

### Shared browser control (coordinated with AA)

The shared controller owns human/agent control state, controller epoch, session lifecycle, observation version, target resolution and action acknowledgement. Consume the coordinated AA browser-control-v1 contract, using human_active for recordable human ownership and its typed terminal receipts; pending/dispatching ledger rows are not acknowledgements. Human commands include session ID, controller epoch, observation sequence and a stable command ID. A repeated command ID returns the existing receipt; a stale epoch or stale observation rejects before dispatch. No frontend optimistic retry may create another command identity.

The recording consumer needs these semantic fields in a receipt: project/session ID, command ID, controller epoch, action ID and ordered sequence, acknowledged operation and typed arguments, resolved target, terminal outcome, post-action observation sequence and evidence reference. Resolve and bind the semantic target against the pre-action observation before DOM mutation or navigation. Exact transport adapters belong to the shared controller implementation, not a duplicate LCA endpoint. Unknown outcome remains unknown and is not treated as recorded success.

AA owns the shared controller implementation; LCA consumes it. The agreed control contract uses actor-bound admission plus pre-dispatch fencing, ephemeral viewing frames without raw CDP exposure, and a fresh observation on handback.

A session has one writer. Recording opens in human ownership; agent execution cannot continue simultaneously. Stop recording closes admission and records a sequence watermark. It waits for admitted commands through that watermark to settle within the session deadline, then freezes only successful acknowledged actions. Pending/unknown actions remain explicitly excluded and visible. Late receipts cannot silently mutate a frozen snapshot; incorporating a later confirmed success requires an explicit new draft version. Test stop during an in-flight action and delayed receipt delivery. Cancel/close follows existing session cancellation and preserves receipts.

### LCA debug-flow domain

TAP non-AI backend owns a separately named debug-flow module and project-scoped MySQL records:

- DebugFlow: stable ID, project, title, owner, current draft version.
- DebugDraft: optimistic version, canonical structured definition, recording provenance, update time.
- DebugSnapshot: immutable canonical bytes/digest, schema and generator versions, frozen definition and allowed origins.
- DebugRun: snapshot ID/digest, frozen browser bundle ID, execution ID, actor and explicit debug classification.

These are not Test Plan/Case or published Automation records. Formal publication later adopts the canonical definitions with explicit lineage, not an automatic relabelling of debug history. No new writes to legacy AI test-plan ownership are introduced.

Draft save requires an expected version. Stale writes return conflict; never overwrite another editor. Creation and replay requests require project-scoped idempotency keys; same key with changed input rejects.

## Canonical definition

Versioned JSON contains stable step IDs, target definitions, parameter definitions and ordered operations. BDD/business intent is optional descriptive mapping and is not an independent executable script.

Initial operations: navigate, click, fill, select, assert_text and assert_url. Assertions are explicit assertion entries, never inferred merely from the absence of an exception. Each entry has an ID, operation, typed arguments, optional target reference, timeout and provenance. Timeouts are positive bounded integers. Duplicate IDs, unknown operations and undeclared parameter references fail validation.

Targets contain a stable ID and one explicit validated locator: test ID or role plus accessible name, with CSS fallback explicitly marked for review. Do not silently change a target when replay fails. Locator values are data, never executable JavaScript. The first milestone supports the main document only; iframe, popup, download, upload and shadow-DOM recording outside supported locator behavior are visibly unsupported rather than silently dropped.

Parameters have names, types and required/default semantics. A replay fixes the supplied data digest. Real credentials are not supported in the first fixture milestone: password controls and explicitly secret-designated inputs are rejected before command persistence, not merely masked in observations. Arbitrary secrets typed into ordinary text fields cannot reliably be detected; this is not a general secret-detection claim. Secret references and secure runtime resolution must be implemented before credential-bearing site acceptance. Do not claim that password-field masking protects action requests, traces or screenshots.

A recording action becomes a step only after an acknowledged successful outcome. Failed/unknown interactions remain visible in the recording log for review. Recording captures what happened; it must not manufacture an assertion that the business goal succeeded. Navigation caused by a click is observation metadata, not an extra replay navigation command.

## Generation and execution

A deterministic validator checks canonical schema, targets, argument types, origins, step limits and reference integrity. A generator uses JSON-safe literal encoding and fixed templates, never eval or user-supplied source interpolation. Generated Playwright wraps each canonical entry in a uniquely identified test.step and emits an explicit canonical-ID mapping. Reporter results must distinguish canonical entries from Playwright internal steps; title matching alone is insufficient authority.

Debug replay requires an explicit user command. The server freezes the definition, input digest, generator version and resulting bundle before queuing execution. Replay uses a fresh isolated browser context, the existing origin/network policy and bounded runtime limits. Recording context cookies/profile are not silently copied. No arbitrary helper code or raw-script editor is exposed in this milestone.

Use the existing frozen-bundle execution authority. Integrate through a narrow internal service boundary rather than introducing a second worker or execution table state machine. Create the DebugRun intent and a stable execution idempotency key before queueing. On timeout or crash, reconciliation queries or resubmits the same frozen request under that same key and persists the existing execution ID. A crash after enqueue but before linking must neither lose the history intent nor dispatch a second execution. New debug runs reference existing execution receipts. Saving, validating or generating never queues execution.

A dispatch timeout is not proof of failure. Query the same idempotency receipt before any retry. Unknown action or execution outcome requires reconciliation or explicit new-run intent; do not automatically resubmit business actions. Existing terminal rerun guard stays intact.

## UI and evidence

Add a real debug-flow entry alongside clearly labelled simulated prototypes. The workflow is: name flow/start recording → operate live browser → stop and review → edit targets/arguments/order and add assertions → save → debug replay → inspect history.

Display recording and replay as different activities. Warn that recording itself performs real actions. Run history uses the frozen snapshot, not the mutable draft. Show each canonical step's actual status, bounded failure detail and available evidence. Missing evidence is labelled missing. Unknown outcomes are neither successful nor safely failed.

Screenshots/traces may contain page data. Keep project authorization on every evidence read, bounded storage, private object references and explicit retention behavior. Initial acceptance uses only owned synthetic pages and non-secret data. No external business website or paid model call is required.

## Authorization and persistence

Use the coordinated AA local application-gateway capability adapter: short-lived opaque capabilities bound to project/task/session, controller and permitted operations, with same-origin/CSRF checks. Retain backend service credentials server-side. This is a trusted local-development boundary, not production multi-user authentication; real-user deployment needs separate authentication acceptance. Verify actor, project, permitted operation and session ownership before recording, editing, executing or evidence access. Cross-project IDs must not reveal content. Existing loopback deployment is not an authentication design for wider exposure.

Use the TAP migration chain for debug-flow records. Snapshot/run publication must be recoverable: frozen object staging and digest verification precede a database pointer; retries reuse immutable digests, and orphan staging can be reconciled. Reuse existing content-addressed browser objects for executable bundles. Do not claim a distributed transaction across database/object store/worker.

## Acceptance and failure tests

1. On an owned fixture, record navigation, fill, select and one submit; the fixture proves submit occurred exactly once during recording. Stop/save/generate do not submit again.
2. Edit a parameter and add a text assertion. Explicit debug replay performs the flow once in a fresh context and produces a real passing receipt and evidence.
3. Deliberately wrong assertion produces a real failure at its canonical ID. A changed definition creates a new snapshot/run; previous failure and evidence remain unchanged.
4. Reload the UI and retrieve persisted draft and real run history from backend storage, not local-storage simulation.
5. Duplicate command/replay request, lost HTTP response, crash between enqueue and DebugRun linkage, and duplicate worker wake do not produce a second dispatch. Reconciliation restores history linkage under the original execution idempotency key. Unknown outcomes block automatic resubmission.
6. Stale editor version, stale controller epoch/observation, cross-project access, same-project unauthorized actors and unsupported recording actions fail explicitly. Evidence reads enforce the same actor/project policy. Cancellation during recording or replay preserves receipts and never resumes implicitly.
7. Reject password/secret-field recording before persistence; verify synthetic action inputs never masquerade as credential protection.
8. Test deterministic byte-for-byte generation, literal escaping, malicious selectors/arguments, duplicate IDs, missing data, evidence tampering and canonical result mapping.

Use unit/contract tests first, then owned isolated MySQL/browser integration. Real browser acceptance must execute generated code and inspect actual receipts, not mock the executor. Never use shared business data or shared database destructive resets. Default make test and affected boundary/type checks remain required; opt-in browser integration skips must be reported, not counted as passes.

## Delivery sequence and coordination

1. Review this architecture and shared-controller contract with AA; pin the shared browser-control-v1 generated contract and local gateway capability adapter in the implementation plan.
2. Implement canonical validation/generation and real fixture replay as an independently tested core, without presenting it as a complete recorder.
3. Add persisted debug drafts/snapshots and idempotent execution linkage.
4. Connect shared human-control recording and the editor/history UI.
5. Run the complete owned-site acceptance above; document actual supported operations and limitations.

Shared browser controller, session authentication and canonical result mapping are integration prerequisites. They cannot be replaced by screenshots alone, simulated receipts or a second temporary controller. Existing AA worktree ownership, user edits and ADR history remain preserved.

On written-spec approval, update the affected product-roadmap row with this debug-only scope and approval date before implementation. This draft does not mark a feature delivered.
