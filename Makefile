TAP_MILVUS_COMPOSE_PROJECT ?= tap-milvus-local-experiment
export TAP_MILVUS_COMPOSE_PROJECT
TAP_TAPPER_COMPOSE_PROJECT ?= tap-tapper-demo
export TAP_TAPPER_COMPOSE_PROJECT
override TAP_REPO_ROOT := $(realpath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: gate-v0 schema-drift migration-check bootstrap check brand-check test contracts tap-ai-bootstrap tap-ai-check tap-ai-test tap-ai-migrate tap-ai-dev tap-ai-api tap-ai-web tap-web-dev tap-backend-dev quality-kb quality-kb-real quality-graph quality-graph-real quality-test-design quality-test-design-real milvus-preflight milvus-up milvus-down milvus-bootstrap milvus-health research-embeddings test-milvus test-milvus-rebuild-empty demo-up demo-check demo-dev legacy-tapper-codex-dev demo-e2e demo-down demo-reset

bootstrap: ## install frozen Python and Node dependencies
	uv sync --frozen --all-groups
	corepack pnpm install --frozen-lockfile

check: ## lint, format-check, typecheck, architecture checks
	uv run --project apps/tap-ai-backend python scripts/check_backend_boundary.py
	uv run --project apps/tap-ai-backend ruff check scripts/check_backend_boundary.py
	uv run --project apps/tap-ai-backend ruff format --check scripts/check_backend_boundary.py
	uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/architecture/test_product_boundary.py -q
	uv run --project apps/backend pytest apps/backend/tests/test_app.py -q
	uv run --project apps/tap-ai-backend ruff check apps/tap-ai-backend/src apps/tap-ai-backend/tests scripts/export_contracts.py scripts/evaluate-quality-kb.py scripts/evaluate-quality-graph.py scripts/evaluate-quality-test-design.py scripts/generate-quality-graph-candidate.py scripts/generate-quality-test-design-profile.py scripts/run-quality-graph-candidate.py scripts/run-quality-test-design-candidate.py scripts/run-quality-kb-real.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py scripts/tapper_collection.py scripts/check-tapper-demo.py scripts/migration_support.py scripts/tapper_v0_gate.py scripts/check-schema-drift.py scripts/check-migration.py
	uv run --project apps/tap-ai-backend ruff format --check apps/tap-ai-backend/src apps/tap-ai-backend/tests scripts/export_contracts.py scripts/evaluate-quality-kb.py scripts/evaluate-quality-graph.py scripts/evaluate-quality-test-design.py scripts/generate-quality-graph-candidate.py scripts/generate-quality-test-design-profile.py scripts/run-quality-graph-candidate.py scripts/run-quality-test-design-candidate.py scripts/run-quality-kb-real.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py scripts/tapper_collection.py scripts/check-tapper-demo.py scripts/migration_support.py scripts/tapper_v0_gate.py scripts/check-schema-drift.py scripts/check-migration.py
	uv run --project apps/tap-ai-backend mypy apps/tap-ai-backend/src/tap scripts/export_contracts.py scripts/evaluate-quality-kb.py scripts/evaluate-quality-graph.py scripts/evaluate-quality-test-design.py scripts/generate-quality-graph-candidate.py scripts/generate-quality-test-design-profile.py scripts/run-quality-graph-candidate.py scripts/run-quality-test-design-candidate.py scripts/run-quality-kb-real.py scripts/milvus_bootstrap.py scripts/milvus_health_probe.py scripts/milvus_embedding_research.py scripts/milvus_fixture.py scripts/tapper_collection.py scripts/check-tapper-demo.py scripts/migration_support.py scripts/check-schema-drift.py scripts/check-migration.py
	uv run --project apps/tap-ai-backend mypy --explicit-package-bases --follow-imports=silent scripts/tapper_v0_gate.py
	bash -n scripts/run-tapper-v0-gate.sh scripts/run-tapper-dev.sh scripts/run-tapper-e2e.sh scripts/build-tapper-object-store.sh
	uv run --project apps/tap-ai-backend python scripts/export_contracts.py --check
	corepack pnpm --filter @tap/ai-frontend run contracts:check
	corepack pnpm --filter @tap/ai-frontend run check
	uv run --project apps/backend ruff check apps/backend/src apps/backend/tests
	uv run --project apps/backend ruff format --check apps/backend/src apps/backend/tests
	corepack pnpm --filter @tap/web run check
	$(MAKE) brand-check

brand-check:
	uv run --project apps/tap-ai-backend python scripts/check_brand_namespace.py

test: ## unit, integration, and contract tests
	uv run --project apps/tap-ai-backend python scripts/check_backend_boundary.py
	uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests -v
	corepack pnpm --filter @tap/ai-frontend test -- --run
	uv run --project apps/backend pytest apps/backend/tests -q
	corepack pnpm --filter @tap/web run test

contracts: ## export OpenAPI/SSE schema and generate TypeScript
	uv run --project apps/tap-ai-backend python scripts/export_contracts.py
	corepack pnpm --filter @tap/ai-frontend run contracts

tap-ai-bootstrap: ## install only Tap AI frozen dependencies
	uv sync --package tap-ai-backend --frozen --all-groups
	corepack pnpm --filter @tap/ai-frontend install --frozen-lockfile

tap-ai-check: ## verify Tap AI contracts, backend and frontend
	uv run --project apps/tap-ai-backend python scripts/check_backend_boundary.py --product ai
	uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/architecture/test_product_boundary.py -q
	uv run --project apps/tap-ai-backend python scripts/export_contracts.py --check
	corepack pnpm --filter @tap/ai-frontend run contracts:check
	uv run --project apps/tap-ai-backend ruff check apps/tap-ai-backend/src apps/tap-ai-backend/tests
	corepack pnpm --filter @tap/ai-frontend run check

tap-ai-test: ## run Tap AI backend and frontend suites
	uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests -q
	corepack pnpm --filter @tap/ai-frontend exec vitest run

tap-ai-migrate: ## migrate configured Tap AI MySQL without resetting data
	@set -eu; if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	uv run --project apps/tap-ai-backend alembic -c apps/tap-ai-backend/alembic.ini upgrade head

tap-ai-dev: ## run only Tap AI app processes against configured loopback services
	bash scripts/run-tapper-dev.sh

tap-ai-api: ## run Tap AI API on the configured loopback address
	@set -eu; if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	uv run --project apps/tap-ai-backend python -m tap.entrypoints.tapper_api

tap-ai-web: ## run Tap AI frontend on the configured loopback address
	@set -eu; if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	corepack pnpm --filter @tap/ai-frontend run dev

tap-web-dev: ## run TAP non-AI frontend separately
	corepack pnpm --filter @tap/web run dev

tap-backend-dev: ## run TAP non-AI backend separately
	uv run --project apps/backend uvicorn tap_platform.app:app --host 127.0.0.1 --port 8001

TAP_QUALITY_KB_PROFILE ?= apps/tap-ai-backend/tests/fixtures/quality/kb/profile-v1.json
TAP_QUALITY_KB_REPORT ?= .local/quality-kb/report.json
TAP_QUALITY_KB_OBSERVATIONS ?= .local/quality-kb/observations.json
TAP_QUALITY_KB_PROVIDER_CALL_BUDGET ?= 500
TAP_QUALITY_KB_MAX_RETRIES_PER_CASE ?= 2

quality-kb: ## deterministically evaluate the committed QUALITY-KB-01 profile offline
	uv run --project apps/tap-ai-backend python scripts/evaluate-quality-kb.py "$(TAP_QUALITY_KB_PROFILE)" --observations "$(TAP_QUALITY_KB_OBSERVATIONS)" --report "$(TAP_QUALITY_KB_REPORT)" --provider-call-budget "$(TAP_QUALITY_KB_PROVIDER_CALL_BUDGET)" --max-retries-per-case "$(TAP_QUALITY_KB_MAX_RETRIES_PER_CASE)"

quality-kb-real: ## require an opted-in real, human-labeled QUALITY-KB-01 run
	@if [ "$${TAP_RUN_QUALITY_KB_01:-}" != "1" ]; then \
		echo "quality-kb-real requires TAP_RUN_QUALITY_KB_01=1" >&2; \
		exit 2; \
	fi
	@if [ -z "$${TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST:-}" ] || [ -z "$${TAP_QUALITY_KB_MODEL_APPROVAL_ARTIFACT:-}" ]; then \
		echo "quality-kb-real requires an explicit approved actual provider/model/operation mapping and approval artifact" >&2; \
		exit 2; \
	fi
	@mkdir -p .local/quality-kb
	uv run --project apps/tap-ai-backend python scripts/run-quality-kb-real.py "$(TAP_QUALITY_KB_PROFILE)" --observations "$(TAP_QUALITY_KB_OBSERVATIONS)" --report "$(TAP_QUALITY_KB_REPORT)"

TAP_QUALITY_GRAPH_PROFILE ?= apps/tap-ai-backend/tests/fixtures/quality/graph/profile-v1.json
TAP_QUALITY_GRAPH_OBSERVATIONS ?= .local/quality-graph/observations.json
TAP_QUALITY_GRAPH_REPORT ?= .local/quality-graph/report.json

quality-graph: ## deterministically evaluate the committed QUALITY-GRAPH-01 candidate profile
	uv run --project apps/tap-ai-backend python scripts/evaluate-quality-graph.py "$(TAP_QUALITY_GRAPH_PROFILE)" --report "$(TAP_QUALITY_GRAPH_REPORT)"

quality-graph-real: ## require opted-in real model, Graph stores, and approved human labels
	@if [ "$${TAP_RUN_QUALITY_GRAPH_01:-}" != "1" ]; then \
		echo "quality-graph-real requires TAP_RUN_QUALITY_GRAPH_01=1" >&2; \
		exit 2; \
	fi
	@if [ "$${TAPPER_GRAPH_EXTRACTION_MODE:-}" != "model" ]; then \
		echo "quality-graph-real requires TAPPER_GRAPH_EXTRACTION_MODE=model" >&2; \
		exit 2; \
	fi
	@if [ -z "$${TAP_DATABASE_URL:-}" ] || [ -z "$${MILVUS_URI:-}" ]; then \
		echo "quality-graph-real requires explicit MySQL and Milvus endpoints" >&2; \
		exit 2; \
	fi
	uv run --project apps/tap-ai-backend python scripts/run-quality-graph-candidate.py "$(TAP_QUALITY_GRAPH_PROFILE)" --observations "$(TAP_QUALITY_GRAPH_OBSERVATIONS)" --require-approved-review
	uv run --project apps/tap-ai-backend python scripts/evaluate-quality-graph.py "$(TAP_QUALITY_GRAPH_OBSERVATIONS)" --report "$(TAP_QUALITY_GRAPH_REPORT)" --real

TAP_QUALITY_TEST_PROFILE ?= apps/tap-ai-backend/tests/fixtures/quality/test-design/profile-v1.json
TAP_QUALITY_TEST_OBSERVATIONS ?= .local/quality-test-design/observations.json
TAP_QUALITY_TEST_REPORT ?= .local/quality-test-design/report.json

quality-test-design: ## deterministically evaluate the committed QUALITY-TEST-01 candidate profile
	uv run --project apps/tap-ai-backend python scripts/evaluate-quality-test-design.py "$(TAP_QUALITY_TEST_PROFILE)" --report "$(TAP_QUALITY_TEST_REPORT)"

quality-test-design-real: ## require opted-in real model observations and one approved named reviewer
	@if [ "$${TAP_RUN_QUALITY_TEST_01:-}" != "1" ]; then \
		echo "quality-test-design-real requires TAP_RUN_QUALITY_TEST_01=1" >&2; \
		exit 2; \
	fi
	TAPPER_MODEL_TIMEOUT_SECONDS=60 uv run --project apps/tap-ai-backend python scripts/run-quality-test-design-candidate.py "$(TAP_QUALITY_TEST_PROFILE)" --observations "$(TAP_QUALITY_TEST_OBSERVATIONS)"
	uv run --project apps/tap-ai-backend python scripts/evaluate-quality-test-design.py "$(TAP_QUALITY_TEST_OBSERVATIONS)" --report "$(TAP_QUALITY_TEST_REPORT)" --real

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
	uv run --project apps/tap-ai-backend python scripts/milvus_bootstrap.py

milvus-health: ## run the destructive-isolated three-role behavioral probe
	uv run --project apps/tap-ai-backend python scripts/milvus_health_probe.py

research-embeddings: ## run the explicitly authorized paid embedding research profile
	@if [ "$${TAP_RUN_PAID_EMBEDDING_RESEARCH:-}" != "1" ]; then \
		echo "research-embeddings requires TAP_RUN_PAID_EMBEDDING_RESEARCH=1" >&2; \
		exit 2; \
	fi
	uv run --project apps/tap-ai-backend python scripts/milvus_embedding_research.py

test-milvus: ## run the non-skippable real Milvus correctness gate with committed vectors
	$(MAKE) milvus-up TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	$(MAKE) milvus-bootstrap TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	$(MAKE) milvus-health TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	$(MAKE) milvus-bootstrap TAP_MILVUS_COMPOSE_PROJECT="$(TAP_MILVUS_COMPOSE_PROJECT)"
	uv run --project apps/tap-ai-backend python scripts/milvus_fixture.py publish --fixture apps/tap-ai-backend/tests/fixtures/milvus/doc-fixture-v1.json --queries apps/tap-ai-backend/tests/fixtures/milvus/query-cases-v1.json --vectors apps/tap-ai-backend/tests/fixtures/milvus/vectors-research-embedding-v1.json
	MILVUS_URI="$${MILVUS_URI:-http://127.0.0.1:19530}" \
	MILVUS_DATABASE="$${MILVUS_DATABASE:-default}" \
	MILVUS_READER_USERNAME="$${MILVUS_READER_USERNAME:-tap_reader}" \
	MILVUS_READER_PASSWORD="$${MILVUS_READER_PASSWORD:-tap-local-Reader1!}" \
	MILVUS_WRITER_USERNAME="$${MILVUS_WRITER_USERNAME:-tap_writer}" \
	MILVUS_WRITER_PASSWORD="$${MILVUS_WRITER_PASSWORD:-tap-local-Writer1!}" \
	MILVUS_PROVISIONER_USERNAME="$${MILVUS_PROVISIONER_USERNAME:-tap_provisioner}" \
	MILVUS_PROVISIONER_PASSWORD="$${MILVUS_PROVISIONER_PASSWORD:-tap-local-Provisioner1!}" \
	TAP_RUN_MILVUS_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_milvus_search_acl.py apps/tap-ai-backend/tests/integration/test_milvus_rebuild_alias.py -v

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

demo-up: ## start durable Tapper middleware and initialize exact owned resources
	@set -eu; \
	tapper_demo_project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	case "$$tapper_demo_project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#tapper_demo_project}" -ge 3 ] && [ "$${#tapper_demo_project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	readonly tapper_demo_project; \
	if [ -f "$(TAP_REPO_ROOT)/.env" ]; then set -a; . "$(TAP_REPO_ROOT)/.env"; set +a; fi; \
	unset OPENAI_API_KEY BAILIAN_API_KEY BAILIAN_API_BASE; \
	unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE; \
	export TAP_TAPPER_COMPOSE_PROJECT="$$tapper_demo_project"; \
	if [ "$${TAPPER_OBJECT_STORE_PROVIDER:-azure}" = minio ]; then \
		TAPPER_OBJECT_STORE_IMAGE="$$(bash "$(TAP_REPO_ROOT)/scripts/build-tapper-object-store.sh" verify)"; \
		export TAPPER_OBJECT_STORE_IMAGE; \
		docker compose -f "$(TAP_REPO_ROOT)/compose.yaml" -p "$$tapper_demo_project" --profile milvus --profile tapper-objects up -d --wait --wait-timeout 180; \
		tapper_object_container="$$(docker compose -f "$(TAP_REPO_ROOT)/compose.yaml" -p "$$tapper_demo_project" --profile tapper-objects ps -q tap-minio)"; \
		bash "$(TAP_REPO_ROOT)/scripts/build-tapper-object-store.sh" verify-container "$$tapper_object_container" >/dev/null; \
	else \
		docker compose -f "$(TAP_REPO_ROOT)/compose.yaml" -p "$$tapper_demo_project" --profile milvus up -d --wait --wait-timeout 180; \
	fi; \
	unset DASHSCOPE_API_KEY DASHSCOPE_API_BASE; \
	uv run --project apps/tap-ai-backend alembic -c apps/tap-ai-backend/alembic.ini upgrade head; \
	TAP_ALLOW_INITIAL_MILVUS_ROOT=1 uv run --project apps/tap-ai-backend python scripts/milvus_bootstrap.py; \
	uv run --project apps/tap-ai-backend python scripts/tapper_collection.py ensure

demo-check: ## check exact Tapper dependencies without exposing provider details
	@set -eu; \
	tapper_demo_project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	case "$$tapper_demo_project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#tapper_demo_project}" -ge 3 ] && [ "$${#tapper_demo_project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	readonly tapper_demo_project; \
	if [ -f "$(TAP_REPO_ROOT)/.env" ]; then set -a; . "$(TAP_REPO_ROOT)/.env"; set +a; fi; \
	unset OPENAI_API_KEY BAILIAN_API_KEY BAILIAN_API_BASE; \
	unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE; \
	export TAP_TAPPER_COMPOSE_PROJECT="$$tapper_demo_project"; \
	uv run --project apps/tap-ai-backend python scripts/check-tapper-demo.py

demo-dev: ## run API, relay, ingestion, graph, generation, and Web on loopback ports
	@set -eu; \
	project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	case "$$project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#project}" -ge 3 ] && [ "$${#project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	TAPPER_COMPOSE_OBJECT_STORE_VERIFY=1 bash scripts/run-tapper-dev.sh

legacy-tapper-codex-dev: ## run only the retired loopback Codex answer composition
	bash scripts/run-tapper-legacy-codex-dev.sh

demo-e2e: ## run the isolated deterministic browser and persistence journey
	@set -eu; \
	project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	case "$$project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#project}" -ge 3 ] && [ "$${#project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	bash scripts/run-tapper-e2e.sh

demo-down: ## stop only the selected Tapper project and preserve named volumes
	@set -eu; \
	project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	case "$$project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#project}" -ge 3 ] && [ "$${#project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	docker compose -f "$(TAP_REPO_ROOT)/compose.yaml" -p "$$project" --profile milvus down --remove-orphans

demo-reset: ## explicitly remove only the default Tapper project's volumes
	@set -eu; \
	project="$${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"; \
	allow_reset="$${TAP_ALLOW_TAPPER_VOLUME_RESET:-0}"; \
	case "$$project" in ''|[-_]*|*[!a-z0-9_-]*) echo "invalid Tapper Compose project" >&2; exit 2;; esac; \
	[ "$${#project}" -ge 3 ] && [ "$${#project}" -le 63 ] || { echo "invalid Tapper Compose project" >&2; exit 2; }; \
	[ "$$project" = tap-tapper-demo ] || { echo "Tapper volume reset requires exact project tap-tapper-demo" >&2; exit 2; }; \
	[ "$$allow_reset" = 1 ] || { echo "Tapper volume reset requires TAP_ALLOW_TAPPER_VOLUME_RESET=1" >&2; exit 2; }; \
	docker compose -f "$(TAP_REPO_ROOT)/compose.yaml" -p "$$project" --profile milvus down --volumes --remove-orphans

schema-drift: ## compare ORM metadata with an isolated migrated MySQL
	uv run --project apps/tap-ai-backend python scripts/check-schema-drift.py

export MIGRATION

migration-check: ## preserve frozen 0005 data through an exact migration revision
	uv run --project apps/tap-ai-backend python scripts/check-migration.py "$${MIGRATION:-}"

.PHONY: knowledge-recover
knowledge-recover: ## bounded Knowledge operator; pass ARGS with command and explicit --project
	uv run --project apps/tap-ai-backend python scripts/knowledge-operator.py $(ARGS)

.PHONY: object-store-build
object-store-build: ## build the pinned local MinIO image for an explicit platform
	bash scripts/build-tapper-object-store.sh build --platform "$(PLATFORM)"

.PHONY: parser-build parser-security
parser-build: ## build the pinned local parser image for an explicit platform
	bash scripts/build-tapper-parser.sh build --platform "$${TAPPER_PARSER_PLATFORM:-linux/arm64}"

parser-security: ## run real security checks in owned isolated parser containers
	TAP_RUN_PARSER_SECURITY=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/security/test_owned_parser.py -v

gate-v0: ## require complete owned V0 native evidence with zero skips
	bash scripts/run-tapper-v0-gate.sh
