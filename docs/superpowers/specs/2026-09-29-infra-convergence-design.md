# 基础设施收敛设计

日期：2026-09-29。V1 子项目 1，见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。

## 目标

1. 模型调用只有一条路径：`ModelGateway → LiteLLM`。删除 Codex 与遗留运行时。
2. 对象存储只有一种实现：S3 API（本地 MinIO）。删除 Azure Blob、Azure AI Search 与 Azurite；保留端口与一致性测试作为日后 Azure 接入的扩展点。
3. 新增模型只改 LiteLLM 配置：模型目录由 LiteLLM `/model/info` 动态提供，后端无需改代码或重启。
4. 默认启动的服务收敛为 MySQL、Redis、MinIO、Milvus、LiteLLM（另有 `tap-parser`；ClickHouse 仍在 `insights` profile 下，服务 `apps/backend`）。

不考虑向后兼容：旧模型名、旧 Azurite 数据与旧定位符不做迁移，本地升级后执行 `make demo-reset` 重建。

## 非目标

- LiteLLM 调用的重试退避、token 流式输出、并发上限：子项目 3。
- 每次调用的模型与 token 持久化：子项目 2（本 spec 只保证 `ModelResult` 中的实际模型准确）。
- `operations/milvus/bailian.py`：仅供 `make research-embeddings`，不在运行时路径，保留。

## 1. 删除

### Codex 与遗留运行时

- `modules/knowledge/adapters/codex_exec.py`、`codex_target.py`
- `entrypoints/legacy_loopback_answer_runtime.py`、`entrypoints/legacy_litellm.py`
- `tapper_runtime.py`：`_create_codex_answers`、`include_codex` 参数、`_FIXED_CODEX_CHAT_ALIAS` 与模型目录中的 Codex 条目、`TAPPER_ANSWER_BACKEND` 解析
- `KnowledgeModelGateway` 的 `alternate_answers`
- `Makefile` 的 `legacy-tapper-codex-dev` 与 `scripts/run-tapper-legacy-codex-dev.sh`；dev/e2e 脚本中的 `CODEX_*` 处理
- 相关测试（`test_codex_exec_strict.py`、`test_codex_target_strict.py`、`test_legacy_loopback_answer_runtime.py`、`test_litellm_strict.py`、`smoke/test_tapper_codex_smoke.py` 等）及其他测试中的 Codex 用例
- 前端测试 fixture 中的 `tapper-chat-codex`

### Azure 与 Azurite

- `modules/knowledge/adapters/blob_artifacts.py`（`AzureBlobArtifactStore`）、`azure_ai_search.py`
- `entrypoints/knowledge_bootstrap.py` 的 `TAP_SEARCH_BACKEND=azure` 分支
- `TAPPER_OBJECT_STORE_PROVIDER`、`TAPPER_LEGACY_AZURE_ENABLED` 及 `KnowledgeArtifactStore` 的 `legacy=` 旁路：非 `art1.` 定位符与非 `stg1.` 暂存键一律 `ArtifactUnavailable`
- `azure-storage-blob` 依赖；`aiohttp` 若无其他使用者一并删除
- `scripts/azurite_test_support.py`，`check-local-services.sh` 的 `check_azurite`，`check-tapper-demo.py`、`tapper_collection.py`、`run-tapper-dev.sh`、`run-tapper-e2e.sh`、`Makefile` 中的 provider 分支与 Azurite 处理
- 相关测试（`test_blob_artifact_contract.py`、`test_azurite_test_support.py`、`test_azurite_artifacts.py`、`test_azure_search_strict.py` 等）及混合 provider 用例

### V0 门禁

- `Makefile` 的 `gate-v0` 及 `check` 中对 `scripts/tapper_v0_gate.py` 的 ruff/mypy 条目
- `scripts/tapper_v0_gate.py`、`scripts/run-tapper-v0-gate.sh`、`tests/gates/test_v0_gate_report.py`

### compose

- 删除 `azurite` 服务与 `azurite-data` 卷。
- `tap-minio` 去掉 `tapper-objects` profile，默认启动。
- `litellm` 服务删除 `LITELLM_MODEL`、`LITELLM_FLASH_MODEL`、`LITELLM_MAX_MODEL`、`LITELLM_TAPPER_EMBEDDING_MODEL`、`LITELLM_TAPPER_VISION_MODEL` 环境变量。

