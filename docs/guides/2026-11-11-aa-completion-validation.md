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

The second bounded FWD run also failed, before quotation: the model emitted
`combobox [ref=e59]` rather than the documented `aria-ref=e59` locator. It matched
nothing, so the click timed out and remained unknown. Recorded usage was 7 calls /
USD 0.023654. Both attempt reservations now exhaust the approved FWD 60-call /
USD 1 allowance; no more FWD paid tests are run from this allowance.

The same direct pre-dispatch resolution now rejects zero matches as a known
failure and asks for a fresh snapshot or an explicit wait. This does not guess a
replacement selector, silently normalize arbitrary CSS, retry a dispatched
click, or weaken the unknown-outcome fence. Waiting is a separate supported
read-only action. A new real Chromium test reproduced the 30-second failure before
this change. Further live FWD success still requires separate acceptance.

## AIA and environment results

The first bounded real AIA run used the existing qwen-plus alias, reached the
iframe quote form, filled the synthetic sum insured, selected the gender/smoking
controls and opened the date picker. After changing its month, the model reused
an obsolete snapshot ref for a click. That inspector had started before the
zero-match precheck fix and correctly stopped as unknown after timeout. There
was no verified quote. Recorded usage: 17 calls / USD 0.103962; its full initial
30-call / USD 0.50 reservation remains retained.

The second AIA attempt failed in normal launcher/parser startup before any task
or model execution (`usage-manifest` was empty). Concurrent owned fixture startup
identified Docker VM `/tmp` ENOSPC, despite ample Mac host disk space. No paid
retry is issued until the environment is healthy. This unused attempt remains
within the originally approved AIA allowance; it is not an additional budget.

The broader repository `make test` and `make check` passed after the bridge and
assert_read implementation; browser socket tests required the authorized local
execution context rather than the restricted sandbox. The subsequent readonly
and zero-match inspector changes passed 16 real Chromium context/alias tests.

After Docker was expanded without deleting data, the same unused AIA reservation
was resumed. It reached the result iframe and read HKD 35.00 monthly, but did not
set the requested synthetic DOB 1996-11-08. Its attempted later proof instead
referenced the default 11-11-2008 and stale refs from the previous iframe. The task
ended failed with no published frozen bundle. A visible premium alone is not a
valid requested quote and is not reported as successful acceptance.

That final run recorded 23 calls / USD 0.180625. Across the four actual provider
runs the ledger records 57 calls / USD 0.348784. Earlier unresolved reservations
remain conservatively retained; no further paid run is started under this pass.
The startup-only failed attempt had an empty usage manifest and no model call.

This result separates three concerns: browser tools must support the operation;
the model must choose correct actions and preserve requested inputs; acceptance
must prove both inputs and output. Fixing locator recovery does not prove reliable
planning, and reading any positive premium does not prove the right profile.
The next evaluation should compare exact requested-input compliance, current-page
readback, final-result provenance and frozen replay, rather than headline model
names or a screenshot of a quote.

## Requested next model selection

The user subsequently requested Qwen Max for implementation. A metadata-only
lookup of the existing gateway confirmed alias `qwen-max` resolves to
`dashscope/qwen-max`, with catalog input limit 30,720 and listed rates USD 1.60/M
input, USD 6.40/M output. No pinned snapshot/version was returned, so this is not
identified as Qwen3.8 Max. No model completion was issued by this lookup.

The acceptance runner now binds planning and coding to the same explicitly chosen
alias and refuses a nondefault real model without declared input limit and both
positive finite cost rates. Its experiment manifest records those non-secret
settings. This prevents selecting Max while accidentally retaining Plus pricing
or planning. A future separately bounded approved run can specify:

- `TAP_BROWSER_CODING_MODEL_ALIAS=qwen-max`
- `TAP_BROWSER_CODING_MAX_INPUT_TOKENS=30720`
- `TAP_BROWSER_CODING_INPUT_PRICE_PER_MILLION=1.6`
- `TAP_BROWSER_CODING_OUTPUT_PRICE_PER_MILLION=6.4`

The current implementation/fixture work remains provider-free. Model preference
does not enlarge the approved spending limit or guarantee successful acceptance.

A later read-only reconciliation resolved the conservative reservation uncertainty:
all action reservations were settled (10/10, 7/7, 17/17, 23/23), all 57 settlements
had costs, all 53 coding proxy settlements had verified usage, the four planning
settlements identified `dashscope/qwen-plus`, and all four browser session ledgers
confirmed stopped/canceled. The earlier `recovering` workflow state did not mean
missing model billing. Using the ledger's conservatively rounded cost totals,
the original per-site allowance therefore has FWD 43 calls / USD 0.935803 and
AIA 20 calls / USD 0.715413 remaining. This reconciliation itself used no model;
no new paid run is started without an explicit bounded continuation decision.

## Combined AA and second-batch LCA gate

Integrated AA `94a331a6` with develop `164e4fb1`, preserving explicit page contexts, popup/dialog handling, project-scoped uploads/downloads, reviewed contracts, document-bound proofs, and privacy-safe model presentation. Duplicate cherry-pick conflicts were resolved using byte-identical logical bases `7f52fb84`/`9c32cdc9`, not by discarding either implementation.

