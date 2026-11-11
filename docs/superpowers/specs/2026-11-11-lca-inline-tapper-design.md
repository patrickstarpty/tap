# LCA-inline Tapper Browser Automation

## Goal and scope

The formal TAP Low Code Automation page owns recording, deterministic editing,
saving, debug replay and publication. Its persistent Tapper side panel accepts a
natural-language goal and explicitly reviewed input values. Sending that intent
uses the existing automatic BDD planning → AA execution workflow. There is no
additional human BDD approval stage before execution.

This slice uses the existing loopback validation identity. It does not establish
enterprise SSO, team sharing, deployment readiness or unrestricted real-site
success. The synthetic product journey and paid-model/site acceptance are
separate evidence.

## Product and code boundaries

- TAP Web keeps `LcaWorkspace` and the assistant iframe mounted while the panel is
  hidden. Closing is not cancellation. Existing unsaved-LCA unload warnings stay;
  unsaved LCA edits are not claimed to autosave.
- The configured Tapper origin serves `/embed/lca` through its real `AppProviders`
  and `TapperWorkspace` variant. The existing composer, reviewed-input editor,
  conversation runtime, workflow details and BDD/action presentation are reused.
  Full-app navigation is hidden. Embedded execution is fixed to Browser; AA
  frozen-replay creation is hidden in this slice. LCA debug replay remains.
- There are no cross-app source imports. Both same-origin API proxies target the
  same authorized AA gateway. No CORS origins are widened.

## Trusted context and frame protocol

The parent and child independently resolve server context, then compare project,
actor, validation identity mode, gateway incarnation and context binding. These
non-secret bindings are not credentials. The browser-context endpoint permits
standalone AA recovery without depending on LCA permissions or configuration.

Messages use `lca-assistant-v1`, the exact configured sender origin, exact
`event.source`, a fresh per-mount nonce, strict shapes and bounded identifiers.
The parent retries the same `lca:init` nonce/context within a ten-second bound.
Only a matching child returns `lca:ready`; a repeated validated init can recover a
lost acknowledgement without remounting the workspace or broadcasting forever. Neither
query parameters nor messages authorize identity. The iframe is restricted to the
configured parent by `frame-ancestors`; sandbox permits scripts, same-origin,
restricted popups and evidence downloads, never top navigation or popup escape.

Ordinary parent rerenders preserve the channel. A real context change invalidates
it and reloads the same iframe element. Reconnect refreshes authoritative parent
context before a new handshake, preserving the LCA editor. Changed project or
actor blocks the workspace rather than silently switching an unsaved draft.

## Frozen intent and interrupted requests

Before browser admission, session recovery stores the bounded exact request,
selected conversation and idempotency key under the independently verified
context. It stores the user-supplied goal, notes and reviewed values temporarily; it does
not copy transport headers, browser cookies, gateway tokens or executable scripts.
This is not secret detection: passwords must not be entered in task text or
required-input values. An unknown receipt survives reload. Recovery resubmits that exact body/key only after
an explicit recovery action; mount, hide/show and source review never execute.
New browser work is blocked while an outcome is uncertain.

Only a first definite pre-admission rejection may release the intent. A later
rejection after an unknown POST cannot prove no previous task exists. A confirmed
receipt can be restored after gateway restart using the authorized read-only
conversation API. An unknown receipt cannot be automatically rebound across a
changed gateway incarnation; it remains blocked for task-history investigation.
Unavailable or corrupt session storage fails closed. Existing explicit cancel
commands remain requests until the backend confirms cancellation.

## Result review and LCA lifecycle

Only a successful LIVE AA task offers embedded **Review as LCA draft**. It sends a
reference containing task, execution, original bundle and input digest, never an
executable definition. Frozen AA replay, failed and unknown tasks are ineligible.

The host selects that reference without creating a task or importing a flow.
**Preview AA task** fetches the authoritative server projection and verifies all
four provenance fields. Unsupported projections report issues without executable
partial output. Task/project/reference changes abort and discard stale preview.
Dirty-editor, recording and publication locks remain effective.

**Use as unsaved draft → Save draft → Debug replay → Publish verified snapshot**
remain separate explicit actions. Current requirements are checked against the
original reviewed contract and fresh execution evidence; AA success alone never
certifies an edited LCA flow.

## Verification

Focused tests cover forged origin/window/nonce/context, duplicate and stale
messages, iframe/API disconnection, preserved hide/show state, reload recovery,
known rejection versus unknown receipt, review eligibility, unsupported sources
and import locks. The official `--lca-bridge` synthetic harness begins at the
formal LCA page, sends through the embedded composer, runs actual backend/browser,
reviews and imports without top-level navigation, edits/saves/replays, checks one
AA admission per intent and no model calls from import or deterministic replay.
Full-stack acceptance is recorded separately after the fixed final tree passes.