## 2. 保留与调整

- **定位符解析**：`object_artifacts.py` 需要的解析函数从 `blob_artifacts.py` 移入 `object_artifacts.py`（或其私有模块）；只服务旧格式的解析删除。
- **端口**：`SearchPort`、`ArtifactStore`、`CitationArtifactStore` 保持不变，补充 docstring 说明实现契约。
- **一致性测试**：`search_provider_conformance.py`、`artifact_store_conformance.py` 只由 Milvus 与 S3/MinIO 实现运行，作为新 provider 的验收标准。
- **架构测试**
  - 子进程白名单只剩 `tapper_parser_worker.py`。
  - 新增：AST 扫描 `src/`，任何位置（含函数内）不得 import 名称含 `codex` 或以 `azure` 开头的模块。
  - 更新 `test_model_gateway_composition.py`、`test_module_boundaries.py` 中引用已删模块的断言。
- **`TAPPER_MODEL_BACKEND`**（`litellm | fake`）保留，`fake` 仍用于 E2E。

## 3. 模型通路

### 配置：`deploy/local/litellm/config.yaml` 为唯一来源

`model_name` 即模型名，按模型命名：全小写、连字符，`<模型家族>-<型号>`，不含角色或供应商账号。上游模型直接写在配置中，`.env` 只放凭据。

```yaml
model_list:
  - model_name: qwen-plus
    litellm_params:
      model: dashscope/qwen-plus
      api_key: os.environ/DASHSCOPE_API_KEY
      api_base: os.environ/DASHSCOPE_API_BASE
    model_info:
      mode: chat
      supports_response_schema: true
      tapper_display_name: Qwen Plus
  - model_name: qwen-flash          # 同上，Qwen Flash
  - model_name: qwen-max            # 同上，Qwen Max
  - model_name: qwen3-vl-plus
    litellm_params: { model: dashscope/qwen3-vl-plus, ... }
    model_info: { mode: chat, supports_vision: true, supports_response_schema: true, tapper_display_name: Qwen3 VL Plus }
  - model_name: text-embedding-v4
    litellm_params: { model: dashscope/text-embedding-v4, ... }
    model_info: { mode: embedding }
```

- 能力只用 LiteLLM 标准字段：`mode`、`supports_vision`、`supports_response_schema`、`supports_function_calling`。
- 唯一自定义字段 `tapper_display_name` 可选，缺省显示 `model_name`。

### 角色：后端环境变量

| 变量 | 默认值 | 要求 |
| --- | --- | --- |
| `TAPPER_DEFAULT_CHAT_MODEL` | `qwen-plus` | `mode: chat` |
| `TAPPER_EMBEDDING_MODEL` | `text-embedding-v4` | `mode: embedding`；与 `TAPPER_EMBEDDING_DIMENSION` 共同决定 Milvus 索引，变更需重建索引 |
| `TAPPER_VISION_MODEL` | 空 | 可选；设置时须 `supports_vision: true`；为空则关闭流程图识别 |

删除 `TAPPER_CHAT_ALIAS`、`TAPPER_EMBEDDING_ALIAS`。

### 模型目录加载

- 新增 `modules/ai/adapters/litellm_catalog.py`：`LiteLLMCatalog`，方法 `async routes() -> LiteLLMRoutes`。
  - 首次调用时请求 `GET {base_url}/v1/model/info`，使用后端现有 LiteLLM key；结果缓存 60 秒。
  - 刷新失败时沿用上次成功结果；从未成功则抛 `ModelGatewayUnavailable`，HTTP 层映射为 503 Problem Details。
  - 忽略 `mode` 缺失或不是 `chat`/`embedding` 的条目；同名重复条目（LiteLLM 负载均衡的多个部署）合并为一个模型，能力取交集。