The combined tree passed `make check` (including 702 backend tests, 90 environment-dependent skips) and `make test` (536 AA core, 83 AA UI, 70 browser/backend core, 9 web shell tests). Additional LCA frontend tests passed 122/122; browser actions/live control passed 87/87; the explicit browser contract target passed 79/79. Contract schemas were regenerated and checked.

A new eight-case real HTTP/Chromium integration matrix caught a genuine merge-boundary error: dialog arguments containing `type=confirm` could override the internal proof invalidation action type. The generator now preserves the internal action type while leaving dialog parameters unchanged. All eight cases pass, including actual second-page value 31 versus main-page value 30, seven new page/file/dialog operations invalidating old input proofs, and captured download bytes. After this fix, 91 related unit tests, the 19-case LCA document matrix, and Ruff/MyPy checks passed. Earlier page/file/read/guard browser tests passed 41/41.

These gates use owned synthetic fixtures without provider charges. They do not constitute successful autonomous FWD/AIA Qwen Max validation or a completed product recording. Those require a fixed combined revision and new retained evidence.

## Completed product edit-and-replay checkpoint

The fixed `e645962b` normal-stack, zero-provider-cost acceptance completed through the real TAP UI: review requirements, execute AA, preview and import the source-derived LCA draft, add an exact-result assertion under the Then step, save the edited definition, and replay it. Run `641a173004e24350ad50d9c114afd6de` has 11/11 canonical actions passed, eight BDD groups, and current account/amount contract requirements both passed. The added assertion was persisted and executed; the original contract was not rewritten to obtain success. A continuous 25.04-second H.264/yuv420p/faststart MP4 was retained and fully decoded for validation. This checkpoint is a synthetic product workflow, not public FWD/AIA autonomy or a demonstration of manual browser recording.

## Real Qwen Max outcomes and lessons

Three bounded Max attempts did not pass real-site acceptance. The first FWD attempt tried to fill a combobox wrapper rather than its actual input. The generic preflight now rejects unsupported fill targets before dispatch while preserving unknown outcomes after real writes. The next FWD attempt repeatedly guessed a nonexistent component-library CSS class. AIA repeatedly supplied invalid locator syntaxes, reached its date picker, and exhausted its call allowance without a quote. Neither site has a successful complete-profile autonomous acceptance result.

The failures exposed tool-contract defects that a larger model alone does not solve:

- Semantic wrapper labels do not make a node editable. Validate the actual supported fill target before dispatch; never silently redirect to a child input.
- A missing target with business guards previously entered `guardTarget` before the zero-match check and waited 30 seconds per attempt. Direct-write existence and uniqueness now precede the unchanged business and sensitive-field guards.
- Greedy model-receipt packing spent its related-field allowance on optional metadata and omitted the date picker's month/year selectors. Identity-first packing now prioritizes usable control identities, then optional detail, under the same bounded output ceiling. Omission and truncation remain explicit; this does not promise every reference fits.
- Native select options formerly stopped at 30 without completeness metadata. Total/truncated fields and scoped select observation now distinguish incomplete observation from an unavailable year. Owned Chromium tests select a year beyond option 30 and verify the final date after reference redraw.
- Returned selectors must satisfy the same 1000-character contract as calls. Longer selectors are explicitly omitted, never shortened or rewritten, while complete evidence is retained.
- Repeating an unchanged missing target under a different action type is not progress. A trusted known-no-write missing-target receipt now blocks that selector across click/fill/select/press. A newer successful exact scoped observation can restore it; unrelated timeouts and readonly-fill recovery do not trigger this lock. A second repeated rejection stops model forwarding and the execution loop as a clear failure, without repair or bundle creation. Cleanup uncertainty still retains the unknown fence.

These changes were implemented with synthetic regressions and independently reviewed. They are not evidence that the public workflows now succeed; a new real-model gate is still required.

## Metered API usage through the completed real experiments

All 110 provider calls are settled, all owned browser sessions stopped, and no API reservation remains active. Rates and usage below are the gateway's recorded API accounting, not a tax-inclusive provider invoice. Coding receipts retain verified token usage and configured aliases, but not each response's provider model identifier. Planner responses confirmed the corresponding `dashscope/qwen-plus` and `dashscope/qwen-max` mappings; no dated Qwen snapshot is claimed.

| Configured model | Calls | Input tokens | Output tokens | Gateway USD |
| --- | ---: | ---: | ---: | ---: |
| Qwen Plus | 57 | 846,956 | 8,313 | 0.34875800 |
| Qwen Max | 53 | 871,846 | 10,192 | 1.46018240 |
| Total | 110 | 1,718,802 | 18,505 | 1.80894040 |

The task budget ledger conservatively rounds each call upward to whole microdollars, totaling USD 1.808987. AIA reached its original 60-call site cap; FWD has only USD 0.053810 and ten calls left. No further complete-site paid attempt is planned under an infeasible remainder, and no budget is automatically enlarged. Development/review conversation token and dollar telemetry is unavailable and is not included or estimated here. Zero-provider-cost regression tests are separate from these billed API calls.
