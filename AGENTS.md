# Repository Guidelines

## Project Structure & Module Organization

TAP contains a Python 3.13 FastAPI Backend in `apps/backend/`, a React/TypeScript/Vite Web application in `apps/web/`, generated cross-language contracts in `contracts/`, local runtime tooling in `scripts/` and `deploy/`, and architecture/governance documentation in `docs/`. The Athena local slice uses MySQL, Redis, Azurite, LiteLLM and Milvus from `compose.yaml`; Backend entrypoints keep API, Relay and ingestion-worker roles separate. The documentation tree uses `architecture/`, `proposals/`, `decisions/`, `plans/`, `reviews/` and `reference/` responsibility directories. Except for indexes and templates, documentation filenames use `YYYY-MM-DD-<lower-kebab-case>.md`.

## Documentation Governance

Before creating, moving, or materially changing content under `docs/`, read `docs/reference/2026-08-22-document-governance.md`. Use `docs/proposals/rfc-template.md` and `docs/decisions/adr-template.md` before creating or materially changing RFCs and ADRs, respectively.

## Build, Test, and Development Commands

Run reproducible commands from the repository root:

```sh
make bootstrap     # frozen uv and pnpm installation
make contracts     # regenerate OpenAPI/SSE and Web client/types
make check         # Python/Web lint, format, type, architecture, contract and build checks
make test          # Backend pytest plus Web Vitest
make demo-up       # durable loopback middleware and owned-resource initialization
make demo-check    # five redacted dependency checks
make demo-dev      # loopback API, Relay, worker and Web
make demo-e2e      # isolated deterministic browser/persistence journey
make demo-down     # stop the selected project and preserve volumes
git diff --check
git diff -- README.md docs/ AGENTS.md
```

`demo-reset` is destructive and is allowed only for exact project `tap-athena-demo` with `TAP_ALLOW_ATHENA_VOLUME_RESET=1`; ordinary `demo-down/up` must preserve document, ingestion and index data. The current rendered answer is page-local state and is not restored as history. Do not export the isolated E2E project into ordinary `make test`. Preview changed Markdown and Mermaid diagrams in a compatible renderer.

## Coding Style & Naming Conventions

Write UTF-8 Markdown with ATX headings, relative repository links, and language-tagged fenced blocks. Preserve existing table and Mermaid styles. Match the file's language and retain canonical casing such as `Athena`, `Test IR`, `Knowledge Chat` and `Azure AI Search`. Python uses Ruff formatting/type-safe async boundaries; Web uses the repository ESLint/TypeScript/Vitest conventions and generated API types rather than duplicate DTOs. Domain IDs use lower snake_case where specified. When architecture changes, synchronize README, baseline, contracts, decisions, roadmap and source notes as applicable, while distinguishing implemented local behavior, enterprise target state, proposals and unresolved inputs.

## Testing Guidelines

Backend tests are under `apps/backend/tests/{unit,contract,integration,smoke}/`; Web component tests live beside feature code and browser acceptance is under `apps/web/tests/e2e/`. Use TDD for behavior changes, run the narrow test first, then `make check` and `make test` in proportion to risk. `make demo-e2e` owns exact isolated local middleware and must not mutate shared/default services. The real-model smoke is opt-in only; without `TAP_RUN_ATHENA_REAL_MODEL_SMOKE=1` it must produce one intentional skip. Review rendered docs, links, lifecycle metadata and Phase boundaries. Do not describe Athena's local `doc` Milvus projection as the enterprise Azure four-index completion or deterministic fake E2E as real-model validation.

The current Athena Demo is loopback-only, has no authentication and supports text-extractable PDF/DOCX/MD/TXT without OCR. Those constraints are safe only for local development; do not widen binds or add LAN/production claims without a separate security design.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style subjects such as `docs: add platform architecture review`. Keep subjects lowercase, imperative, and narrowly scoped. Pull requests should explain architectural intent, list affected documents, link relevant issues or source discussions, and identify any new decision or open question. Include screenshots only when UI specifications or rendered diagrams materially change. Never commit credentials, tenant data, or local `.env` files; use sanitized examples and update `.env.example` if configuration is introduced.
