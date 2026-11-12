# Formal LCA-inline Tapper acceptance

## Result and scope

The official synthetic `--lca-bridge` journey passed on source commit
`bc95666906f0f0344d0404b0bf7f542eb3ca6346`, supervisor owner
`tap-browser-core-a63e3945389b`. The harness exited 0 after starting at the formal
TAP Low Code Automation page, submitting through the actual embedded Tapper
composer and running the real backend and local browser against an owned site.

The provider was **synthetic**, with no paid provider calls. The `qwen-plus` alias
in the fixture configuration is not evidence that Qwen autonomously completed
this task. This acceptance does not establish autonomous FWD/AIA quotation
success, enterprise SSO, team sharing or production deployment. It verifies
explicit import, save and current-contract replay; it does not add a publication
acceptance claim.

The portable, screened [acceptance summary](assets/2026-11-11-lca-inline-tapper/acceptance-summary.json)
contains source/result IDs, checks and the recording digest. Raw traces, temporary
session material and database exports are not committed.

## Verified product journey

1. Open the normal TAP LCA workspace. Enter the natural-language goal, Account
   `AC-42` and Amount `120` in the real iframe composer; review the exact required
   inputs and select **Send**. The existing workflow plans BDD steps and executes
   AA automatically, without a new pre-execution BDD approval screen.
2. Inspect the actual task's BDD/actions and successful result. Open its Screenshot
   link in a real popup. Hide/open the panel, then reconnect and recover the same
   conversation on `/embed/lca`. The top-level LCA URL stays unchanged.
3. Select **Review as LCA draft**, then the host's **Preview AA task** and
   **Use as unsaved draft**. Source projection is authoritative and import remains
   explicit; the result message does not create or run another task.
4. Add a `Then` exact-text assertion for `AC-42 / 120 / MATCH`, save the edited
   draft and explicitly start **Debug replay**. The original reviewed source
   contract remains attached. The replay checks current observed values and all
   canonical actions rather than reusing old AA success as proof.

The AA task was `a58aa4299c274baca78935c6f83a98d4`; its live browser execution was
`8bf4698c5b8945bfa0ae4dc6547b0a9c`. The saved LCA flow was
`291d9a9003dc4bee81ac3e9a417aa474`, frozen snapshot
`67d49128ec594dcb8a25f58e0e80021c`, debug run
`0ec721569e59498991d7a513a7098bfa`.

- Technical replay: passed; 11/11 canonical actions, eight BDD groups present, no
  canonical-result validation error.
- Current requirements: `account` and `amount` both passed. Both named result
  readbacks were `AC-42 / 120 / MATCH`.
- Admission: one conversation admission and exactly one linked AA task for the
  submitted intent, including hide/open/reconnect.
- Model counter: 11 before import and 11 after edited deterministic replay. These
  are synthetic fixture calls, not paid-provider usage.
- Recording: `tap-inline-lca-full-acceptance-bc956669.mp4`, 22.08 seconds; H.264,
  yuv420p, faststart. The untrimmed conversion passed full decoding and visual
  inspection. The summary records its SHA-256; delivery is separate from Git.

## Failures found only through the real iframe journey

### Initialization messages can precede React listeners

The first real journey had matching parent/child server context, matching runtime
identity and HTTP 200 responses, yet never reached readiness. A single iframe-load
`lca:init` could arrive before the child's React effect registered its listener.
The symmetric case also matters: the parent could miss the child's first ready
acknowledgement.

The host now retries the **same** nonce/context every 250 ms within the existing
ten-second deadline. The child reacknowledges only a repeated validated same-nonce
init. Verified readiness, error, unmount or authority changes stop host retries;
there is no permanent readiness broadcast or repeated workspace mount/execution.
Exact origin, `event.source`, schema, bounded IDs, nonce and independently resolved
context checks remain mandatory. Failure-first regressions cover the late
listener, forged readiness and bounded cleanup paths.

### A sandbox can block React's native form event

The next real journey completed the handshake and filled/reviewed inputs, but
**Send** produced no POST. The browser console explicitly reported that form
submission was blocked because `allow-forms` was absent. Component DOM tests had
not executed this browser-enforced behavior.

The correction added only `allow-forms` to the existing scripts/same-origin/
popups/downloads allowlist. Top navigation and popup sandbox escape remain
forbidden. Component and official-harness token assertions were updated before
the successful end-to-end rerun. Evidence links were also clicked, rather than
merely checked for visibility.

