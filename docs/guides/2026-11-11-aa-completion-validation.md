# AA continuation validation — 2026-11-11

This is an incremental continuation of the approved OpenCode feedback-loop plan,
not a claim that the full Manus-style AA requirement is complete.

## Custom dropdown scope correction

The existing custom-selection helper searched top-level page options even when
its target was inside an iframe, and ignored `aria-controls` / `aria-owns` when
other dropdowns were visible. Real Chromium regressions reproduced both failure
modes: ambiguous same-name options and selecting the parent page's option rather
than the frame's requested value.

The live helper and newly generated frozen replay now resolve the selected
control's owner frame and its observed popup associations after opening it. They
retain exact visible matching and Playwright strictness; there is no first-match
fallback, arbitrary JavaScript from the model, or site-specific selector. Native
select and radio/checkbox behavior is unchanged. Unassociated custom controls
still use their owning frame, so isolation between unassociated dropdowns is not
guaranteed. Existing frozen bundles retain their original content.

## Verification

- Both new live regressions failed before the fix.
- Both generated replay regressions failed before the replay helper fix.
- Live and compiled replay Chromium suite: 15 passed, including previous closed,
  open, hidden-duplicate cases and the new association/frame cases.
- Tool observation and alias Chromium tests: 24 passed on the baseline.
- Affected Python browser/OpenCode regression: 88 passed.
- Ruff check, Ruff format check and `git diff --check`: passed.
- Independent source review found no blocking issue; the reviewer's Chromium
  launch was sandbox-blocked, so it adds no independent execution evidence.

These checks use controlled fixtures with no external model calls or new model
spend. They do not establish autonomous FWD/AIA quotation success, AI-generated
LCA draft integration, or full release/remote-CI completion. Those remain separate
acceptance requirements.

## Public FWD tool-level preflight

With the task-owned local Browser runtime and existing configured upstream proxy,
opening the public MyTerm Plus quote page, selecting synthetic age `30` through
the current observed readonly combobox, and exact `value=30` readback all returned
no error. The browser was confirmed stopped afterward. This used no model and
submitted no application, contact details or payment. It proves this tool-level
interaction works today, not an autonomous full quote task or recorded acceptance
video. The probe had no evidence sink, so its cleanup reported that no session
recording was persisted; observation JSON was saved separately.

## Recoverable pre-write failure regression

The normal-stack synthetic Chat → OpenCode → browser acceptance exposed a
regression: the browser worker classified every failed write as unknown, including
an invalid CSS selector that could not have executed. This stopped the task in
`recovering`, so model correction never ran. No paid model was used.

The trusted direct inspector now reports whether a write call actually started.
It parses/counts the locator before dispatch and rejects malformed/ambiguous
locators read-only. Only an explicit `write_started=false` from a legacy/direct
inspector allows the worker to publish a known failure. Missing/true proof and
controlled-input paths keep the unknown fence. The workflow preserves the actual
error and current observation for a known failure; it does not retry automatically.
Navigation, click/fill/select/press execution and uncertain outcomes remain
conservative. This does not declare all timeouts safe to retry.

Verification: new Python and Chromium regressions failed before the fix; 145
browser/authority/progress Python tests and 53 inspector Chromium/Node tests then
passed. Full normal-stack acceptance is rerun separately after this fix.

The corrected normal-stack synthetic acceptance subsequently passed through TAP
Chat, pinned OpenCode 1.19.5, real Chromium, recoverable locator correction,
verified result publication, and a separate frozen-script replay with zero model
calls. Task `51eaef68e7f74e8f83bfddae8333abd0` completed; replay task
`910564f826644d90afda47a1ab5bb6f3` preserved script SHA-256
`aa5a3c06a8a28cf9f16feea364f239a7b5829b11780e2490e1ca93261c94a195`.
Synthetic model accounting is fixture data, not billed provider usage. Independent
review of the recovery change found no blockers; 48 persistence/progress tests
also passed independently. Public autonomous quotation and AA-to-LCA UI acceptance
remain distinct checks.

## Bounded real FWD attempt and readonly action correction

The first actual `qwen-plus` / `dashscope/qwen-plus` run selected `fill` for the
readonly age combobox. Playwright never found an editable target and timed out;
the unknown-write fence correctly halted the run instead of duplicating it or
claiming a quote. Recorded usage was 10 calls / USD 0.040543. Because the audit
remained recovering rather than fully settled, the full attempt reservation
(30 calls / USD 0.50) is retained against the approved cap, not recycled.

Direct fill now checks the resolved element's actual `readOnly` property before
invocation and returns a known pre-write error suggesting `select`/inspection.
It never converts a timeout or post-dispatch outcome to safe failure, and it does
not force a site's control into an editable state. The real Chromium regression
failed before the change (30-second timeout, `write_started=true`) and passed
afterward with `write_started=false` and no click/open/write on the control.
