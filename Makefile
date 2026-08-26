TAP_MILVUS_COMPOSE_PROJECT ?= tap-milvus-local-experiment
export TAP_MILVUS_COMPOSE_PROJECT

.PHONY: bootstrap check test contracts milvus-preflight milvus-up milvus-down milvus-bootstrap milvus-health research-embeddings test-milvus test-milvus-rebuild-empty

bootstrap: ## install frozen Python and Node dependencies
	uv sync --frozen --all-groups
	corepack pnpm install --frozen-lockfile

check: ## lint, format-check, typecheck, architecture checks
	uv run --project apps/backend ruff check apps/backend/src apps/backend/tests scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py
	uv run --project apps/backend ruff format --check apps/backend/src apps/backend/tests scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py
	uv run --project apps/backend mypy apps/backend/src/tap scripts/export_contracts.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py
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
	docker compose -p "$(TAP_MILVUS_COMPOSE_PROJECT)" --profile milvus up -d milvus

milvus-down: ## stop only Milvus profile containers and preserve named volumes
	docker compose -p "$(TAP_MILVUS_COMPOSE_PROJECT)" --profile milvus stop milvus milvus-etcd milvus-minio
	docker compose -p "$(TAP_MILVUS_COMPOSE_PROJECT)" --profile milvus rm -f milvus milvus-etcd milvus-minio

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

test-milvus: ## run the non-skippable real Milvus correctness gate with committed vectors
	$(MAKE) milvus-up TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	$(MAKE) milvus-bootstrap TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	$(MAKE) milvus-health TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	uv run --project apps/backend python scripts/milvus_fixture.py publish --fixture apps/backend/tests/fixtures/milvus/doc-fixture-v1.json --queries apps/backend/tests/fixtures/milvus/query-cases-v1.json --vectors apps/backend/tests/fixtures/milvus/vectors-research-embedding-v1.json
	MILVUS_URI="$${MILVUS_URI:-http://127.0.0.1:19530}" \
	MILVUS_DATABASE="$${MILVUS_DATABASE:-default}" \
	MILVUS_READER_USERNAME="$${MILVUS_READER_USERNAME:-tap_reader}" \
	MILVUS_READER_PASSWORD="$${MILVUS_READER_PASSWORD:-tap-local-Reader1!}" \
	MILVUS_WRITER_USERNAME="$${MILVUS_WRITER_USERNAME:-tap_writer}" \
	MILVUS_WRITER_PASSWORD="$${MILVUS_WRITER_PASSWORD:-tap-local-Writer1!}" \
	MILVUS_PROVISIONER_USERNAME="$${MILVUS_PROVISIONER_USERNAME:-tap_provisioner}" \
	MILVUS_PROVISIONER_PASSWORD="$${MILVUS_PROVISIONER_PASSWORD:-tap-local-Provisioner1!}" \
	TAP_RUN_MILVUS_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_milvus_search_acl.py apps/backend/tests/integration/test_milvus_rebuild_alias.py -v

test-milvus-rebuild-empty: ## delete only the opted-in project volumes and rebuild from fixtures
	@set -eu; \
	if [ "$${TAP_ALLOW_MILVUS_VOLUME_RESET:-0}" != "1" ]; then \
		echo "test-milvus-rebuild-empty requires TAP_ALLOW_MILVUS_VOLUME_RESET=1" >&2; \
		exit 2; \
	fi; \
	printf '%s' "$(TAP_MILVUS_COMPOSE_PROJECT)" | rg -q '^[a-z0-9][a-z0-9_-]{2,62}$$' || { \
		echo "Refusing volume reset for an invalid Compose project." >&2; \
		exit 2; \
	}
	docker compose -p "$(TAP_MILVUS_COMPOSE_PROJECT)" --profile milvus down -v --remove-orphans
	$(MAKE) test-milvus TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