## Verification evidence

The implementation passed independent review after its reported issues were
fixed. The complete application `make test` passed: 539 TAP AI Python, 83 TAP AI
frontend, 70 TAP backend and nine Web App tests. Complete `make check` passed on an
authorized rerun, including 702 TAP backend tests with 90 skips. The first sandboxed
attempt could not listen on an owned Unix socket; the local-runtime file passed
22/22 after the required execution permission, then the full check passed.
Skipped tests are not counted as acceptance.

After the real-browser fixes, the affected host/application suite passed 40 tests,
the embedded Tapper suite passed six, both frontend checks passed, and the harness
passed actual TypeScript checking. Unchanged modules reused their completed gate
evidence. The final official full-stack run used the fixed commit named above;
this document-only follow-up does not change its source.

Unknown browser POST receipts remain recoverable only with the exact frozen
request/key and matching authorized context. An unknown receipt cannot be
silently cleared or rebound across a changed gateway incarnation. Session storage
contains a temporary copy of user task text and reviewed values, not transport
credentials; it is not secret detection, so passwords must not be entered there.

## 2026-11-12: bounded real FWD attempts (not accepted)

Both attempts used the formal LCA embedded composer, synthetic business inputs,
reviewed requirements, and the existing `qwen-max` → `dashscope/qwen-max` mapping.
Neither obtained a verified quote. No application, payment or AIA run was made.

| Attempt | Model calls | Input tokens | Output tokens | Verified provider cost (USD) | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| First, task `f8357f84246f43aa801d42c1a42d634c` | 5 | 46,234 | 1,458 | 0.0833056 | First navigation returned unknown; stopped |
| Second, task `1e6e3d6a93464c158de201c57fb8256b` | 25 | 315,173 | 5,067 | 0.5367056 | `budget_calls`; zero accepted browser plans/actions |

All 30 calls settled. This FWD grant's verified provider cost totals **$0.6200112**;
its 30-call allowance is exhausted even though its dollar ceiling was not reached.
AIA used **zero calls** from its separate grant. Conservative per-call rounded
budget charges total $0.620025, distinct from provider cost above. The cumulative
reconciled TAP API accounting across the recorded trials is **140 calls,
2,080,209 input + 25,030 output = 2,105,239 tokens, $2.42895160**. This is not an
accounting of all assistant credits or all models used in development.

The first launcher omitted the explicitly required local upstream proxy. The
original navigation remains **unknown**, not retroactively “not dispatched.”
Commit `02611388` requires an explicit reachable IPv4 loopback HTTP upstream
before real-inline resources/model work and passes it into the actual worker.
A separate zero-model controlled fixture and a separate read-only FWD navigation
with the existing proxy succeeded. Neither proves the precise cause of the old
unknown receipt nor counts as quotation acceptance.

The second run stopped after its remaining 25 calls without a valid browser plan
or action. Bounded read-only investigation of an isolated copy of its retained
owned database found 25 reservation events, 25 settlements, a session-start event
and a coding-result event. The public snapshot's 52 generic `activity` entries
are privacy projections of those records, **not 52 tool calls**. Tool transcripts
and validation messages were not persisted; the temporary OpenCode workspace had
already been removed. The specific model/tool error cannot be reconstructed from
retained evidence, so no selector/contract failure is claimed as the cause.

Zero-fee follow-ups `c450bafb` and `b1d78653` retain at most 32 allowlisted tool
status/category records without raw arguments, prompts, outputs or error text.
A budget-independent guard stops before a fifth model forward if four requests
have no accepted browser progress. Only the first accepted plan or an increased
successful-action count resets the window; repeated plan edits do not. Unknown
outcomes keep their existing priority. Diagnostics are optional and revalidated;
a malformed artifact cannot replace the authoritative `no_progress` stop.

Verification: 17 Node tests, 77 related Python tests (three skipped), then eight
final focused tests covering the guard, failed diagnostic collection, sanitization
and unknown priority; Ruff, MyPy and diff checks passed. A deterministic 25-turn
mailbox fixture forwarded only four times. These changes have **not** been
validated with another real-model FWD or AIA run. No further provider expenditure
was made for this fix; synthetic formal-LCA acceptance above remains a separate
claim.

### Zero-fee native OpenCode protocol matrix

