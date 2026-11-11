# LCA page contexts and file actions

## Scope and acceptance

Extend the existing TAP BDD action editor and deterministic Playwright engine. This is the next common-Web-action batch, not a claim of complete browser or mobile parity. The existing flow, revision, publish, replay and evidence lifecycle remains authoritative. No replacement UI, arbitrary JavaScript, arbitrary host file path or externally fetched upload URL is introduced.

Acceptance requires typed editing, save/reopen, immutable published execution, actual generated Chromium execution, failure attribution and project-authorized evidence access. Controlled fixtures cover page, popup, dialog and file behaviors that are inappropriate to exercise on public insurance sites. Existing FWD/AIA regression evidence remains distinct from these synthetic cases.

## Page and dialog model

A step may optionally name `page_id`; omission preserves existing canonical bytes and uses the active page, initially `main`. `open_page` and `click_popup` create a fresh page ID without switching active page. `switch_page` changes the active page. `close_page` requires a live non-active page. Ordered validation rejects unknown, closed or reused page IDs; ordinary targets resolve within the chosen page and preserve iframe scoping.

Popup capture registers its bounded waiter before the explicit click and validates the resulting URL against allowed origins. Existing network restrictions continue to apply. Dialog handling is attached only to a specific `click_dialog` trigger, with explicit expected type, exact message and accept/dismiss decision. Prompt text is permitted only for an accepted prompt. An unmatched dialog is dismissed and fails the step; listeners are removed on completion or failure. No global automatic dialog acceptance is added.

## File model

User-selected upload bytes enter a bounded LCA assets API, not a machine path. An immutable opaque asset ID and SHA-256 identify project-and-actor-owned bytes. The upload size limit is 256,000 bytes; execution retains the existing total 1,000,000-byte inputs boundary. Metadata listings do not return bytes. The canonical upload action records asset ID and hash; service resolution verifies ownership and content before frozen execution input creation. Renaming or editing another draft cannot silently alter a previously frozen execution.

`download_file` is an explicit click-and-wait action. The worker captures a bounded artifact (maximum 5,000,000 bytes), associates it with existing run evidence and serves it only through the authorized attachment endpoint. Names are sanitized and are never used as arbitrary paths. The limit bounds captured evidence and application memory, not the native browser temporary transfer: Playwright may spool the download to its managed temporary directory before the bounded read. Existing execution deadlines and network restrictions still apply. Downloaded content is not executed or automatically opened. Failure, excessive size and absent download produce explicit failed-step evidence.

## Compatibility and boundaries

Optional fields are omitted from legacy canonical output. New operations are editor-authored first; this batch does not claim recording of all new actions. Page lifetime, action parameters, asset ownership and digest are validated rather than silently repaired. Existing safety checks and unknown-outcome handling remain unchanged.

File limits are intentionally bounded initial support, not unrestricted document ingestion. Nested iframe, mobile gestures, arbitrary multi-window recording and broad UiPath parity remain outside this batch. Local tests and controlled product acceptance do not establish remote CI status or recovery of unrelated full-stack startup failures.
