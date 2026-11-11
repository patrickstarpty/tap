# Integrated TAP LCA local startup

The normal `make tap-ai-dev` / `make demo-dev` supervisor can own TAP Web, the TAP API with LCA and browser routes, and the browser worker. No `/tmp` application or separate ad-hoc server is needed. This is loopback development, not enterprise SSO or a public deployment.

## Configuration

Use the existing supported Tapper middleware configuration. Add explicit server-only LCA settings to the private local environment; never put credentials in `VITE_*` or commit `.env`.

- `TAP_LCA_ENABLED=true` enables integrated LCA startup.
- `TAP_WEB_PORT=5174` must differ from the Tapper API/Web ports.
- `TAP_BROWSER_SERVICE_MODE=managed` starts/stops the owned API and worker. `external` requires an already supervised API with the same LCA routes and leaves its lifecycle alone.
- `TAP_BROWSER_API_ORIGIN=http://127.0.0.1:8002`; `TAP_LCA_BACKEND_URL` must match this local origin. The supervisor supplies this default only when the value is empty.
- `TAP_LCA_GATEWAY_TOKEN` is an explicit server credential, at least 16 characters, distinct from all browser credentials.
- `TAP_BROWSER_CONTROL_ENABLED=true`, `TAP_BROWSER_GATEWAY_TOKEN`, and `TAP_BROWSER_WORKER_TOKEN` enable actor-bound recording control. The latter two are separate credentials of at least 32 characters.
- Retain the configured `TAP_BROWSER_DATABASE_URL`, `TAP_BROWSER_OBJECT_ROOT`, `TAP_BROWSER_PROJECT_ID`, `TAP_BROWSER_ACCESS_TOKEN` and `TAP_BROWSER_TOKEN_EXPIRES_AT`. The project must match the trusted Tapper project.
- `TAP_BROWSER_EXECUTION_MODE=docker` uses the configured `TAP_BROWSER_IMAGE`. Explicit `local` development uses the repository's local browser runtime and needs its installed Playwright/Node dependencies. It does not provide Docker process isolation.
- An existing local upstream may be explicitly selected by `TAP_BROWSER_LOCAL_UPSTREAM_PROXY`; the launcher does not change system DNS, proxy, VPN or trust settings.

LCA does **not** require `TAP_BROWSER_CODING_ENABLED` or a model-based workflow. Recording and frozen-definition replay do not invoke a coding model. Existing AA settings remain independently configured.

## Migrations and launch

Apply the ordinary TAP Alembic migration to the selected **test** browser database through `0011_lca_releases` before enabling LCA. Use `TAP_DATABASE_URL` pointing to the same database as `TAP_BROWSER_DATABASE_URL`, then `make tap-backend-migrate`. Do not reset existing data. The launcher checks for the release table and refuses startup if the schema is absent; it does not silently migrate a user database.

Run `make tap-ai-dev` after the middleware is ready. The integrated supervisor supplies the TAP↔Tapper frontend navigation URLs, starts TAP Web on the selected port, and keeps the browser/LCA API and worker under the same lifecycle. The normal TAP Low Code Automation entry is `http://127.0.0.1:5174/?module=low-code` at the default port.

On a child failure or shutdown, only the supervisor's own child PIDs are stopped. External browser mode never starts or stops the externally owned service. No credentials are generated or replaced by this flow.

## Verification limits

Tests cover launch arguments, credential/environment allowlisting, separate local ports and origins, LCA without coding-model configuration, supervised child cleanup, standard TAP app persistence, and real-browser product flows. A real invocation of `scripts/run-tapper-dev.sh` also passed with isolated MySQL data and loopback ports, existing local middleware, and local browser execution: TAP Web returned the trusted context and project flow list through its same-origin proxy, then created and retrieved a durable synthetic draft. No custom application server was used. This smoke did not replay the synthetic draft or invoke a coding model.

Full `make tap-ai-dev` startup remains dependent on the configured middleware and installed browser runtime. Passing the isolated LCA flow does not prove enterprise authentication, public ingress or every middleware service is healthy.
