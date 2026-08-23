.PHONY: bootstrap check test contracts

bootstrap: ## install frozen Python and Node dependencies
	uv sync --frozen --all-groups
	corepack pnpm install --frozen-lockfile

check: ## lint, format-check, typecheck, architecture checks
	uv run --project apps/backend ruff check apps/backend/src apps/backend/tests scripts/export_contracts.py
	uv run --project apps/backend ruff format --check apps/backend/src apps/backend/tests scripts/export_contracts.py
	uv run --project apps/backend mypy apps/backend/src/tap scripts/export_contracts.py
	uv run --project apps/backend python scripts/export_contracts.py --check

test: ## unit, integration, and contract tests
	uv run --project apps/backend pytest apps/backend/tests -v

contracts: ## export OpenAPI/SSE schema and generate TypeScript
	uv run --project apps/backend python scripts/export_contracts.py
