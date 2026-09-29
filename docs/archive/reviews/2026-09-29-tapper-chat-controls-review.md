# Tapper 对话控制（TAP-8517）验收

日期：2026-09-29。结论：**通过**。范围为 TAP-8517 列出的 9 项对话入口：重命名对话、删除对话、重新生成回复、取消生成、发送按钮状态、对话历史搜索、编辑用户问题、文件上传/附件、失败重试。覆盖 TAP AI 前端、`apps/web` `/prototype` 设计基线与 TAP AI 后端。

## 设计决定

| 项 | 决定 |
| --- | --- |
| 重命名、删除 | 新增 `PATCH`／`DELETE /conversations/{id}`，仅会话创建者可操作（否则 403）。删除为软删除（迁移 `0024_conversation_history` 增加 `deleted_at`、`deleted_by`）：先取消进行中的回合，保留回合与证据；删除后读取、追加、事件、流、引用、取消和重命名均返回 404，worker 不再领取其回合。 |
| 历史搜索 | 会话列表增加 `q`：标题不区分大小写的子串匹配，LIKE 通配符按字面处理，可与游标分页组合。前端用单独查询驱动侧边栏，不改动主会话列表。 |
| 重新生成、失败重试、编辑问题 | 回合不可变。三者都以原问题（编辑时为修改后的问题）和原回合的来源、Agent、Skill 追加一个新回合，复用现有发送校验，不改写历史。原先的 “Retry” 只重连事件流，现改为真正重新发送。 |
| 取消生成、发送状态 | 生成中用“停止生成”替换发送按钮；发送中按钮显示加载状态并禁用（`aria-busy`）。同一待确认发送复用幂等键，网络模糊失败后重发会回放，不产生重复回合。 |
| 附件 | 复用 Library 上传（pdf、docx、md、txt，25 MB），不引入会话临时附件。附件显示处理状态；Library 审核发布后自动加入本轮来源，发布前显示“需审核发布后使用”和“去知识库审核”。 |

## 验证

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| 真实浏览器旅程 | 在隔离 `tap-flowchart-uat` 栈、真实 LiteLLM（Qwen Plus）与已发布来源上，按顺序执行发送状态、停止生成、停止后重试、重新生成、编辑问题、历史搜索、重命名（刷新后保留）、删除（刷新后消失，`GET` 返回 404）、上传附件至“需审核发布后使用”。9 项全部通过。 | 通过 |
| 设计基线 | `/prototype` 同步增加 9 项交互；1280 × 720、2× 前后截图对照，并核对 Test Management、Low Code Automation、Test Analytics、Tapper、Agents、Skills、Library、Knowledge Graph 导航，一条 Tapper → Test Plan → Low Code 旅程与浮动助手交接。 | 通过 |
| 自动化 | 后端单元、契约、架构测试与隔离 MySQL 集成测试通过；`make check` 通过；TAP AI 前端 459 项、`apps/web` 101 项、`apps/backend` 116 项通过。完整 `pytest tests` 中 31 项失败来自本机默认 3306 数据库表结构过旧，相应 3 个文件在隔离新建 MySQL 上 53 项通过。 | 通过 |

真实旅程在合并了 [流程图 PR](https://github.com/patrickstarpty/tap/pull/27) 的临时集成工作树中执行：`main` 上带回答计划的知识回答会被 Gateway 以受治理请求拒绝，该修复在 #27 中，需先合并 #27 才能在 `main` 上获得真实模型回答。两分支自动合并无冲突，合并后契约与前端测试一致。

## 截图

- 设计基线历史：[修改前](../../assets/tapper-chat-controls/2026-09-29-prototype-history-before.png)、[修改后](../../assets/tapper-chat-controls/2026-09-29-prototype-history-after.png)
- 设计基线回答：[修改前](../../assets/tapper-chat-controls/2026-09-29-prototype-answer-before.png)、[修改后](../../assets/tapper-chat-controls/2026-09-29-prototype-answer-after.png)
- TAP AI 前端：[停止生成](../../assets/tapper-chat-controls/2026-09-29-tap-ai-stop-generating.png)、[历史菜单](../../assets/tapper-chat-controls/2026-09-29-tap-ai-history-menu.png)、[附件](../../assets/tapper-chat-controls/2026-09-29-tap-ai-attachment.png)

## 已知限制

- 降级迁移会删除软删除列，已删除会话会重新出现。
- 删除进行中时追加的回合保持排队且不会被领取；此窗口内被领取的回合在 worker 发现会话已删除时跳过。
- 附件标签只存在于当前页面；上传的文件本身保留在 Library。
- 重命名和删除的权限依据会话创建者；本地演示只有一个固定身份。
