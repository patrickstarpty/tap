# Tapper V1 统一 ModelGateway 与模型目录验收

评审日期：2026-09-08。结论：**Task 7 实现与验收通过**。主实现提交为 `e57183da84759cbbb64dacb424c3b260c609e509`，审查修正为 `47607dfa6a29f23ebf0b314e004f459866589308`。独立审查最终为 Critical 0、Important 0；该结论只覆盖统一 ModelGateway、Project 模型目录、Knowledge 调用收口与 legacy Codex 隔离，不代表真实模型质量、V1 出口或生产就绪。

## 实现范围

V1 默认、Validation 和 Product composition 现在共用唯一 `LiteLLMModelGateway`，提供 `catalog`、`chat`、`embed` 和 `generate_structured`。每个请求固定 Project scope、已启用 alias、operation kind、schema/prompt digest、脱敏 context、timeout 和 idempotency key；单一 Adapter 统一处理有界重试、错误归一化、usage 与实际 provider/model 审计。Knowledge 仅保留领域请求/结果映射，不再创建第二个 HTTP client 或 Provider selector。

`GET /api/v1/projects/{project_id}/ai/models` 在服务端 scope 与 `ai.models.read` 授权后返回 alias、display name、capabilities 和默认 alias。生成 OpenAPI/TypeScript 合同与实际 Problem Details 保持一致，不暴露 credential、provider 或 reasoning effort。Web 模型菜单支持 Arrow/Home/End/Enter/Space/Escape/Tab；模型不可用时不静默 fallback，也不允许发送草稿。

`tapper_runtime.py`、默认 API/Worker 和 `make demo-dev` 不再导入或解析直接 Codex Adapter，并对 `TAPPER_ANSWER_BACKEND=codex` fail closed。RFC-006 历史能力只能由 `make legacy-tapper-codex-dev` 在 loopback 启动，不挂载 RFC-009 Project API，也不能转向 LiteLLM 回答路由或计入 V1 证据。

## 审查与修正

首轮独立审查发现 4 项 Important：alias-only 回应可伪装实际模型审计；Python 相等性会把 JSON enum 中的 `true` 与 `1` 混同；catalog 实际 422 Problem Details 与生成合同不一致；真实模型 smoke 仍要求旧 Adapter，启用时必然失败。修正轮以 `16 failed` 复现全部问题，随后通过显式 provider/model mapping、类型敏感的递归 JSON equality、生成错误合同和基于受治理工厂的 always-run smoke setup 回归全部修正。同一审查者复审确认 4/4 已解决，未发现新 Critical/Important。

公共显示名 `GPT-5.6 Sol` 是受治理的逻辑 catalog 标签，不是真实 provider 证据。仓库现有 `dashscope/qwen-plus` 路由未被偷换或重命名，实际 provider/model 只按明确 mapping 与回应审计记录。Task 10 必须在已批准的真实映射上独立通过 `QUALITY-KB-01`；fake、历史 qwen 或 legacy Codex 均不能替代该证据。

## 最终验证

| 检查                            | 实际结果                                                                                                               |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Task 7 字面量定向命令           | Backend 106 passed；Web 19 文件/315 passed                                                                             |
| 审查修正覆盖                    | 205 passed、1 个真实 provider opt-in skip；无 provider 调用                                                            |
| `make contracts` / `make check` | exit 0；Ruff、format、mypy、契约、架构、Web build 与品牌检查通过                                                       |
| 最终完整 Backend                | 2941 passed、118 skipped、6 条既有 Alembic 警告，225.82 秒，exit 0                                                     |
| 最终完整 Web                    | 19 文件、315 passed，exit 0                                                                                            |
| 清理与工作树                    | 独占 MySQL `tap-schema-832ad61e7d2f` 与 Redis `tap-task7-tests-07e34e8fb67e` 精确清理；只保留既有未跟踪 `node_modules` |

最终完整日志 SHA-256 为 `9e9b255834e71791abaf2ce8948f27373a655dff42bfa159a48dc04ff4b64b4d`。118 项跳过包含未显式启用的真实模型、Milvus/Parser/对象存储独立门禁及由独占配方验收的集成用例；本轮没有把 skip 计为通过证据。六条 Alembic `path_separator` 弃用警告早于本任务，本轮如实保留，未扩大范围清理。

本轮没有调用真实模型、修改 provider credential/路由、操作共享/default 服务或宣称 V1 质量出口。Task 7A 承接已批准 Agent/Skill Revision，Task 8–10 继续完成持久 Conversation、真实产品接线和 V1 质量门禁。