A follow-up used the actual pinned **OpenCode 1.19.5 executable**, native
`tap_browser`, mailbox, BrowserProgress, real HTTP/SQLite BrowserService,
BrowserWorker and owned Chromium page. The model was an owned loopback scripted
SSE provider. It did not visit public sites or call a paid provider.

| Scripted response case | Synthetic requests | Tool calls | Accepted plans | Completed browser actions | Observed result |
| --- | ---: | ---: | ---: | ---: | --- |
| Normal plan | 6 | 4 | 1 | 3 | Clicked DOM read back `CLICKED` |
| JSON-looking plain text | 2 | 0 | 0 | 0 | Text is not tool admission |
| Invalid tool schema | 3 | 1 | 0 | 0 | `validation_error` |
| Completed tool with server rejection | 3 | 1 | 0 | 0 | `server_rejected`, not success |
| Rejected plan, then correction | 7 | 5 | 1 | 3 | Rejection retained; corrected DOM read `CLICKED` |

Actual transport completion can coexist with business rejection. The matrix
found the native missing-selector validation was initially classified too
broadly; a failure-first regression now assigns its fixed validation category.
Completed native-tool JSON errors are separately categorized without storing
raw output or error text. Generation guidance now uses the frozen contract's
attribute and expected readback instead of coercing checked/selected_label/text
to value. These fixes do not modify the frozen contract or target verification.

[Machine-readable safe evidence](assets/2026-11-12-opencode-protocol-matrix.json)
contains protocol counts and fixed diagnostics only. Normal/plain-text evidence
was collected before the final validation-classifier-only change; the other
three cases were rerun after it. All five passed. The runner deliberately uses a
test model-forward handler, **not CodingRuntime/BudgetGate**; it does not replace
the separate four-call guard runtime regression or validate paid autonomy.

Reproduce with the repository's installed dependencies and pinned binary:

```sh
PYTHONPATH=apps/tap-ai-backend/src:apps/backend/src .venv/bin/python \
  scripts/verify-opencode-protocol.py \
  --opencode-binary "$TAP_OPENCODE_BINARY" \
  --evidence /tmp/tap-opencode-protocol.json
```

Each owned scenario permits at most ten synthetic requests, with a 75-second
prompt-phase deadline and separately bounded container startup. No
Docker stack or external model key is needed. The final diagnostic unit suite
passed 19 Node tests; affected Python prompt/guard/unknown regressions and
Ruff/MyPy checks passed. FWD/AIA autonomous quote acceptance remains unproven.

### Separate official Playwright MCP tool comparison

A separate zero-model probe compared TAP's native tool with official
`@playwright/mcp@0.0.85` on the same owned calculator HTML and Google Chrome
`157.0.8103.51`. Both filled Units=3, clicked Calculate exactly once and observed
`Result: 21.00`, Clicks=1. TAP used Playwright 1.62.1; the official package used
1.66.0-alpha-2026-11-09. TAP mapped its test origin to the same loopback HTML;
origins/network paths were not identical. This is not a performance/token or
model-autonomy comparison.

The normal TAP path had five tool calls including the required plan and exact
DOM read assertion. Official MCP had four browser calls **plus two actual
snapshot-file reads**. Its small response byte count excludes those additional
reads and is not model token usage. Additional pre-plan TAP snapshot/navigation
attempts were both rejected with zero browser dispatch. This demonstrates an
extra TAP protocol prerequisite, not the root cause of the earlier 25-call
failure, whose tool transcript is unavailable.

This comparison used actual native tools and Chromium, but TAP observations were
wrapped by a test adapter rather than the full HTTP/API/database service. It did
not run OpenCode. One TAP navigation failed with `ERR_PROXY_CONNECTION_FAILED`;
a new owned session subsequently succeeded, without establishing the failure's
cause or general stability. Teardown stopped the session, but no EvidenceSink was
configured, so full screenshot/trace/video archiving and replay were **not**
validated. No FWD/AIA or autonomous-model result is claimed.

The bounded source report and scripts remain at
`/tmp/tap-browser-comparison-20261112/REPORT.md` with `compare.py`,
`tap-tool-driver.mjs`, `fixture.html` and `evidence.json`. Raw tool parameters and
response data were not copied into repository documentation. This independent
tool probe is also separate from the native OpenCode protocol matrix above,
which bypasses CodingRuntime/BudgetGate; the financial/no-progress protection
continues to have its own deterministic runtime regression, not a new paid test.

