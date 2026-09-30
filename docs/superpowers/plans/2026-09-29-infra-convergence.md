# 基础设施收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 模型只经 `ModelGateway → LiteLLM`，模型目录由 LiteLLM `/v1/model/info` 动态提供；删除 Codex、Azure、Azurite 与 V0 门禁；默认服务收敛为 MySQL、Redis、MinIO、Milvus、LiteLLM。

**Architecture:** 新增 `LiteLLMCatalog`（60 秒缓存）作为模型目录与角色校验的唯一来源；`LiteLLMModelGateway` 不再持有上游映射，改为记录响应中的实际模型；运行时以三个角色变量指向 LiteLLM `model_name`。删除代码前先打参考 tag。

**顺序说明：** 与 spec 第 7 节不同，先新增目录（纯新增、无依赖），再删 Codex，最后改造 gateway 与运行时，避免改造即将删除的遗留代码。

**Tech Stack:** Python 3.13、FastAPI、httpx、pytest、uv；React/TypeScript/Vitest；Docker Compose；LiteLLM v1.87.0。

**Spec:** `docs/superpowers/specs/2026-09-29-infra-convergence-design.md`

## Global Constraints

- 不考虑向后兼容：旧模型名、旧 Azurite 数据、旧定位符不迁移；本地升级执行 `make demo-reset`。
- 模型名：全小写、连字符，`<模型家族>-<型号>`；本次为 `qwen-plus`、`qwen-flash`、`qwen-max`、`qwen3-vl-plus`、`text-embedding-v4`。
- 能力只用 LiteLLM 标准字段 `mode`、`supports_vision`、`supports_response_schema`、`supports_function_calling`；唯一自定义字段为可选 `tapper_display_name`，缺省显示 `model_name`。
- 角色变量：`TAPPER_DEFAULT_CHAT_MODEL=qwen-plus`、`TAPPER_EMBEDDING_MODEL=text-embedding-v4`、`TAPPER_VISION_MODEL`（默认空）。
- 目录缓存 60 秒；刷新失败沿用上次成功结果；从未成功则 `ModelGatewayUnavailable` → 503 Problem Details；后端启动时不连接 LiteLLM。
- 实际模型记录而非拒绝；保留响应体结构、embedding 维度、token 数值范围校验。
- 请求模型不在目录中 → "模型不可用"，不回退。
- 非 `art1.` 定位符与非 `stg1.` 暂存键一律 `ArtifactUnavailable`。
- `TAPPER_MODEL_BACKEND`（`litellm | fake`）保留。
- 工作区 `apps/tap-ai-backend/tests/` 下 4 个用户未提交改动（`conftest.py`、`test_document_ledger.py`、`test_document_upload_recovery.py`、`test_ingestion_recovery.py`）不得暂存或修改；`git add` 只用显式路径。
- 推送 tag 前征得用户确认。
- 提交：小写祈使 Conventional Commit，结尾 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；分支 `claude/v1-infra-convergence`。
- 后端测试命令前缀 `uv run --project apps/tap-ai-backend pytest`（下文简写 `PYTEST`）。

## Review Focus

- LiteLLM 同一 `model_name` 配多个部署（负载均衡）：目录中只出现一次，能力取交集 —— Task 2 `test_duplicate_deployments_merge_with_capability_intersection`。
- 目录刷新时 LiteLLM 暂时宕机：已加载过的目录继续可用，不让正在使用的会话报错 —— Task 2 `test_refresh_failure_keeps_last_good_routes`。
- LiteLLM fallback 返回与请求不同的上游模型：调用成功且 `actual_model` 为实际返回值 —— Task 3 `test_fallback_model_is_recorded_not_rejected`。
- 会话或 Agent 引用了已从配置删除的模型：返回"模型不可用"，不静默换模型 —— Task 3 `test_unknown_model_is_rejected_without_fallback`。
- `TAPPER_VISION_MODEL` 指向不支持视觉的模型：健康检查报告原因，流程图识别调用 503，而不是在上游报晦涩错误 —— Task 5 `test_vision_role_without_supports_vision_reports_unhealthy`。

---

### Task 1: 实测 LiteLLM `/v1/model/info`

**Files:** 无仓库改动（临时配置放 `/tmp/litellm-probe/`）。

**Interfaces:**
- Produces: 账本记录两个事实——普通 key 调用 `/v1/model/info` 时，`data[].model_info` 是否包含 (a) `mode`/`supports_*` 标准字段，(b) 自定义 `tapper_display_name`；以及 `data[].model_name` 字段名。Task 2 按此解析。

- [ ] **Step 1: 起一个临时 LiteLLM**

`/tmp/litellm-probe/config.yaml` 含两个条目：`qwen-plus`（`model: dashscope/qwen-plus`，`api_key: sk-probe`，`model_info: {mode: chat, supports_response_schema: true, tapper_display_name: Qwen Plus}`）与 `text-embedding-v4`（`model_info: {mode: embedding}`），`general_settings.master_key: sk-master`。

