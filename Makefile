.PHONY: bootstrap check test contracts milvus-preflight milvus-up milvus-down milvus-bootstrap milvus-health research-embeddings

bootstrap: ## install frozen Python and Node dependencies
	uv sync --frozen --all-groups
	corepack pnpm install --frozen-lockfile

check: ## lint, format-check, typecheck, architecture checks
	uv run --project apps/backend ruff check apps/backend/src apps/backend/tests scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py
	uv run --project apps/backend ruff format --check apps/backend/src apps/backend/tests scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py
	uv run --project apps/backend mypy apps/backend/src/tap scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py
	uv run --project apps/backend python scripts/export_contracts.py --check

test: ## unit, integration, and contract tests
	uv run --project apps/backend pytest apps/backend/tests -v

contracts: ## export OpenAPI/SSE schema and generate TypeScript
	uv run --project apps/backend python scripts/export_contracts.py

milvus-preflight: ## require Docker with at least 2 vCPU and 8 GiB memory
	@set -eu; \
	resources="$$(docker info --format '{{.NCPU}} {{.MemTotal}}')"; \
	cpus="$${resources%% *}"; \
	memory_bytes="$${resources#* }"; \
	case "$$cpus:$$memory_bytes" in *[!0-9:]*|:|*:) echo "Invalid Docker resource metadata." >&2; exit 1;; esac; \
	if [ "$$cpus" -lt 2 ] || [ "$$memory_bytes" -lt 8589934592 ]; then \
		echo "Milvus requires Docker with at least 2 vCPU and 8 GiB memory; found $$cpus vCPU and $$memory_bytes bytes." >&2; \
		exit 1; \
	fi; \
	echo "Milvus resource gate passed: $$cpus vCPU and $$memory_bytes bytes."

milvus-up: milvus-preflight ## start the fixed local Milvus profile
	docker compose --profile milvus up -d milvus

milvus-down: ## stop only Milvus profile containers and preserve named volumes
	docker compose --profile milvus stop milvus milvus-etcd milvus-minio
	docker compose --profile milvus rm -f milvus milvus-etcd milvus-minio

milvus-bootstrap: ## create local users/roles; initial root requires explicit opt-in
	uv run --project apps/backend python scripts/milvus_bootstrap.py

milvus-health: ## run the destructive-isolated three-role behavioral probe
	uv run --project apps/backend python scripts/milvus_health_probe.py

research-embeddings: ## run the explicitly authorized paid embedding research profile
	@if [ "$${TAP_RUN_PAID_EMBEDDING_RESEARCH:-}" != "1" ]; then \
		echo "research-embeddings requires TAP_RUN_PAID_EMBEDDING_RESEARCH=1" >&2; \
		exit 2; \
	fi
	uv run --project apps/backend python scripts/milvus_embedding_research.py
