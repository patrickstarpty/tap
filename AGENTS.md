# Repository Guidelines

## Project Structure & Module Organization

TAP is currently a documentation-only architecture repository; implementation has not started. `README.md` provides the project overview, status, and document index. Treat `docs/architecture.md` as the integrated baseline. `docs/contracts.md`, `docs/decisions.md`, and `docs/roadmap.md` define domain contracts, adopted decisions, and delivery phases. Topic-specific designs cover RAG, indexing, retrieval, the Knowledge Chat UI, and the controlled Codex runtime. Put new design documents in `docs/` using lower kebab-case names, such as `retrieval-tuning.md`. No source, test, or asset directories exist yet.

## Build, Test, and Development Commands

There is no package manifest, executable application, CI workflow, or repository-defined build/test command yet. For documentation changes, use:

```sh
rg --files README.md docs
git diff --check
git diff -- README.md docs/ AGENTS.md
```

These commands inventory the documentation, catch whitespace errors, and review the complete patch. Also preview changed Markdown and Mermaid diagrams in a compatible renderer. When implementation tooling is added, document its exact reproducible commands in both `README.md` and this guide.

## Coding Style & Naming Conventions

Write UTF-8 Markdown with ATX headings, relative repository links, and language-tagged fenced blocks. Preserve the existing table and Mermaid styles. Match the language of the file being edited (currently mainly Chinese) and retain canonical casing such as `Test IR`, `Knowledge Chat`, and `Azure AI Search`. Domain IDs use lower snake_case where specified, for example `test_checkout_happy_path`. When architecture changes, synchronize the README, baseline, contracts, decisions, roadmap, and source notes as applicable. Clearly distinguish confirmed decisions, proposals, and unresolved inputs.

## Testing Guidelines

No automated test framework or coverage threshold is configured. Review rendered headings, tables, diagrams, and links; then cross-check terminology, status metadata, and phase boundaries across affected documents. Do not present roadmap technologies such as React, FastAPI, or Playwright as already implemented. Future code changes should introduce tests and document their naming and execution conventions with the relevant tooling.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style subjects such as `docs: add platform architecture review`. Keep subjects lowercase, imperative, and narrowly scoped. Pull requests should explain architectural intent, list affected documents, link relevant issues or source discussions, and identify any new decision or open question. Include screenshots only when UI specifications or rendered diagrams materially change. Never commit credentials, tenant data, or local `.env` files; use sanitized examples and update `.env.example` if configuration is introduced.
