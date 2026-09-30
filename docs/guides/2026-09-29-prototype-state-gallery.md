# 原型状态截图集：Tapper 交互补齐

创建日期：2026-09-29。对应设计：[原型 Tapper 交互补齐设计](../superpowers/specs/2026-09-29-prototype-tapper-interaction-sync-design.md)；基准更新见[产品原型基准规范](2026-09-22-product-prototype-baseline.md#2026-09-29-tapper-交互补齐)。

本页截图使用 `/prototype` 的确定性示例数据（浏览器内样例文档、样例会话与固定人工输入）采集，1280×720 布局视口、2× 像素密度（2560×1440 PNG）。截图仅证明交互在原型中可达，不代表模型、检索、索引或持久化等后端能力已经接通。

截图分两类触发方式：

- **自然**：在 `/prototype` 中正常操作（选择来源、发送问题、打开对话框等）即可到达。
- **注入**：通过 `apps/web/src/widgets/tap/prototype/prototypeFaults.ts` 在页面外经 `window.__TAP_PROTOTYPE_FAULTS__` 注入的故障状态，仅出现在采集脚本 [`prototype-states.spec.ts`](../../apps/web/tests/e2e/prototype-states.spec.ts) 与本截图集中，页面本身不提供触发这些状态的开关。

`b10-sources-processing` 与 `b11-library-empty` 的采集脚本额外通过 `localStorage` 预置一份 `tap.prototype.workspace.v2` 快照（等价于删除全部知识来源后的自然终态），因为在浏览器中逐条删除全部样例来源过于缓慢；页面上仍只能通过"查看 → 删除来源"逐条达成同样的结果。

## A：回答的证据与引用

| 编号 | 交互 | 触发方式 | 截图 |
| --- | --- | --- | --- |
| a1 | 执行记录（检索/筛选/生成步骤）展开 | 自然 | [a1-answer-trace.png](../assets/prototype-states/states/a1-answer-trace.png) |
| a2 | 本次使用的资料与配置展开 | 自然 | [a2-context-used.png](../assets/prototype-states/states/a2-context-used.png) |
| a3 | 引用侧栏（点击引用） | 自然 | [a3-citation-panel.png](../assets/prototype-states/states/a3-citation-panel.png) |
| a4 | 引用失效（来源已更新提示） | 自然 | [a4-citation-stale.png](../assets/prototype-states/states/a4-citation-stale.png) |
| a5 | 引用核验失败 | 注入 · `citation-verification-failed` | [a5-citation-verification-failed.png](../assets/prototype-states/states/a5-citation-verification-failed.png) |
| a7 | 来源冲突（两处引用并列） | 自然 | [a7-conflict.png](../assets/prototype-states/states/a7-conflict.png) |
| a8 | 来源版本已变化，需重新提交 | 注入 · `source-version-changed` | [a8-source-changed.png](../assets/prototype-states/states/a8-source-changed.png) |
| a9 | 部分检索受限提示 | 自然 | [a9-retrieval-limited.png](../assets/prototype-states/states/a9-retrieval-limited.png) |

## B：失败、空状态、加载与重试

| 编号 | 交互 | 触发方式 | 截图 |
| --- | --- | --- | --- |
| b1 | 排队态"等待开始…" | 自然 | [b1-queued.png](../assets/prototype-states/states/b1-queued.png) |
| b2 | 发送失败，草稿保留 | 注入 · `send-failed` | [b2-send-failed.png](../assets/prototype-states/states/b2-send-failed.png) |
| b3 | 停止失败提示 | 注入 · `stop-failed` | [b3-stop-failed.png](../assets/prototype-states/states/b3-stop-failed.png) |
| b4 | 对话更新中断，可重试 | 注入 · `stream-interrupted` | [b4-stream-interrupted.png](../assets/prototype-states/states/b4-stream-interrupted.png) |
| b5 | 模型菜单中的不可用模型 | 自然 | [b5-model-unavailable.png](../assets/prototype-states/states/b5-model-unavailable.png) |
| b5 | 无可用模型，发送按钮禁用 | 注入 · `no-models` | [b5-no-models.png](../assets/prototype-states/states/b5-no-models.png) |
| b7 | 会话历史加载失败 | 注入 · `history-load-failed` | [b7-history-load-failed.png](../assets/prototype-states/states/b7-history-load-failed.png) |
| b8 | 会话历史"加载更多" | 自然 | [b8-history-load-more.png](../assets/prototype-states/states/b8-history-load-more.png) |
| b9 | 删除会话失败 | 注入 · `conversation-delete-failed` | [b9-delete-failed.png](../assets/prototype-states/states/b9-delete-failed.png) |
| b10 | 来源均在处理中 | 自然（`localStorage` 预置终态，见上） | [b10-sources-processing.png](../assets/prototype-states/states/b10-sources-processing.png) |
| b10 | 知识来源面板加载失败 | 注入 · `sources-load-failed` | [b10-sources-load-failed.png](../assets/prototype-states/states/b10-sources-load-failed.png) |
| b11 | Library 空态 | 自然（`localStorage` 预置终态，见上） | [b11-library-empty.png](../assets/prototype-states/states/b11-library-empty.png) |
| b11 | Library 加载失败 | 注入 · `library-load-failed` | [b11-library-load-failed.png](../assets/prototype-states/states/b11-library-load-failed.png) |
| b12 | 上传中（按钮加载、控件禁用） | 自然 | [b12-uploading.png](../assets/prototype-states/states/b12-uploading.png) |
| b12 | 上传失败，对话框不关闭 | 注入 · `upload-failed` | [b12-upload-failed.png](../assets/prototype-states/states/b12-upload-failed.png) |
| b13 | 切片索引失败，可重试索引 | 自然 | [b13-chunk-index-failed.png](../assets/prototype-states/states/b13-chunk-index-failed.png) |

## C：Library 来源管理

| 编号 | 交互 | 触发方式 | 截图 |
| --- | --- | --- | --- |
| c1 | 上传时配置并预览切片 | 自然 | [c1-upload-chunk-preview.png](../assets/prototype-states/states/c1-upload-chunk-preview.png) |
| c3 | 来源详情（含失败文档重试） | 自然 | [c3-source-detail.png](../assets/prototype-states/states/c3-source-detail.png) |
| c4 | 已发布来源图谱为空提示 | 自然 | [c4-graph-empty.png](../assets/prototype-states/states/c4-graph-empty.png) |
| c4 | 知识图谱加载失败 | 注入 · `graph-load-failed` | [c4-graph-load-failed.png](../assets/prototype-states/states/c4-graph-load-failed.png) |

## D：Agent/Skill

| 编号 | 交互 | 触发方式 | 截图 |
| --- | --- | --- | --- |
| d | 内置 Agent 只读（"内置"标记，无编辑入口） | 自然 | [d-builtin-readonly.png](../assets/prototype-states/states/d-builtin-readonly.png) |
| d | 创建技能时的 `SKILL.md` 实时预览 | 自然 | [d-skill-preview.png](../assets/prototype-states/states/d-skill-preview.png) |