## 2026-11-12: observe first, compile after verified execution

Independent review approved fixed source `3edd7e86`. The formal LCA embedded
synthetic form journey then passed: task `baebdb4277a749a695af4665224d47fd`, edited
LCA run `6ffab9b975b744ae98955d723a5d2850`. One conversation admission, unchanged
top-level URL, 11/11 canonical actions, eight BDD groups, and both current
account/amount requirements passed. Import/replay model counts remained **10 → 10**.

The frozen entry was navigated once before planning. The server-injected Given
“Observe the authorized starting page (system discovery)” retained its real
navigation receipt in explicit review/import. The live ledger has 13 completed
operations including preflights and verification reads, exactly one navigation
and one click; finish is sequence 13 with **zero later dispatches**. Script
compilation performs no model/browser I/O. There was no automatic business rerun;
the user-facing test explicitly requested one edited LCA replay after saving.
The harness added a real exact-text assertion under Then, not just a timeout edit.

The boundary is deliberately narrow: pre-plan snapshot and one exact frozen-start
navigation only, with same-session/fresh-sequence/receipt checks. Writes and
business read proofs still need BDD; unknown has priority. A preserved system Given
uses one of the 24 plan slots and cannot dispatch again. Only the first trusted
observation earns a bootstrap progress reset; repeated snapshots/waits or the
same business read do not extend the four-turn no-progress window.

Verified live finish now precedes deterministic script compilation. Unresolved
session-local selectors, unsupported actions or selection without recorded
semantic guards fail closed: verified business outputs remain available, but no
replayable bundle/execution is claimed and no business operation is repeated.
This supersedes the earlier pre-plan rejection prerequisite, not the authorization,
contract, sensitive-input, unknown-outcome or budget protections.

[Formal safe evidence](assets/2026-11-12-lca-preplan-acceptance.json) and the
[seven-case native protocol matrix](assets/2026-11-12-opencode-preplan-matrix.json)
keep their scopes separate. The latter uses real OpenCode/native tools/HTTP/worker/
Chromium with an owned scripted provider, but bypasses CodingRuntime/BudgetGate.
Its pre-plan write and non-frozen URL cases had zero dispatch. The full product
run uses the normal workflow; the dedicated runtime regression separately proves
repeated fresh snapshots cannot buy a fifth unproductive provider forward.

The uncut UI recording `tap-preplan-lca-full-acceptance-3edd7e86.mp4` is 21.60 seconds,
H.264/yuv420p/faststart, fully decoded without errors. Actual frames at 3 and 12
seconds were inspected and show the embedded composer/execution, not a blank
iframe. The acceptance program reported passed and exported all evidence; its
outer interactive shell subsequently rejected assignment to zsh's read-only
`status` variable. This reporting-wrapper error did not rerun or invalidate the
completed acceptance.

Affected gates: 73 browser-progress Python tests, 32 native schema/tool Node tests,
35 compiler/BDD tests, 19 diagnostic Node tests, prompt/guard/artifact/fixture
regressions and Ruff/MyPy passed. One older expectation that absent handwritten
script must fail was updated to verified deterministic compilation; both success
and compiler-failure/no-repeat paths passed. No paid provider or external website
was used. This is not FWD/AIA autonomous quotation acceptance.

## 2026-11-12 AIA bounded real runs: not accepted

Both runs used the formal embedded LCA composer, actual Qwen Max through the
existing DashScope mapping, the same reviewed synthetic profile, and the explicit
existing upstream proxy. No application, payment or contact submission occurred.
Neither run produced a verified quotation. The original 30-call/$1 AIA grant was
not enlarged and FWD's exhausted allowance was not reused.

| Task | Result | Calls | Input / output tokens | Actual provider USD | Conservative budget micros |
| --- | --- | ---: | ---: | ---: | ---: |
| `9cea36c637844c86a0de602a53e03aea` | Entry click unknown; stopped | 6 | 73,357 / 1,751 | 0.1285776 | 128,581 |
| `92b38980526b47ee9eb1d0af6a56148b` | Input preflight failed; stopped | 6 | 65,326 / 1,502 | 0.1141344 | 114,137 |

