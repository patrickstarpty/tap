# LCA integration into the TAP product

## Goal and acceptance

The user rejected a detached recorder/editor demonstration and requires usable TAP functionality. The existing real-browser recorder and SQL-backed replay are retained, but the normal Low Code Automation module must use them rather than localStorage simulations. Acceptance is the platform navigation → real recording → editable saved draft → independent replay → verified immutable publication → replay of the published version after draft changes.

## Implementation boundaries

- `apps/web` owns the real non-AI LCA module. `/`, `/?module=low-code`, and the compatible `/lca` entry render inside the TAP product rail and theme. Legacy migration/simulated runs remain only at `/prototype/legacy`; the design review prototype remains at `/prototype`.
- Tapper's rail links directly to the configured TAP Web address. TAP's rail returns to the configured Tapper address. Product source imports still respect the existing frontend boundary; there is no embedded prototype or copied AI implementation.
- Authorized project context and mutations continue through the same-origin gateway. Read, draft write, browser execution and publication each require their own current project authorization decision. A knowledge-answer grant is not an LCA grant. The browser and worker retain their independent credentials and policy enforcement.
- A published version references a verified immutable snapshot. Publishing is not execution. Later draft changes cannot rewrite it; replay uses its parameter schema and explicit confirmation, not current draft values. Versions remain project-and-owner scoped: this is not implicit team sharing.
- Internal navigation keeps draft and recording state mounted; leaving the page warns for unsaved or in-flight work. Ambiguous mutations retain their original idempotency keys. Backend outage shows an error, never simulated success.

## What this does not claim

No production deployment, enterprise SSO integration, team-sharing model, scheduled suite execution, Test Plan binding, general Robot Framework compatibility, or unrestricted browser automation is claimed. Six supported canonical operations and existing recorder restrictions remain. Product-mode authorization is a tested integration seam; the deployed identity provider must supply trusted scope and policy. These boundaries must remain visible in the delivery report rather than calling all LCA/AA work finished.
