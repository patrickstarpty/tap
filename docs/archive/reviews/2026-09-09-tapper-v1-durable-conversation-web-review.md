# Tapper V1 真实 Conversation Web 接线验收

评审日期：2026-09-09。结论：**Task 9 实现与验收通过**。主实现提交为 `2fc555ae996cb5c2e4e39d547889acde467167e4`，两轮根级审查修正最终至 `b240c6f61fe8bee96078f6f6bf6b796662588f1e`。独立审查最终为 Critical 0、Important 0、Minor 0；该结论覆盖 Tapper 产品壳的真实 Conversation、SSE、Context 与 Citation 接线及隔离 E2E，不代表 Task 10 真实模型质量门禁或 V1 出口通过。

## 实现范围

默认 Tapper 页面使用生成合同连接 Project-scoped Source、AI Agent、Skill、model、Conversation、SSE 与 Citation API。内置 Prototype 数据只存在于显式 fixture mode，事件缺失、加载或失败不会回退为模拟回答。Conversation history 支持分页、首条消息创建、后续 Turn append、刷新和跨模块恢复；不可变 Input view 恢复 model 与 Source/Document/Agent/Skill 的历史标签，同时不暴露 prompt、ACL 内部、credential、provider 或治理 secret。

SSE client 使用 `Last-Event-ID` 和恢复事件最大 sequence 续接，处理分片与 CRLF、去重、断线重试和取消。重试只覆盖可重试网络/服务错误，权限与不存在错误进入可见恢复态；所有 Turn 终态后停止 stream 与轮询。多轮 Conversation 以目标 active Turn 判断终态，历史 Turn 的 completed/canceled 不会截断新 Turn，当前 response 的完整 batch 在停止前全部消费。

历史 Citation 通过 Conversation、Turn、Project、Evidence digest 与 Artifact Link 授权的不可变预览读取。Source 删除后，既有 Turn 的 quote、原文 preview 与 deep link 仍可核验；被删除/禁用的 Source、Agent 或 Skill 不进入未来 Turn。Composer 同步防重复发送，上箭头只召回输入而不自动提交，Stop 控件走真实 cancel API。

## 产品与 E2E 证据

既有 TAP 浅色视觉、一级 Rail、Tapper 二级 Sidebar、Context chips、composer、minimap、收展与模型菜单键盘语义保持不变。唯一一次 Impeccable detector 对修改目标返回空结果；本轮是数据与状态接线，不做视觉重设计。

`knowledge-conversation.spec.ts` 通过页面完成 Source/Agent/Skill 与 model 选择、首发、第二轮 append、非零 `Last-Event-ID` 续流、真实回答/Citation、Stop、Library 跨模块和 reload。应用与 Compose 重启后，页面从 History 恢复 Conversation、消息、model、Context、answer 与 Citation；owned 数据库受控禁用资产并删除 Source 后，页面仍显示历史事实，未来 Turn 只提交仍可用的新 Source。核心 UI 断言不以 `page.request` 代替。

E2E manifest 按动态声明用例数验收，要求 zero unexpected、flaky 和 skipped。三项浏览器规格、应用重启、Compose 重启及 28 项持久化核验全部通过，专属 `tap-tapper-e2e` 资源与锁已清理。

## 审查与修正

根级初审发现 5 项 Important：事件错误时仍可显示 fixture 回答、历史上下文未恢复、SSE 对非重试错误持续请求、Source 删除使历史 Citation 失效，以及重启旅程未证明完整生命周期。第一轮修正关闭这些问题后，复审又发现多轮 SSE 从 cursor 0 回放时会被旧 Turn 终态提前截断。

第二轮以“历史 Turn 终态 + 新 Turn 事件”复现并修正：hook 显式接收目标 Turn 和初始 cursor，按每个 Turn 的 `lastSequence` 合并 detail、恢复页与实时流。相同审查者确认多轮续流、完整 batch、断线去重、终态轮询关闭及页面级第二轮回答/Citation 均已覆盖，最终规格与代码质量 Approved。

## 最终验证

| 检查               | 实际结果                                                                                            |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| `make demo-e2e`    | 3 个动态浏览器 spec、应用重启、Compose 重启全部通过；持久化 verify 28 passed、2 warnings            |
| 最终完整 Backend   | 2975 passed、139 skipped、6 条既有 Alembic warnings                                                 |
| 最终完整 Web       | 25 files、354 passed                                                                                |
| Task 9 定向覆盖    | 最终多轮 stream/reducer 12 passed；修正前扩展覆盖 80 passed                                         |
| `make check`       | Ruff、format、mypy、shell、contracts、TypeScript drift、Web lint/build、architecture 与品牌检查通过 |
| UI detector / diff | Impeccable detector `[]`；`git diff --check` 通过                                                   |
| 清理               | owned E2E/数据库资源和锁为零；只保留既有未跟踪 `node_modules`                                       |

本轮没有把浏览器 localStorage、fixture 回答、后台 API 轮询或 fake Citation 计为产品证据。Task 10 继续在批准的真实 provider/model 映射上运行固定质量集、预算/超时/重试审计和 V1 gate。