All 12 reservations have verified settlements. AIA actual total is $0.2427120;
remaining authorization is 18 calls and $0.757282 after conservative deductions.
No automatic third attempt was made. Across the recorded TAP API experiments,
the updated cumulative total is 152 calls, 2,247,175 tokens and $2.67166360;
this is not a total of assistant credits.

The first run's generic entry-link selector failed cardinality resolution before
the controlled actuator was constructed, but the inspector lost that structural
fact. Its historical receipt remains unknown, never rewritten. Patch `9b08a03f`
now brands only actual zero/multiple target-count exceptions in a private WeakMap.
The inspector emits bounded fixed-category pre-actuation evidence; the worker
requires exact proof, matching error, strict false and matching original session
identity. It may classify the new receipt failed, never completed. Forged error
text/properties or post-actuator exceptions cannot acquire this proof.

In the second run the model navigated to the product introduction page and
attempted to fill a date-of-birth control that did not exist there. The only
following ledger action was preflight, failed with `target_missing` and
`write_started:false`. The actual fill was not admitted or dispatched. The API
returned 409, the workflow blocked, and the harness stopped the owned session.
This proves the narrow failure-evidence correction worked, not autonomous
navigation to the quotation form or successful recovery from that API error.

Affected validation: 80 controlled-browser tests, 85 durable-ledger tests and
71 AI browser-step/no-progress tests passed; Ruff/MyPy and independent review
passed. The new Chromium matrix executes production inspector dispatch/catch,
but uses in-memory socket transport, not a standalone Unix-socket integration.
No full repository gate was rerun for this narrow patch. Each real run has an
uncut H.264/yuv420p/faststart failure recording, fully decoded and honestly labeled.

### Strict preflight feedback and one remaining-budget real run

Patch `3cf3b161` retains only verifiable, same-session/epoch preflight failure as
an explicit `preflight_rejected` tool response. The actual input action is not
admitted and no business receipt is invented. A newer successful same-epoch
snapshot is required before another write. Generic 409, missing proof, mismatch,
timeout and unknown remain fatal. Cross-epoch human handback recovery is not
implemented by this narrow change.

Zero-cost validation: 144 focused client/progress regressions plus two transport
negatives, ten no-progress tests, Ruff/MyPy and independent review passed.
`scripts/verify-opencode-protocol.py --cases controlled_preflight` exercised real
OpenCode 1.19.5/native tool/mailbox/HTTP/SQLite/worker/controlled Chromium with an
owned synthetic provider: eight local model requests, six tools, zero fill rows
when preflight rejected, then fresh observation and exactly one corrected fill
plus successful value read. This harness bypasses CodingRuntime/BudgetGate; the
four-turn guard is separately tested with actual BrowserProgress state, not
claimed as financial integration from this fixture.

The authorized next real run used fixed `3cf3b161`, the formal LCA entry, the same
business-only reviewed profile and an 18-call/$0.757282 cap. Task
`94bc6b98c0894f3d870660ac3cad4b85` stopped after navigate completed → snapshot
completed → click unknown. The model chose the fresh observation's Buy Now
button reference, but the controlled actuator's hit test failed after scroll
preparation: `target_obscured_or_outside_viewport`. The receipt contains no
unwritten proof; its unknown state is preserved. The preflight recovery path
was not reached, and no verified quotation was obtained. No automatic retry or
manual rescue was performed; the owned session confirmed stopped.

Five reservations settled with verified usage: 57,756 input / 878 output tokens,
actual $0.0980288, conservative deduction 98,031 micros. Across the three AIA
runs: 17 calls, actual $0.3407408; remaining original allowance 13 calls/$0.659251,
with zero in-flight reservation. The updated recorded TAP API experiment total
is 157 calls, 2,305,809 tokens/$2.76969240, not all assistant credits. The uncut
failure video is labeled `aia-real-lca-obscured-click-unknown-20261112.mp4`.

### Zero-cost controlled click geometry diagnosis

The original unknown ledger has no screenshot/geometry evidence. A new isolated
read-only AIA context reproduced the same error mechanism without clicking or
entering data: nearest scrolling placed the unique Buy Now button at y752/h48,
whose center y776 was intercepted by a fixed bottom alert panel y711–800. Center
scrolling alone moved that same button to y376/h48; its center hit the button.
This does not retrospectively establish the original unknown action's outcome.

