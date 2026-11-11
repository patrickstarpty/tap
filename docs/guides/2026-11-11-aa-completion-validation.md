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
