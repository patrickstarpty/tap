# Tapper Skills、Agents 与 MCP

本文描述 V1 的本机能力；不改变 Tapper Demo 的回环绑定与无认证边界。设计见 [Skills/Agents](../superpowers/specs/2026-11-03-skills-agents-design.md)，工程与业务验收分开记录在 [V1 总纲](../superpowers/plans/2026-09-29-v1-roadmap.md)。

## Skill 导入与修订

Skill 是模型可读取的指令，不是可执行插件。导入文件必须是 UTF-8 `SKILL.md`，不超过 64 KiB，并包含 YAML front matter 和非空正文。例如：

```markdown
---
name: release-review
description: 根据已发布资料检查发布说明中的事实与依据。
allowed-tools:
  - knowledge.search
---

先列出与问题有关的事实，再给出逐项带引用的结论。缺少依据时明确说明。
```

`name` 使用小写连字符格式，最多 64 字符；`description` 最多 1024 字符。`allowed-tools` 只记录请求，**不会授予工具权限**；权限由 Agent 白名单和服务端注册表决定。代码块、链接和脚本只作为文本保存；导入不执行脚本，不下载链接，也不接受压缩包。

同一项目中，同名 Skill 的相同最新内容重复导入会返回原修订；内容变化会新增不可变修订，旧对话仍使用原修订。禁用修订后，新对话不能再选择它；原始文件和历史配置仍可读取。重新导入相同的已禁用最新内容不会自动启用，需要明确启用该修订。

对应接口（`{project}` 为当前项目 ID）：

| 操作 | 接口 |
| --- | --- |
| 上传文件 | `POST /api/v1/projects/{project}/ai/skills/imports`，multipart 单字段 `file` |
| 从编辑器创建 | `POST .../ai/skills`，`name`、`description`、`instructions` |
| 管理修订（包括禁用） | `GET .../ai/skills/revisions` |
| 读取完整配置 | `GET .../ai/skills/revisions/{revisionId}` |
| 下载原文 | `GET .../ai/skills/{revisionId}/content`；系统内置修订未保存原文件时返回 404 |
| 启停 | `POST .../ai/skills/revisions/{revisionId}/status`，`status=enabled|disabled` |

## Agent 组合与历史

Agent 组合系统提示词、Skill 修订、工具白名单和可选模型。创建后修改会产生新修订，不覆盖历史。Agent 配置模型时优先于会话模型；未配置时使用会话模型。绑定的 Skills 与会话额外选择的 Skills 按顺序去重，生成 Turn 时保存快照。

- `POST .../ai/agents` 创建，`POST .../ai/agents/{assetId}/revisions` 新增修订。
- 字段为 `displayName`、`description`、`systemInstruction`、`skillRevisionIds`、`toolAllowlist`、`modelAlias`。
- `GET .../ai/agents/revisions` 与 `GET .../ai/agents/revisions/{revisionId}` 返回完整管理配置，包括禁用修订。
- `POST .../ai/agents/revisions/{revisionId}/status` 启停。
- `GET .../ai/agents` 与 `GET .../ai/skills` 仍是供对话选择的启用版本摘要，不包含指令原文。

系统提示词最多 16000 字符，Skills 最多 16 项，工具白名单最多 32 项。`knowledge.answer` 是保留回答能力，创建时自动加入白名单，所以最多还能选择 31 个工具。选中的 Skill 必须存在且启用；模型必须支持结构化回答，使用可调用工具时还需支持工具调用。LiteLLM 的模型元数据须明确声明 `supports_function_calling: true`；缺失时按不支持处理。本地配置中的四个聊天模型已按 [Model Studio 官方支持列表](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling) 声明能力，新增模型仍需核对实际供应商与路由。

## 工具与 MCP

`GET .../ai/tools` 返回内置与已注册 MCP 工具。MCP 工具 ID 为 `mcp.{server_slug}.{tool_name}`；不符合内部命名规则的远端名称会映射为稳定 ID，原名用于远端调用。同步结果会报告跳过的工具。

| 操作 | 接口 |
| --- | --- |
| 读取服务器与工具 | `GET .../ai/mcp-servers` |
| 注册并同步 | `POST .../ai/mcp-servers`，`slug`、`displayName`、`url`、可选 `authHeaderEnv` |
| 重新同步 | `POST .../ai/mcp-servers/{serverId}/sync` |
| 启停服务器 | `PATCH .../ai/mcp-servers/{serverId}`，`enabled` |

仅支持 Streamable HTTP，不支持启动进程的 stdio。默认只允许 loopback URL；确需访问其他主机时，运维人员通过 `TAP_MCP_ALLOWED_HOSTS` 显式追加主机名（逗号分隔），然后重启相应应用进程。该设置只放行 MCP 目标，不会放宽应用监听地址或提供身份认证。鉴权填写环境变量**名称**，实际 Authorization 值由服务端环境提供，不在数据库中保存密钥。

同步最多 10 秒、64 个工具；调用最多 30 秒，返回文本超过 16 KiB 时截断并标记。MCP 的只读声明映射为 `read`，其他工具归为 `external`；只读声明由远端提供，不证明服务器无副作用。只有受信任的 MCP server 应被注册，其工具须逐项加入 Agent 白名单。

模型请求工具时，经服务端重新检查项目范围、启停与白名单后才执行；Skill 中的工具声明和模型返回的工具名都不能扩大权限。有界循环最多 4 个工具步骤，每步最多 3 次调用，预算为模型上下文的 50%；到达上限后停止探索并生成最终结构化回答。工具输出作为不可信资料处理，最终回答仍受引用校验约束。调用链记录工具步骤及耗时，不能据此把本地验证描述为真实模型或企业部署验收。