An identical owned HTML/viewport comparison showed production nearest scrolling
failed with zero clicks while standard Playwright `locator.click` without force
repositioned and clicked once. [Safe geometry evidence](assets/2026-11-12-controlled-click-geometry.json)
contains only bounds/roles and explicit limitations, not private page data.

Patch `17452cec` permits one bounded center-scroll fallback only for agent clicks
when nearest positioning fails hit testing. It retains the exact element and
rechecks identity, document, type, sensitivity and actionability before clicking;
it never forces, removes an overlay or resolves another target. Human screenshot
proof behavior is not given this fallback, and no unknown result is downgraded.
Eight failure-first geometry cases plus the existing controlled/human safety
matrix passed, 88/88 total, including full-overlay and target-mutation refusals.
No provider calls occurred in these diagnostics or fixtures.

### Bounded real AIA run after geometry fix: not accepted

Independent review found no blocker in fixed `f692e363`. With the original
remaining cap of 13 calls/$0.659251, formal embedded-LCA task
`0d5c8edcd4704a70a07d2256eaaf7f5f` navigated, completed two snapshots, then stopped
on an unknown click with the model-proposed selector `aria/Get in touch`.
The server reported only `controlled browser request rejected`; this was not
the Buy Now target and did not exercise the geometry recovery. No quotation,
input, contact submission, application or payment occurred.

A separate owned static-HTML diagnostic reproduced invalid selector syntax at
`locator.count` for the same string, before any click. The production error's
original unknown classification remains unchanged. This identifies another
model/tool-boundary problem, not successful autonomous task understanding; no
further paid run or unapproved selector workaround was performed.

All six provider reservations settled: 72,268 input / 712 output tokens, actual
$0.1201856, conservative deduction 120,189 micros. AIA cumulative usage is 23
calls/$0.4609264, leaving 7 calls/$0.539062 of the original grant and no in-flight
reservation. The recorded TAP API experiment total is 163 calls, 2,378,789 tokens
and $2.88987800, not all assistant credits. The owned session confirmed stopped.
The uncut failure recording is `aia-real-lca-selector-unknown-20261112.mp4`.

### Zero-cost selector feedback follow-up

[Safe forensic summary](assets/2026-11-12-selector-feedback-forensics.json) separates
retained evidence from reconstruction. Both stored 16,000-character snapshots
contained Get in touch and Buy Now. Re-evaluating the fixed compact modelReceipt
on those observations retained the early Get in touch text but omitted Buy Now;
its generic field had no context and was excluded from related_fields. The full
source-redacted evidence file retained both. This is an offline projection, not
a captured provider message. No full provider prompt/native file-read transcript
survives, so whether the model inspected that file and why it chose the wrong
entry remain unknown. The prompt/tool schema already explicitly prohibited
`aria/Label`; missing instruction is not established.

Patch `c242ee53` adds a narrow pre-execution rejection of the known unsupported
leading `aria/` DSL, with fixed `unsupported_selector_syntax` and current-ref/
returned-selector guidance. It does not rewrite the target, force a business
entry, implement a full CSS parser or classify arbitrary exceptions as unwritten.
Existing CSS/text/role/ref chains remain unchanged; unknown takes precedence.
86 BrowserProgress regressions, Ruff/MyPy and diff checks passed.

`scripts/verify-opencode-protocol.py --cases unsupported_selector` at `d0ff3cb2`
ran real OpenCode/native tool/mailbox/HTTP/SQLite/worker/owned Chromium against a
local synthetic provider. Nine synthetic responses/seven tools: unsupported
selector rejected with zero click rows, then a fresh snapshot's current ref
performed exactly one click and a successful `CLICKED` readback. This bypasses
CodingRuntime/BudgetGate and does not prove model autonomy or financial guard
integration. No external page or paid provider was used. The remaining seven
AIA calls/$0.539062 were not touched; recorded API costs are unchanged.

Independent review found the initial gate too late in the full CodingRuntime:
control handback observations and required-input refresh could run before
BrowserProgress handled the selector. Follow-up `6fe93858` shares one pure gate
and places it before those runtime pre-actions, with unknown still higher
priority. Failure-first actual CodingRuntime tests cover existing required reads
and control handback: zero browser/control/refresh calls for invalid syntax,
followed by valid observation/ref continuation. Runtime + BrowserProgress suite:
158 passed; Ruff/diff passed. MyPy reports two pre-existing runtime errors
(unused-ignore and no-any-return), also reproduced on the prior HEAD file under
the same environment; no new errors were introduced. A complete repository gate
was not rerun and these baseline type failures are not reported as green.

