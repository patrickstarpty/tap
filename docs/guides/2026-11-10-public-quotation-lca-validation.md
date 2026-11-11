# Public quotation LCA validation — 2026-11-10

## Later evidence

This is the 2026-11-10 checkpoint, not the final status. The [2026-11-11 lessons learned](2026-11-11-aa-lca-lessons-learned.md) records AIA positive recording/replay and FWD editor-assisted replay. FWD live dropdown recording remains unresolved; the earlier AIA Code 903 cause is still unproven.

## Result

This acceptance is **not fully passed**. No application, payment, contact details, real personal data or paid model calls were submitted. Original worktrees remain unchanged; nothing was committed, merged or pushed.

### AIA: complete negative-path LCA validation

Official Cancer Care Essence quote entry: https://www.aia.com.hk/en/products/health/cancer-care-essence/buy-online/checkout-beginning

Synthetic inputs: DOB 08-11-1996, male, nonsmoker, HK$500,000 cover, no promotion code. The actual LCA UI recorded nine actions, imported them, edited assertions, saved version 4 and performed a fresh independent zero-model replay. Nine actions and URL/title assertions passed; the positive-premium assertion correctly failed (11 passed, 1 failed).

The real API returned HTTP 200 with business Code 903, “Validation for SOAP response fields fail”, identifying missing LIFE, INITIAL_PREMIUM and TOTPREM fields. Monthly Premium remained blank. This is an observed failed quotation, not an inferred price or successful quotation. Thirteen owned selector cases validate the positive-numeric condition, including rejection of whitespace, placeholders, zero and negative values. The editor still uses exact text/URL assertions; numeric matching is expressed through a tested Playwright selector, not a native numeric assertion feature.

Final run: fc11463cb3884d1691beb0fa8a8556be. Definition digest: eb190efb62db82d0b6b1ba7cf6b839936509fa7c997e8508d8e12662b4b2125b. User-safe evidence is retained in candidate `.local/aia-lca-acceptance-redraw-20261110/delivery`. Raw traces/databases are separate and must not be shared without reviewing temporary site-session headers.

### FWD: blocked before requested scenario

Official page: https://www.fwd.com.hk/online-insurance/term-life/en/

Requested synthetic scenario: age 30, male, nonsmoker, ten-year renewal, HK$2 million cover. The public page and observed official ecapi.fwd.com.hk dependency load through the validated proxy route. A visible default-age-25 quotation proves connectivity only and is not acceptance of the requested scenario.

The final reviewed build rejected the first readonly age-input click with `frame_revision_changed`, despite an explicit fresh frame. The narrow target proof did not apply and correctly fell back to strict whole-page validation. No age-30 input or independent quotation replay completed. The session was canceled and owned processes stopped; unknown actions were not retried.

One bounded offline inspection found the saved ARIA structure within proof limits. Running decorative animations are a plausible reason for conservative proof exclusion, but retained artifacts lack runtime animation/frame/font facts. The exact exclusion remains unproven. No further live attempts, guard relaxation or speculative repair were made. Fixed value-free eligibility diagnostics would be the next useful investigation.

## Code and verification boundary

Implemented proxy-compatible public-IP validation, bounded pending-frame reads, readonly click/focus, retained-target scrolling, safe native-select redraw acknowledgement, repeated-recording teardown, redacted diagnostics and narrowly reviewed selector proofs. These do not establish universal site compatibility. Coordinate/unsupported target/context cases retain strict fallback.

- Python make-check equivalent recipes: all 20 passed; backend 458 passed, 47 explicit skips.
- Final full AI backend: 6,053 passed, 610 explicit skips, zero failures; 18 existing Alembic warnings.
- Web: 304 passed.
- Runtime aggregate before final bounded-cleanup correction: 243 passed; final affected suite: 81 passed. These are separate gates, not one combined count.
- Architecture repair targeted checks: 116 passed, 1 skipped; generated contracts 11 passed; mypy and Ruff clean.

The final target-proof implementation was reviewed independently. Its real FWD acceptance remains failed as stated above. AIA acceptance preceded the later diagnostic/target-proof patches; launch and packaging hashes are retained separately. No claim is made that AIA was rerun on the final target-proof tree.