Run: `docker run -d --rm --name litellm-probe -p 127.0.0.1:14000:4000 -v /tmp/litellm-probe/config.yaml:/app/config.yaml ghcr.io/berriai/litellm:v1.87.0 --config /app/config.yaml`，等待 `curl -s 127.0.0.1:14000/health/liveliness` 返回 200。

- [ ] **Step 2: 用 master key 与普通 key 各取一次**

Run: `curl -s -H "Authorization: Bearer sk-master" 127.0.0.1:14000/v1/model/info | python3 -m json.tool | head -80`。若 LiteLLM 无数据库无法生成普通 key，则只验证 master key 并在账本记录"后端沿用现有 key；普通 key 行为待 demo 环境 Task 9 复核"。
Expected: 看到 `model_name` 与 `model_info.mode`、`supports_response_schema`、`tapper_display_name`。

- [ ] **Step 3: 判定并停止容器**

`docker stop litellm-probe`。若标准字段缺失：**停下**，向用户报告（spec 第 5 节）。若仅自定义字段缺失：继续，账本记 Ruling"展示名回退 `model_name`"。无提交。

---

### Task 2: `LiteLLMCatalog`

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/adapters/litellm_catalog.py`
- Test: `apps/tap-ai-backend/tests/contract/test_litellm_catalog.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class LiteLLMModel:
      name: str; display_name: str; mode: Literal["chat", "embedding"]
      supports_vision: bool; supports_response_schema: bool; supports_function_calling: bool

  @dataclass(frozen=True, slots=True)
  class LiteLLMRoutes:
      models: Mapping[str, LiteLLMModel]
      def get(self, name: str) -> LiteLLMModel | None: ...

  @dataclass(frozen=True, slots=True)
  class ModelRoles:
      default_chat_model: str; embedding_model: str; vision_model: str | None
      def problems(self, routes: LiteLLMRoutes) -> tuple[str, ...]: ...  # 空元组表示通过

  class LiteLLMCatalog:
      def __init__(self, *, base_url: str, api_key: str, client: httpx.AsyncClient | None = None,
                   ttl_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None: ...
      async def routes(self) -> LiteLLMRoutes: ...   # 无成功结果时抛 ModelGatewayUnavailable
      async def aclose(self) -> None: ...
  ```

- [ ] **Step 1: 写失败测试**（`httpx.MockTransport` 模拟 `GET /v1/model/info`，响应形如 `{"data": [{"model_name": ..., "model_info": {...}}]}`）

```python
async def test_parses_chat_and_embedding_models(): ...
    # qwen-plus: mode chat, supports_response_schema, tapper_display_name "Qwen Plus"
    # assert routes.get("qwen-plus") == LiteLLMModel("qwen-plus", "Qwen Plus", "chat", False, True, False)
async def test_display_name_defaults_to_model_name(): ...          # 无 tapper_display_name → "qwen-max"
async def test_ignores_entries_without_supported_mode(): ...       # mode 缺失 / "image_generation" → get() is None
async def test_duplicate_deployments_merge_with_capability_intersection(): ...
    # 两条 qwen-plus，supports_vision True/False → 一个条目，supports_vision False
async def test_caches_for_ttl_then_refreshes(): ...                # 假 clock：59s 内 1 次请求，61s 后 2 次
async def test_refresh_failure_keeps_last_good_routes(): ...       # 首次 200，过期后 500 → 仍返回首次结果
async def test_never_loaded_raises_unavailable(): ...              # 首次 500 / 超时 → ModelGatewayUnavailable
async def test_sends_bearer_key(): ...                             # Authorization == "Bearer <api_key>"
def test_roles_report_missing_and_wrong_mode(): ...
    # default_chat_model 不存在 → problems 含 "TAPPER_DEFAULT_CHAT_MODEL"
    # embedding_model 指向 chat 模型 → 含 "TAPPER_EMBEDDING_MODEL"
def test_vision_role_requires_supports_vision(): ...               # 含 "TAPPER_VISION_MODEL"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/contract/test_litellm_catalog.py -q`
Expected: FAIL（`ModuleNotFoundError: tap.modules.ai.adapters.litellm_catalog`）

- [ ] **Step 3: 实现**

响应体按 1 MiB 上限读取（沿用 `litellm.py` 的有界读取方式）；base_url 校验沿用 `LiteLLMModelGatewayConfig` 的 HTTPS/loopback 规则；缓存以 `asyncio.Lock` 防并发重复刷新。`problems()` 返回的每条消息以变量名开头，如 `"TAPPER_VISION_MODEL=qwen-plus does not support vision"`。

- [ ] **Step 4: 运行确认通过**

Run: 同 Step 2。Expected: 10 passed。

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/ai/adapters/litellm_catalog.py apps/tap-ai-backend/tests/contract/test_litellm_catalog.py
git commit -m "feat: add litellm model catalog with cached model info"
```

---

### Task 3: 删除 Codex 与遗留运行时

**Files:**
- Delete: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/codex_exec.py`、`codex_target.py`；`apps/tap-ai-backend/src/tap/entrypoints/legacy_loopback_answer_runtime.py`、`legacy_litellm.py`；`scripts/run-tapper-legacy-codex-dev.sh`；`apps/tap-ai-backend/tests/contract/test_codex_exec_strict.py`、`test_codex_target_strict.py`、`test_litellm_strict.py`；`apps/tap-ai-backend/tests/unit/entrypoints/test_legacy_loopback_answer_runtime.py`；`apps/tap-ai-backend/tests/smoke/test_tapper_codex_smoke.py`
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`（删 `_create_codex_answers`、`include_codex`、`_FIXED_CODEX_CHAT_ALIAS` 及目录条目、`TAPPER_ANSWER_BACKEND` 与 `TAPPER_CODEX_*` 解析）；`apps/tap-ai-backend/src/tap/modules/knowledge/adapters/litellm.py`（删 `alternate_answers`）；`Makefile`（删 `legacy-tapper-codex-dev` 及 `.PHONY` 项）；`scripts/run-tapper-dev.sh`、`scripts/run-tapper-e2e.sh`（删 `CODEX_*` unset 与 codex 拒绝逻辑）；`.env.example`（删 `TAPPER_ANSWER_BACKEND`、`TAPPER_CODEX_*` 及注释块）
- Modify tests: `tests/architecture/test_model_gateway_composition.py`、`test_module_boundaries.py`（子进程白名单只剩 `tapper_parser_worker.py`；删除针对已删模块的断言）；`tests/contract/test_litellm_model_gateway.py`、`test_knowledge_api.py`、`test_demo_commands.py`；`tests/unit/entrypoints/test_tapper_runtime.py`、`tests/unit/ai/test_model_catalog.py`、`tests/unit/knowledge/test_ingestion_worker.py`；`tests/integration/test_relay_entrypoint.py`、`test_ingestion_entrypoint.py`、`test_test_plan_repository.py` —— 删除 Codex/legacy 用例或改用 `LiteLLMModelGateway` 桩
- Modify: `apps/tap-ai-frontend/src/features/knowledge/testing/renderKnowledgeApp.tsx`（删 `tapper-chat-codex`）
- Create test: `apps/tap-ai-backend/tests/architecture/test_no_legacy_providers.py`

**Interfaces:**
- Produces: `test_no_legacy_providers.py` 中 `FORBIDDEN = re.compile(r"(^|\.)codex|^azure")`，Task 6 依赖其覆盖 azure。

- [ ] **Step 1: 写失败的架构测试**

```python
def test_src_has_no_codex_or_azure_imports():
    # 遍历 apps/tap-ai-backend/src/**/*.py，ast.walk 收集 Import/ImportFrom（含函数体内）模块名
    # 断言无模块名匹配 FORBIDDEN
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/architecture/test_no_legacy_providers.py -q`
Expected: FAIL，列出 `codex_exec`、`codex_target`、`azure.storage.blob` 等。

- [ ] **Step 3: 删除 Codex 与遗留运行时并修正引用**

临时让测试只检查 codex：Task 3 内把断言限定为 `codex`（`azure` 分支用 `pytest.mark.xfail(strict=True, reason="removed in Task 6")` 的独立测试 `test_src_has_no_azure_imports`）。

- [ ] **Step 4: 验证**

Run:
```bash
PYTEST apps/tap-ai-backend/tests/architecture -q
PYTEST apps/tap-ai-backend/tests -q -x -p no:cacheprovider 2>&1 | tail -5
git grep -n -i "codex" -- apps/tap-ai-backend/src scripts Makefile compose.yaml .env.example apps/tap-ai-frontend/src
corepack pnpm --filter @tap/ai-frontend exec vitest run
```
Expected: 架构测试通过（azure 用例为 xfail）；全量 pytest 无失败；grep 无输出；Vitest 通过。

- [ ] **Step 5: 提交**

```bash
git add -A apps/tap-ai-backend/src apps/tap-ai-backend/tests/architecture apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/smoke apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/integration/test_relay_entrypoint.py apps/tap-ai-backend/tests/integration/test_ingestion_entrypoint.py apps/tap-ai-backend/tests/integration/test_test_plan_repository.py scripts Makefile .env.example apps/tap-ai-frontend/src
git commit -m "refactor: remove codex and legacy answer runtime"
```
（`git add -A <路径>` 只作用于列出的路径；提交前 `git diff --cached --name-only | grep -E "conftest.py|test_document_ledger|test_document_upload_recovery|test_ingestion_recovery"` 必须无输出。）

---

### Task 4: Gateway 接入目录，记录实际模型

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/adapters/litellm.py`
- Modify: `apps/tap-ai-backend/src/tap/testing/deterministic_model_gateway.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/ai/application/catalog.py`（删 `default_alias="tapper-chat"` 默认值，改为必填）
- Test: `apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py`

**Interfaces:**
- Consumes: Task 2 `LiteLLMCatalog`、`LiteLLMRoutes`、`ModelRoles`。
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class LiteLLMModelGatewayConfig:
      base_url: str; api_key: str
      roles: ModelRoles
      embedding_dimension: int
      timeout_seconds: float = 15.0
      max_retries: int = 1
  class LiteLLMModelGateway:
      def __init__(self, config, *, scope, redact, catalog: LiteLLMCatalog, client=None) -> None
      async def health_problems(self) -> tuple[str, ...]   # 目录不可达 → ("LiteLLM model catalog unavailable",)
  ```
  删除 `ProviderModelMapping`、`ChatModelRoute`、`disabled_aliases`、`additional_chat_models`。`test_management/adapters/model_gateway_generation.py`、`test_management/adapters/mysql.py`、`scripts/run-quality-kb-real.py` 中对 `ProviderModelMapping` 的使用改为直接使用模型名字符串。

- [ ] **Step 1: 改写/新增失败测试**（删除依赖上游映射与响应头一致性拒绝的旧用例）

```python
async def test_catalog_lists_chat_models_and_embedding(): ...
    # 目录: qwen-plus(chat, schema), qwen-flash(chat), text-embedding-v4(embedding)
    # catalog() == (Descriptor("qwen-plus","Qwen Plus",{CHAT,STRUCTURED}), Descriptor("qwen-flash","qwen-flash",{CHAT}),
    #               Descriptor("text-embedding-v4","text-embedding-v4",{EMBED}))
async def test_fallback_model_is_recorded_not_rejected(): ...
    # 请求 qwen-plus，响应 body["model"]="openai/gpt-4o-mini" → 成功；actual_model=="openai/gpt-4o-mini"；actual_provider=="openai"
async def test_actual_provider_unknown_without_prefix(): ...      # body["model"]="qwen-plus" → actual_provider=="unknown"
async def test_unknown_model_is_rejected_without_fallback(): ...  # alias="removed-model" → ModelGatewayRejected，未发 HTTP
async def test_structured_requires_supports_response_schema(): ...# generate_structured(alias="qwen-flash") → ModelGatewayRejected
async def test_image_input_requires_supports_vision(): ...
async def test_embed_uses_embedding_role(): ...                    # embed(alias="text-embedding-v4") 成功；alias="qwen-plus" → Rejected
async def test_catalog_unavailable_maps_to_unavailable(): ...     # 目录从未加载成功 → ModelGatewayUnavailable
async def test_embedding_dimension_still_enforced(): ...          # 维度不符 → ModelGatewayUnavailable
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py -q`
Expected: FAIL（`LiteLLMModelGatewayConfig` 无 `roles` 参数等）。

- [ ] **Step 3: 实现**

`_validate` 以 `await catalog.routes()` 查模型：chat/structured 需 `mode == "chat"`，structured 另需 `supports_response_schema`，图片输入需 `supports_vision`，embed 需等于 `roles.embedding_model` 且 `mode == "embedding"`。`_normalize` 删除 `x-litellm-model-id`/`x-litellm-model-group`/`returned_model` 一致性判断，`actual_model = body["model"]`（非空字符串，否则 `ModelGatewayUnavailable`），`actual_provider = actual_model.split("/",1)[0] if "/" in actual_model else "unknown"`。`DeterministicModelGateway` 接收一个内置 `LiteLLMCatalog` 替身（子类重写 `routes()` 返回 `qwen-plus`(chat, schema, vision) 与 `text-embedding-v4`），不发 HTTP。

- [ ] **Step 4: 运行确认通过**

Run: `PYTEST apps/tap-ai-backend/tests/contract/test_litellm_model_gateway.py apps/tap-ai-backend/tests/contract/test_litellm_catalog.py -q`
Expected: 全部通过。

- [ ] **Step 5: 不单独提交**

`tapper_runtime.py` 此时仍使用旧配置字段，全量测试不可用；Task 4 的改动与 Task 5 合并为一次提交，避免提交历史中出现不可运行的中间状态。

---

### Task 5: 运行时角色配置与模型改名

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`
  - 删除 `_FIXED_CHAT_ALIAS`、`_FIXED_EMBEDDING_ALIAS`、`_FIXED_VISION_ALIAS`、`_FIXED_LITELLM_EMBEDDING_ROUTE`、`_model_route`、`_model_labels`、`LITELLM_MODEL`/`LITELLM_TAPPER_*` 解析、`TAPPER_CHAT_ALIAS`/`TAPPER_EMBEDDING_ALIAS` 解析、`litellm_model`/`litellm_embedding_model`/`vision_model` 字段。
  - `TapperSettings` 新增 `default_chat_model: str`（`TAPPER_DEFAULT_CHAT_MODEL`，默认 `qwen-plus`）、`embedding_model: str`（`TAPPER_EMBEDDING_MODEL`，默认 `text-embedding-v4`）、`vision_model: str | None`（`TAPPER_VISION_MODEL`，空→`None`）；三者用现有 `_fixed_value` 的名称格式校验 `^[a-z0-9]+(?:[.-][a-z0-9]+)*$`。原 `chat_alias`/`embedding_alias` 的所有使用点改为这两个字段。
  - 构造一个进程级 `LiteLLMCatalog` 传给 gateway；`models_ready()` 改为 `not await gateway.health_problems()`，删除 `_read_models_labels`。
  - 流程图识别：`settings.vision_model` 非空时构造 `ModelGatewayFlowchartVision(alias=settings.vision_model)`。
- Modify（硬编码模型名改为注入）：
  - `modules/chat/adapters/mysql_conversations.py:463`：构造参数新增 `default_chat_model: str`，替换字面量。
  - `interfaces/http/knowledge_service.py:301-303`：`self._models is None` 时 `supported = frozenset()`，删除字面量回退。
  - `interfaces/http/routes/insights_explanations.py:72`：从 app state 的 `ModelCatalog.default_alias` 取值。
  - `tapper_runtime.py:981` `MysqlGraphReadyProjection(model_alias=settings.default_chat_model)`。
  - `modules/knowledge/adapters/flowchart_vision.py:80`：`alias` 改为必填。
  - `modules/knowledge/adapters/milvus_documents.py:68`：`TAPPER_EMBEDDING_MODEL = "text-embedding-v4"`。
- Modify: `deploy/local/litellm/config.yaml`（按 spec 第 3 节完整示例重写 5 个模型；上游模型直接写在 `litellm_params.model`）。
- Modify: `apps/tap-ai-frontend/src/widgets/tap/prototype/model.ts:8`（`DEFAULT_MODEL_ID = "qwen-plus"`）。
- Modify（全局改名）：测试、fixture、前端测试与脚本中 `tapper-chat-flash`→`qwen-flash`、`tapper-chat-max`→`qwen-max`、`tapper-chat`→`qwen-plus`、`tapper-embedding`→`text-embedding-v4`、`tapper-vision`→`qwen3-vl-plus`（按此顺序替换，先长后短）；`scripts/run-tapper-e2e.sh` 的 `TAPPER_CHAT_ALIAS`/`TAPPER_EMBEDDING_ALIAS` 改为 `TAPPER_DEFAULT_CHAT_MODEL=qwen-plus`/`TAPPER_EMBEDDING_MODEL=text-embedding-v4`。
- Test: `apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py`

**Interfaces:**
- Consumes: Task 4 `LiteLLMModelGatewayConfig(roles=...)`、`LiteLLMModelGateway.health_problems()`。

- [ ] **Step 1: 写失败测试**

```python
def test_settings_read_model_roles(): ...
    # from_mapping({... "TAPPER_DEFAULT_CHAT_MODEL": "qwen-max", "TAPPER_EMBEDDING_MODEL": "text-embedding-v4"})
    # → default_chat_model == "qwen-max"; vision_model is None
def test_settings_defaults(): ...          # 未设置 → "qwen-plus" / "text-embedding-v4" / None
def test_settings_reject_invalid_model_name(): ...   # "Qwen Plus" → ValueError
def test_legacy_model_variables_are_ignored(): ...   # 仅设 TAPPER_CHAT_ALIAS=x → default_chat_model 仍为 "qwen-plus"
async def test_vision_role_without_supports_vision_reports_unhealthy(): ...
    # 目录 qwen-plus 无 supports_vision，TAPPER_VISION_MODEL=qwen-plus → models_ready() is False，
    # health_problems 含 "TAPPER_VISION_MODEL"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/unit/entrypoints/test_tapper_runtime.py -q`
Expected: FAIL。

- [ ] **Step 3: 实现上述修改与改名**

改名后若 quality fixture（`tests/fixtures/quality/**/profile-v1.json` 等）中的摘要因模型名变化而校验失败，用对应生成脚本（`scripts/generate-quality-graph-candidate.py`、`scripts/generate-quality-test-design-profile.py`）重新生成，不手改摘要。

- [ ] **Step 4: 全量验证**

Run:
```bash
PYTEST apps/tap-ai-backend/tests -q 2>&1 | tail -5
uv run --project apps/tap-ai-backend python scripts/export_contracts.py --check
corepack pnpm --filter @tap/ai-frontend exec vitest run
git grep -n "tapper-chat\|tapper-embedding\|tapper-vision\|TAPPER_CHAT_ALIAS\|TAPPER_EMBEDDING_ALIAS\|LITELLM_TAPPER_\|LITELLM_MODEL\b" -- apps scripts deploy compose.yaml Makefile .env.example
```
Expected: pytest 无失败；契约无漂移（若有漂移，运行 `make contracts` 并把生成物加入提交）；Vitest 通过；grep 仅剩 `compose.yaml`、`.env.example` 中待 Task 7 删除的 `LITELLM_*_MODEL`。

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/fixtures scripts/run-quality-kb-real.py apps/tap-ai-backend/tests/quality apps/tap-ai-backend/tests/integration/test_test_plan_repository.py deploy/local/litellm/config.yaml apps/tap-ai-frontend scripts contracts
git commit -m "feat: drive models from litellm catalog with role settings and upstream names"
```
（同 Task 3 的暂存检查；如其他 integration 测试文件因改名被修改，逐个显式列出。）

---

### Task 6: Azure 参考 tag，删除 Azure 与 V0 门禁

**Files:**
- Tag: `azure-reference-2026-09-29` 指向 `main`（`git rev-parse main`）。
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/object_artifacts.py`（移入其所需的定位符解析函数；删除 `legacy` 参数与所有旁路分支）
- Delete: `modules/knowledge/adapters/blob_artifacts.py`、`azure_ai_search.py`；`scripts/azurite_test_support.py`、`scripts/tapper_v0_gate.py`、`scripts/run-tapper-v0-gate.sh`；tests `contract/test_blob_artifact_contract.py`、`contract/test_azurite_test_support.py`、`integration/test_azurite_artifacts.py`、`contract/test_azure_search_strict.py`、`gates/test_v0_gate_report.py`
- Modify: `entrypoints/knowledge_bootstrap.py`（删 azure 分支与 `azure_factory`）；`tapper_runtime.py`（删 `TAPPER_OBJECT_STORE_PROVIDER`、`TAPPER_LEGACY_AZURE_ENABLED`、`blob_connection_string`、Azure 分支，`_create_blob` 只建 S3 store；`blob_ready()` 只查 `is_private()`）；`knowledge_operator.py` 相应调用
- Modify: `modules/knowledge/ports/search.py`、`ports/documents.py`、`ports/citations.py`（`SearchPort`、`ArtifactStore`、`CitationArtifactStore` docstring 写明实现契约与对应一致性测试文件名）
- Modify tests: `contract/search_provider_conformance.py`、`artifact_store_conformance.py`（只参数化 Milvus 与 S3/MinIO）；`contract/test_search_bootstrap.py`、`test_search_errors.py`、`test_knowledge_api.py`、`integration/test_search_acl.py`、`integration/test_minio_artifacts.py`（删混合 provider 用例）、`integration/test_tapper_persistence_restart.py`、`unit/.../test_object_artifacts.py`、`test_milvus_source_projection.py`、`contract/test_demo_commands.py`、`unit/entrypoints/test_tapper_runtime.py`、`architecture/test_module_boundaries.py`
- Modify: `apps/tap-ai-backend/pyproject.toml`（删 `azure-storage-blob`；`git grep -n "aiohttp" apps/tap-ai-backend/src scripts` 无结果则删 `aiohttp`），`uv lock`
- Modify: `Makefile`（删 `gate-v0` target、`.PHONY` 项、`check` 中 `tapper_v0_gate.py` 的 ruff/mypy 参数与 `bash -n scripts/run-tapper-v0-gate.sh`）

**Interfaces:**
- Consumes: Task 3 `test_src_has_no_azure_imports`（xfail）。

- [ ] **Step 1: 创建参考 tag（本地）**

Run: `git tag azure-reference-2026-09-29 main && git show --stat azure-reference-2026-09-29 | head -3`
Expected: tag 指向 `main` 当前提交。不推送（Task 9 征得确认后推送）。

- [ ] **Step 2: 写失败测试**

在 `test_object_artifacts.py` 中新增：
```python
async def test_legacy_locator_is_unavailable(): ...       # read_original("documents/x/y") → ArtifactUnavailable
async def test_legacy_staging_key_is_unavailable(): ...   # recover_original("legacy-key") → ArtifactUnavailable
```
并把 `test_src_has_no_azure_imports` 的 `xfail` 去掉。

- [ ] **Step 3: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/architecture/test_no_legacy_providers.py apps/tap-ai-backend/tests/unit -k "legacy_locator or legacy_staging or azure" -q`
Expected: FAIL（azure import 存在；`KnowledgeArtifactStore` 构造仍要求/接受 legacy）。

- [ ] **Step 4: 实现删除与迁移**

- [ ] **Step 5: 验证**

Run:
```bash
PYTEST apps/tap-ai-backend/tests -q 2>&1 | tail -5
git grep -n -i "azurite\|azure_blob\|AzureBlob\|azure_ai_search\|TAPPER_OBJECT_STORE_PROVIDER\|TAPPER_LEGACY_AZURE\|tapper_v0_gate" -- apps/tap-ai-backend scripts Makefile
grep -c "azure" apps/tap-ai-backend/uv.lock uv.lock 2>/dev/null
```
Expected: pytest 无失败；grep 无输出（`.env.example`、`compose.yaml`、各 shell 脚本留待 Task 7）；`uv.lock` 中 azure 计数为 0。

- [ ] **Step 6: 提交**

```bash
git add -A apps/tap-ai-backend/src apps/tap-ai-backend/pyproject.toml apps/tap-ai-backend/tests/contract apps/tap-ai-backend/tests/unit apps/tap-ai-backend/tests/architecture apps/tap-ai-backend/tests/gates apps/tap-ai-backend/tests/integration/test_azurite_artifacts.py apps/tap-ai-backend/tests/integration/test_search_acl.py apps/tap-ai-backend/tests/integration/test_minio_artifacts.py apps/tap-ai-backend/tests/integration/test_tapper_persistence_restart.py scripts/azurite_test_support.py scripts/tapper_v0_gate.py scripts/run-tapper-v0-gate.sh Makefile uv.lock apps/tap-ai-backend/uv.lock
git commit -m "refactor: remove azure providers and v0 gate, keep provider ports"
```
（锁文件只加入实际存在的那个；同 Task 3 的暂存检查。）

---

### Task 7: compose、Makefile、脚本与 `.env.example`

**Files:**
- Modify: `compose.yaml`（删 `azurite` 服务与 `azurite-data` 卷；`tap-minio` 删 `profiles`；`litellm` 删 5 个 `LITELLM_*_MODEL` 环境变量）
- Modify: `Makefile`（`demo-up` 删 provider 分支，始终验证 MinIO 镜像并启动；`demo-down`/`demo-reset` 保持 `--profile milvus`）
- Modify: `scripts/run-tapper-dev.sh`、`scripts/run-tapper-e2e.sh`（删 provider 分支、`AZURITE_BLOB_PORT`、`AZURE_STORAGE_CONNECTION_STRING`、`TAPPER_LEGACY_AZURE_ENABLED`，`--profile tapper-objects` 去掉）；`scripts/check-local-services.sh`（删 `check_azurite` 及调用）；`scripts/check-tapper-demo.py`（删 Azure blob canary 分支）；`scripts/tapper_collection.py`（删 provider 判断，始终建 bucket）
- Modify: `.env.example`（删 `AZURITE_IMAGE`、`AZURITE_BLOB_PORT`、`AZURE_STORAGE_CONNECTION_STRING`、`TAPPER_LEGACY_AZURE_ENABLED`、`TAPPER_OBJECT_STORE_PROVIDER` 及其注释、`LITELLM_MODEL`、`LITELLM_FLASH_MODEL`、`LITELLM_MAX_MODEL`、`LITELLM_TAPPER_EMBEDDING_MODEL`、`LITELLM_TAPPER_VISION_MODEL`、`TAPPER_CHAT_ALIAS`、`TAPPER_EMBEDDING_ALIAS`；新增 `TAPPER_DEFAULT_CHAT_MODEL=qwen-plus`、`TAPPER_EMBEDDING_MODEL=text-embedding-v4`、`TAPPER_VISION_MODEL=qwen3-vl-plus`）
- Modify tests: `contract/test_demo_commands.py`、`unit/operations/test_milvus_embeddings.py`（compose/LiteLLM 配置断言随之更新）

- [ ] **Step 1: 更新断言为目标状态（先红）**

`test_demo_commands.py` 中新增或改写：
```python
def test_compose_has_no_azurite_and_minio_is_default(): ...
    # compose["services"] 无 "azurite"；"tap-minio" 无 "profiles"；volumes 无 "azurite-data"
def test_litellm_service_has_no_model_indirection_variables(): ...
    # compose["services"]["litellm"]["environment"] 无任何以 "LITELLM_" 开头且以 "_MODEL" 结尾的键
def test_env_example_declares_model_roles(): ...
    # 含 TAPPER_DEFAULT_CHAT_MODEL=qwen-plus / TAPPER_EMBEDDING_MODEL=text-embedding-v4；不含 AZURE/AZURITE/TAPPER_OBJECT_STORE_PROVIDER
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTEST apps/tap-ai-backend/tests/contract/test_demo_commands.py -q`
Expected: 上述 3 个 FAIL。

- [ ] **Step 3: 实现文件修改**

- [ ] **Step 4: 验证**

Run:
```bash
PYTEST apps/tap-ai-backend/tests/contract/test_demo_commands.py apps/tap-ai-backend/tests/unit/operations -q
bash -n scripts/run-tapper-dev.sh scripts/run-tapper-e2e.sh scripts/check-local-services.sh
docker compose config -q && docker compose config --services
git grep -n -i "azurite\|azure_storage\|TAPPER_OBJECT_STORE_PROVIDER\|LITELLM_TAPPER_\|LITELLM_\(FLASH\|MAX\)_MODEL" -- compose.yaml Makefile scripts .env.example
```
Expected: 测试通过；`bash -n` 无输出；`--services` 不含 `azurite`；grep 无输出。

- [ ] **Step 5: 提交**

```bash
git add compose.yaml Makefile scripts .env.example apps/tap-ai-backend/tests/contract/test_demo_commands.py apps/tap-ai-backend/tests/unit/operations
git commit -m "chore: converge compose and scripts on minio and litellm roles"
```

---

### Task 8: 文档与总纲

**Files:**
- Create: `docs/guides/2026-09-29-azure-integration.md`、`docs/guides/2026-09-29-litellm-models.md`
- Modify: `docs/architecture.md`（第 3 节表格删 `azurite` 行、`tap-minio` 行改为唯一对象存储；第 4 节问答流写明模型目录来自 LiteLLM `/v1/model/info`）、`docs/index.md`（指南列表加两项）、`AGENTS.md`（Project Structure 本地服务句改为 "MySQL, Redis, MinIO, LiteLLM and Milvus"；删除 provider 描述）、`README.md`（启动说明加"从旧版本升级：先执行 `make demo-reset` 再重新导入"；删除 Codex/Azurite/`TAPPER_OBJECT_STORE_PROVIDER` 相关段落与命令；真实模型 smoke 未开启时的 skip 数改为 1）、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 5 工程完成项末尾追加"；每次模型调用持久化模型名、实际上游模型、供应商与 token 用量，历史回答可查看当时的实际模型"；子项目 1 标"进行中"并附 spec/plan 链接）

- [ ] **Step 1: 写 Azure 指南**

章节固定：1 扩展点（`SearchPort`、`ArtifactStore`、`CitationArtifactStore` 路径）；2 验收（`tests/contract/search_provider_conformance.py`、`artifact_store_conformance.py` 如何对新实现参数化）；3 接线（`tapper_runtime._create_search`、`_create_blob` 与需新增的配置项）；4 参考实现（`git show azure-reference-2026-09-29:apps/tap-ai-backend/src/tap/modules/knowledge/adapters/blob_artifacts.py` 与 `azure_ai_search.py`，以及旧测试路径）；5 可借鉴设计（SAS copy、ACL 过滤、四索引 schema，各 2–3 句）。

- [ ] **Step 2: 写 LiteLLM 模型指南**

章节固定：新增模型三步；`model_info` 字段表（`mode`、`supports_vision`、`supports_response_schema`、`supports_function_calling`、`tapper_display_name`）；命名规则（Global Constraints 原文）；角色变量表；"更换 embedding 模型须重建 Milvus 索引"；目录 60 秒缓存说明。

- [ ] **Step 3: 其余文档修改**

- [ ] **Step 4: 验证**

Run: `python3 /tmp/check_links.py | tail -1`（若脚本已不存在，按子项目 0 计划 Task 1 重建）；`git diff --check`。
Expected: 断链数不高于 30；无空白错误。

- [ ] **Step 5: 提交**

```bash
git add docs AGENTS.md README.md
git commit -m "docs: document model catalog, azure extension points and v1 roadmap update"
```

---

### Task 9: 全量与环境验证

- [ ] **Step 1: 仓库级检查**

Run: `make check > /tmp/infra-check.log 2>&1; echo $?; tail -20 /tmp/infra-check.log` 与 `make test > /tmp/infra-test.log 2>&1; echo $?; tail -20 /tmp/infra-test.log`
Expected: 两者退出码 0。

- [ ] **Step 2: 本地环境**

Run: `make demo-reset`（设置 `TAP_ALLOW_TAPPER_VOLUME_RESET=1`，项目名须为 `tap-tapper-demo`；执行前向用户确认，因会删除本地卷），`make demo-up`，`docker compose ps --services --status running`，`make demo-check`。
Expected: reset 后 `docker volume ls --format '{{.Name}}' | grep -c tapper-object-data` 为 0（spec 第 5 节）；运行中服务不含 `azurite`，含 `tap-minio`、`litellm`；`demo-check` 通过。

- [ ] **Step 3: 新增模型手动验证**

在 `deploy/local/litellm/config.yaml` 临时加入 `qwen-turbo`（`model: dashscope/qwen-turbo`，`model_info: {mode: chat}`），`docker compose restart litellm`，60 秒后请求后端模型目录接口，确认出现 `qwen-turbo` 且后端进程未重启；随后撤销该临时修改（不提交）。

- [ ] **Step 4: 隔离 E2E**

Run: `make demo-e2e > /tmp/infra-e2e.log 2>&1; echo $?; tail -30 /tmp/infra-e2e.log`
Expected: 退出码 0。无法运行（无 Docker/浏览器等）时如实记录原因。

- [ ] **Step 5: 推送 tag**

征得用户确认后：`git push origin azure-reference-2026-09-29`。