### Bounded neutral discovery catalog (zero-cost follow-up)

The producer now adds a separate privacy-checked discovery identity only for
already observed bounded fields. Complete structural CSS (including verified
frame boundaries) does not copy IDs, names or aria-label attributes; labels
exclude protected, invisible and editable content and unsafe external label
references. Password/hidden/sensitive controls fail closed. No values, options,
hrefs or business state enter this directory. Existing field order/context and
execution contracts remain unchanged; legacy observations without these new
identities are not expanded.

Each observation can advertise a private JSONL catalog in source order, never
ranked by site, goal or CTA. It contains at most 100 complete records, at most
1,024 UTF-8 bytes per line/100 KiB total, with four-line pages and explicit counts,
omissions, observation sequence and bounded-source coverage. This is not a full
DOM inventory, actionability guarantee, authorization or business assertion.
Selectors are never shortened; optional labels may be explicitly truncated.
Current target/scope/associated-state details retain priority; inline directory
entries use only remaining space. All model-view paths, including storage
failure and oversized facts, stay within 8,000 UTF-8 bytes. Exact omitted facts
remain in successfully written source-redacted evidence; failed storage is
explicit, never a raw-field fallback or a reason to repeat the action.

Final local gates: 37 Chromium producer/privacy/observation/scope cases and 43
native presentation tests passed. The two prior MyPy errors were fixed with
only generic return typing and removal of an unused ignore: MyPy/Ruff/format
passed, and 82 runtime/control regressions passed (three optional pinned tests
excluded). No full repository gate or production deployment is implied.

Real pinned OpenCode 1.19.5/native `read` used offsets 1, 5, 9 and 13, four records
each, to recover all 16 owned-fixture candidates in source order without gaps
or duplicates. The desired later control was absent from inline controls; its
selector came only from the native-read response, not a Python file-read bypass.
Actual HTTP/SQLite/worker/Chromium execution clicked once and verified `STARTED`.
The scripted provider used exactly ten local responses/eight tools within the
75-second bound. Reproduce using `scripts/verify-opencode-protocol.py` with its
required pinned `--opencode-binary`, `--evidence` and `--cases control_catalog`.
This is deterministic protocol/browser evidence, not model autonomy or
CodingRuntime/BudgetGate coverage. No real provider or external website was
called. AIA's remaining seven calls/$0.539062 are unchanged.

Final review identified that four separate file-read model turns followed by a
click would exceed the unchanged production no-progress window. The four-page
fixture remains [protocol coverage only](assets/2026-11-12-discovery-catalog-protocol.json),
not proof that production can finish that serial sequence. Directory guidance
now recommends targeted read/grep and explicitly states that file reads are not
browser progress; no guard or budget was relaxed.

The additional [bounded fixture](assets/2026-11-12-discovery-catalog-bounded.json),
`--cases control_catalog_bounded` at `944b98de`, used eight controls and two native
read pages (offsets 1 and 5). The actual unchanged CodingProgressGuard was checked
on every model turn: file reads left progress units 1→1→1, then the unique click
and verified result advanced 2→3. Eight synthetic responses/six tools completed
within the original limit, with `expected_result=STARTED` and `result_verified=true`.
This is the real guard class in the protocol handler, not full
CodingRuntime/BudgetGate. An older report flag compared every case with CLICKED;
reporting now compares each case's actual expected readback, with four regression
tests. Original evidence was retained rather than silently rewritten.

## Full production-chain catalog acceptance (2026-11-13)

The opt-in `--lca-catalog` formal LCA journey passed on `d4e63c1d` using real
OpenCode 1.19.5, the production CodingRuntime and BudgetGate, and an owned
synthetic provider/site. This closes the earlier protocol-driver integration gap,
not the separate real-model FWD/AIA acceptance gap. The screened
[full-chain evidence](assets/2026-11-13-discovery-catalog-full-chain.json) records
source hashes, task/run identities, budget reconciliation and canonical results.

- The same submitted intent produced one conversation admission. The LCA top-level
  URL stayed unchanged. Eight safe controls were advertised from observation 9;
  the desired submit control was absent inline. One provider response requested
  two actual native `read` calls, offsets 1/5, four records each. The click selector
  came only from the returned directory records, not a Python file-read bypass.
