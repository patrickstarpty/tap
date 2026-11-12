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
