# Repository Guidelines

## Project Structure & Module Organization

TAP is currently a documentation-only architecture repository; implementation has not started. `README.md` provides the project overview and status, while `docs/index.md` is the documentation entry point. The documentation tree uses six responsibility directories: `docs/architecture/` for the current baseline and domain designs, `docs/proposals/` for RFCs and unresolved inputs, `docs/decisions/` for ADRs, `docs/plans/` for roadmaps and implementation plans, `docs/reviews/` for time-point assessments, and `docs/reference/` for contracts, sources, and governance. Except for indexes and reusable templates, content filenames use `YYYY-MM-DD-<lower-kebab-case>.md`. No source, test, or asset directories exist yet.

## Documentation Governance

Before creating, moving, or materially changing content under `docs/`, read `docs/reference/2026-08-22-document-governance.md`. Use `docs/proposals/rfc-template.md` and `docs/decisions/adr-template.md` before creating or materially changing RFCs and ADRs, respectively.

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