- `LiteLLMModelGatewayConfig` 删除 `chat_alias`、`chat_model`、`additional_chat_models`、`embedding_model` 与硬编码展示名；新增 `default_chat_model`、`embedding_model_name`、`vision_model_name: str | None`；`ChatModelRoute` 删除 `target`。
- `catalog()` 返回 `mode: chat` 的模型（能力：`CHAT`，`supports_response_schema` 时加 `STRUCTURED`）和 embedding 模型（`EMBED`）。
- 角色校验在首次加载与健康检查时进行：角色指向的模型不存在或能力不符时，`/health` 报告 LiteLLM 组件异常并给出原因，相关调用返回 503。
- 后端启动时不连接 LiteLLM。embedding 维度仍由每次响应校验（已有逻辑）。
- 请求的模型名不在目录中时，返回"模型不可用"Problem Details，不回退到其他模型。
- 新增模型流程：改 `config.yaml`、填凭据、重启 LiteLLM；60 秒内出现在前端，后端不重启。

### 实际命中模型：记录而非拒绝

- 删除"响应 `model` / `x-litellm-model-id` / `x-litellm-model-group` 与配置不一致即 `ModelGatewayUnavailable`"的校验，使 LiteLLM 的 fallback 与负载均衡可用。
- `ModelResult.actual_model` 取响应 `model` 字段；`actual_provider` 取其 `/` 前缀（无前缀时为 `unknown`）；`gateway_call_id` 取 `x-litellm-call-id`（已有）。
- 保留：响应体结构、embedding 维度、token 数值范围校验。

### E2E

`DeterministicModelGateway` 使用内置路由（`qwen-plus`、`text-embedding-v4`），不调用 `/model/info`。

## 4. 配置与文档

- `.env.example`：删除 Azure 4 项、Codex 3 项与 `TAPPER_ANSWER_BACKEND`、`TAPPER_OBJECT_STORE_PROVIDER`、`LITELLM_*_MODEL` 5 项、`TAPPER_CHAT_ALIAS`、`TAPPER_EMBEDDING_ALIAS`；新增 `TAPPER_DEFAULT_CHAT_MODEL`、`TAPPER_EMBEDDING_MODEL`、`TAPPER_VISION_MODEL`。
- 新增 `docs/guides/2026-09-29-azure-integration.md`：需实现的端口、需通过的一致性测试、在 `tapper_runtime` 中的接线位置与配置项、tag `azure-reference-2026-09-29` 中旧实现路径、旧实现可借鉴之处（SAS copy、ACL 过滤、四索引 schema）。
- 新增 `docs/guides/2026-09-29-litellm-models.md`：新增模型步骤、`model_info` 字段、命名规则、角色变量、embedding 变更须重建索引。
- 更新 `docs/architecture.md`（基础设施与问答数据流）、`AGENTS.md`（本地服务清单）、README（启动说明加"从旧版本升级先 `make demo-reset`"）。
- 更新 [V1 总纲](../plans/2026-09-29-v1-roadmap.md) 能力 5 工程完成项：追加"每次模型调用持久化模型名、实际上游模型、供应商与 token 用量，历史回答可查看当时的实际模型"。
- 删除前在当前 `main` 上创建 tag `azure-reference-2026-09-29`；推送 tag 前征得用户确认。

## 5. 风险与首步验证

- LiteLLM v1.87.0 的 `/v1/model/info` 是否对非 master key 返回标准能力字段与自定义 `tapper_display_name`：实施第一步用 compose 中的 LiteLLM 实测。若自定义字段不透传，展示名退回 `model_name`；若标准字段不透传，停下并与用户重新确认方案。
- `demo-reset` 当前只带 `--profile milvus`：确认 `tap-minio` 默认启动后其卷随 `--volumes` 删除。

## 6. 验证

- `make check`、`make test` 通过。
- 新架构测试通过；`uv.lock` 无 `azure-*`。
- `rg -i "codex|azurite|azure_blob|AzureBlob" apps/tap-ai-backend/src scripts Makefile compose.yaml .env.example` 无结果（`docs/archive` 除外）。
- `make demo-up` 后 `docker compose ps` 无 azurite；`make demo-check` 通过；`make demo-e2e` 通过。无法在当前环境运行的项如实报告。
- 手动：在 `config.yaml` 新增一个模型并重启 LiteLLM，60 秒内 `GET` 模型目录出现该模型，后端未重启。

## 7. 提交顺序

1. LiteLLM `/model/info` 实测与 `litellm_catalog.py`，模型通路改造与模型改名
2. 删除 Codex 与遗留运行时
3. 创建 Azure 参考 tag；拆出定位符解析；删除 Azure 实现与 V0 门禁
4. compose、Makefile、脚本与 `.env.example` 收敛
5. 文档与总纲更新
