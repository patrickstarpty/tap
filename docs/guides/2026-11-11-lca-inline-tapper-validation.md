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
