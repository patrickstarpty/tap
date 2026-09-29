# LiteLLM 模型目录指南

创建日期：2026-09-29。模型目录唯一来源是 `deploy/local/litellm/config.yaml`；后端在启动时不连接 LiteLLM，改用 `modules/ai/adapters/litellm_catalog.py` 的 `LiteLLMCatalog` 在首次调用时读取 `GET /v1/model/info` 并缓存。背景见[基础设施收敛设计](../superpowers/specs/2026-09-29-infra-convergence-design.md)。

## 新增模型三步

1. 在 `deploy/local/litellm/config.yaml` 的 `model_list` 追加一条 `model_name`/`litellm_params`/`model_info`。凭据只放 `.env`（`litellm_params.api_key`/`api_base` 用 `os.environ/...` 引用）。
2. 重启 LiteLLM（例如 `docker compose restart litellm`）。
3. 无需改后端代码或重启后端：60 秒内 `LiteLLMCatalog` 的下一次目录刷新会带出新模型；`mode: chat` 且 `supports_response_schema: true` 的模型即可在会话中选择。

## `model_info` 字段表

| 字段 | 含义 |
| --- | --- |
| `mode` | `chat` 或 `embedding`；缺失或非此二值的条目会被目录忽略 |
| `supports_vision` | 是否支持图片输入；`TAPPER_VISION_MODEL` 指向的模型须为 `true` |
| `supports_response_schema` | 是否支持结构化输出 schema；知识问答与 Agent 回答都走结构化输出，chat 模型须为 `true` 才会出现在会话模型选择器中，`TAPPER_DEFAULT_CHAT_MODEL` 与 `TAPPER_VISION_MODEL` 也须为 `true` |
| `supports_function_calling` | 是否支持函数/工具调用 |
| `tapper_display_name` | 唯一的 Tapper 自定义字段，可选；前端展示名，缺省回退为 `model_name` |

同名的多个部署条目（LiteLLM 负载均衡的多副本）会合并为一个模型：`mode` 必须一致，能力字段取交集，`tapper_display_name` 取第一个非空值。

## 命名规则

`model_name` 即模型名，按模型命名：全小写、连字符，`<模型家族>-<型号>`，不含角色或供应商账号。上游模型直接写在配置中，`.env` 只放凭据。

目录遵守公共契约上限：`model_name` 须匹配 `^[a-z0-9]+(?:[.-][a-z0-9]+)*$` 且不超过 128 个字符，展示名不超过 128 个字符，按 LiteLLM 返回顺序最多保留前 32 个 chat/embedding 模型。不符合的条目被跳过，其余模型照常可用，就绪状态保持正常：`/health/ready` 的 models 组件仍为 `ok`，只在 `detail` 中以 `LiteLLM model skipped: <name> (<原因>)` 提示被跳过的条目。若被跳过的是角色模型（`TAPPER_DEFAULT_CHAT_MODEL`、`TAPPER_EMBEDDING_MODEL`、`TAPPER_VISION_MODEL`），则按角色问题处理：models 组件失败，`detail` 为 `TAPPER_<角色>_MODEL=<name> was skipped: <原因>`。

## 角色变量

| 变量 | 默认值 | 要求 |
| --- | --- | --- |
| `TAPPER_DEFAULT_CHAT_MODEL` | `qwen-plus` | 目录中必须存在，`mode: chat` 且 `supports_response_schema: true`；新会话默认选中该模型 |
| `TAPPER_EMBEDDING_MODEL` | `text-embedding-v4` | 目录中必须存在，且 `mode: embedding` |
| `TAPPER_VISION_MODEL` | 空 | 可选；设置时必须 `mode: chat`、`supports_vision: true` 且 `supports_response_schema: true`；为空则关闭流程图识别 |

角色校验在首次加载目录与健康检查时进行：角色指向的模型不存在或能力不符时，`/health/ready` 的 models 组件报告异常并在 `detail` 中给出具体原因，相关调用返回 503。

更换 embedding 模型须重建 Milvus 索引：`TAPPER_EMBEDDING_MODEL` 与 `TAPPER_EMBEDDING_DIMENSION` 共同决定 Milvus 索引的向量空间，换模型后旧索引不可直接复用。

## 目录 60 秒缓存

`LiteLLMCatalog.routes()` 首次调用时请求 `GET {LITELLM_BASE_URL}/v1/model/info`（请求超时默认 5 秒），结果缓存 60 秒；缓存内的后续调用直接复用，不重新请求。模型调用刷新失败时沿用上一次成功的结果；如果从未成功获取过，则抛出 `ModelGatewayUnavailable`，HTTP 层映射为 503 Problem Details。健康检查使用 `fresh_routes()` 每次实时请求目录，不使用缓存，因此 LiteLLM 停止后 `/health/ready` 会报告 `LiteLLM model catalog unavailable`。目录同时维护一份 `model_info.id → litellm_params.model` 的部署映射，用于把响应头 `x-litellm-model-id` 换算成实际命中的上游模型（只读缓存，不在模型调用成功后再请求目录），记录在 `ModelResult.actual_model`/`actual_provider` 中，不因为命中了 fallback 或负载均衡的另一个部署而拒绝请求。