- The provider recorded one planning probe and ten coding requests. All **11**
  persisted model reservations had exactly matching settlements and matched
  `budgetUsed.model_calls=11`, below the unchanged cap of 12. The meter recorded
  1,100 synthetic cost micros; **actual paid-provider spend was zero**. This is a
  successful within-budget integration test, not a new budget-exhaustion negative.
- The AA ledger contained exactly one completed business click, two completed
  fills and no failed/unknown actions. Its session stop was confirmed. Native file
  reads did not create browser-action progress or relax the existing guard.
- Explicit preview/import, real editor assertion addition, save and deterministic
  replay passed. All 11 canonical actions passed; current `account` and `amount`
  contract checks passed. Model usage remained 11 before import and after replay.
  That explicitly requested LCA replay is separate from the AA business execution.

Two preparation failures are retained, not reclassified as success. The new
independent worktree initially lacked `deploy/browser`'s separately locked npm
package: inspector initialization failed before any model call. Installing that
lockfile fixed the environment. The next run stopped at controlled-fill UNKNOWN
with four synthetic calls because the optional fixture's long background placed
inputs outside the viewport. An owned production `createLiveControl` test proved
the old layout rejects before any completed write; the fixture was changed to keep
inputs visible while retaining the offscreen, second-page submit. Three Chromium
regressions now cover both layouts and actual controlled fills/click. Historical
UNKNOWN records were not retried or rewritten; successful acceptance used a new
owner. No product actionability or safety rules were weakened.

Reproduction in a clean, fixed worktree requires independent `uv sync --frozen`,
`pnpm install --frozen-lockfile`, and `npm ci --prefix deploy/browser`, plus verified
existing parser/object-store image receipts. Run:

```sh
PYTHONPATH=apps/tap-ai-backend/src:apps/backend/src .venv/bin/python \
  scripts/run-browser-core-e2e.py --mode synthetic --execution-mode local \
  --site owned --case form --lca-bridge --lca-catalog \
  --opencode-binary /path/to/pinned/opencode-1.19.5/opencode
```

The strict catalog flag rejects any other mode/site/case combination before
resource startup. The 30 focused Python regressions and three actual Chromium
fixture tests passed. No real provider, external website, new paid allowance or
real-model quotation claim is involved. The remaining AIA allowance was untouched.

## Real AIA run after catalog integration: failed, stopped (2026-11-12 UTC)

A separately approved, single AIA run on `d548068d` used the existing real
`qwen-max` → `dashscope/qwen-max` gateway, a new 30-call/USD 1 grant and the five
reviewed synthetic inputs. The old seven-call allowance was not combined with it.
The task `0d59312ada3e4234bc7deeb0f3e6cae2` **failed with `no_progress`**; it did
not verify inputs or produce a quote. No import/replay or further paid attempt was
performed. [Screened evidence](assets/2026-11-12-aia-catalog-real-no-progress.json)
records the precise result and accounting.

Seven unique model reservations exactly matched seven verified settlements:
85,950 input tokens, 1,326 output tokens, USD 0.1460064 actual cost. The conservative
budget debit was 146,010 micros. The unused new allowance is 23 calls/USD 0.853990,
not permission for an automatic retry. The old seven calls/USD 0.539062 remain
untouched. The owned browser stop was confirmed and the bounded forensic database
copy was stopped; original data was preserved.

Durable evidence shows snapshot → navigation → accepted BDD plan → snapshot →
no-progress stop. All three browser operations completed, with **zero click/fill
or other business writes**. The source observations contained 62 fields, including
50 safe discovery identities and Buy Now. This proves source discoverability,
not that the model read the catalog: the actual native-read usage and tool-error
categories cannot be recovered. The runtime has bounded terminal diagnostics, but
the persisted coding-result/attempt projection omitted them. Do not infer model
motive, claim a repeated missing-button cause, or call this quotation success.
The next justified engineering step is a zero-fee persistence regression for the
already-sanitized diagnostics, not another paid trial without evidence.

The uncut real LCA video is labelled **failed/no progress**, H.264/yuv420p with
faststart, 78.92 seconds, full-decode zero errors. Actual LCA and Tapper task frames
were inspected. Its digest and Library identity are in the screened evidence;
this is not a successful quotation demonstration.
