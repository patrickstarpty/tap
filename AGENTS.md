# Repository Guidelines

## Working Contract

- Bound each request by its stated goal, scope, constraints and acceptance criteria. Do not invent adjacent work.
- Answer/explain/review/diagnose/plan: inspect and report only. Change/build/fix: make the smallest in-scope edits. Suggestions are not implementation approval.
- Do not refactor, rename, add dependencies or artifacts, or touch unrelated files unless required. Ask only when ambiguity materially affects the outcome; otherwise state the smallest reversible assumption.
- Validate narrowly first; expand only when risk or repository rules require it. Stop when acceptance criteria pass and skip optional cleanup.
- Lead with the conclusion; omit restatement, praise, routine narration, repetition and unsolicited next steps. Unless detail is requested, report only result, files, validation and blockers in at most six bullets.

## Project Structure & Module Organization

TAP contains a Python 3.13 FastAPI Backend (`apps/backend/`), React/TypeScript/Vite Web app (`apps/web/`), generated contracts (`contracts/`), runtime tooling (`scripts/`, `deploy/`) and documentation (`docs/`). The Athena local slice uses MySQL, Redis, Azurite, LiteLLM and Milvus; API, Relay and worker entrypoints remain separate. Place docs in `architecture/`, `proposals/`, `decisions/`, `plans/`, `reviews/` or `reference/`. Except for indexes and templates, use `YYYY-MM-DD-<lower-kebab-case>.md` filenames.

## Documentation Governance

Before materially changing `docs/`, read `docs/reference/2026-08-22-document-governance.md`. Use `docs/proposals/rfc-template.md` and `docs/decisions/adr-template.md` for RFCs and ADRs.

## Build, Test, and Development Commands

Run commands from the repository root:

```sh
make bootstrap     # install frozen uv and pnpm dependencies
make contracts     # regenerate OpenAPI/SSE and Web client/types
make check         # lint, format, type, architecture, contract and build checks
make test          # Backend pytest plus Web Vitest
make demo-up       # start loopback middleware and owned resources
make demo-check    # run five redacted dependency checks
make demo-dev      # loopback API, Relay, worker and Web
make demo-e2e      # isolated deterministic browser/persistence journey
make demo-down     # stop the project and preserve volumes
git diff --check
git diff -- README.md docs/ AGENTS.md
```

`demo-reset` is destructive and allowed only for `tap-athena-demo` with `TAP_ALLOW_ATHENA_VOLUME_RESET=1`; `demo-down/up` must preserve document, ingestion and index data. Keep isolated E2E out of `make test`. Preview changed Markdown and Mermaid.

## Coding Style & Naming Conventions

Follow existing conventions. Markdown uses UTF-8, ATX headings, relative links and tagged fences; preserve table and Mermaid styles. Retain canonical terms: `Athena`, `Test IR`, `Knowledge Chat`, `Azure AI Search`. Python follows Ruff and type-safe async boundaries; Web follows repository ESLint/TypeScript/Vitest and generated API types. Use lower snake_case domain IDs where specified. Architecture changes must synchronize applicable README, baseline, contracts, decisions, roadmap and source notes while separating local behavior, target state, proposals and open inputs.

## Testing Guidelines

Backend tests are in `apps/backend/tests/{unit,contract,integration,smoke}/`; Web tests live beside features and in `apps/web/tests/e2e/`. Use TDD for behavior changes: run the narrow test, then `make check` and `make test` in proportion to risk. `make demo-e2e` owns isolated middleware and must not mutate shared/default services. Real-model smoke requires `TAP_RUN_ATHENA_REAL_MODEL_SMOKE=1`; otherwise expect one skip. Review docs, links, lifecycle metadata and Phase boundaries. Never present local `doc` Milvus as enterprise Azure four-index completion or fake E2E as real-model validation.

Athena Demo is loopback-only, unauthenticated and supports text-extractable PDF/DOCX/MD/TXT without OCR. The rendered answer is page-local and not restored as history. Do not widen binds or make LAN/production claims without a separate security design.

## Commit & Pull Request Guidelines

Use short, lowercase, imperative Conventional Commit subjects. Pull requests should explain intent, list affected docs, link relevant issues or discussions, and identify decisions or open questions. Add screenshots only for material UI or rendered-diagram changes. Never commit credentials, tenant data or local `.env` files; use sanitized examples and update `.env.example` for new configuration.
