# Tapper V0 Project 接口与验证模式验收

评审日期：2026-09-06。结论：**Task 3 实现与定向验收通过**，提交 `7395d70`，独立复审 Approved。完整 Backend 回归唯一旧断言失败已修正并通过定向验证；保留原始失败结果，不宣称最终源码完整 suite 已重跑。V0 完整出口尚未通过。

## 实现范围

- Knowledge 正式路径为 `/api/v1/projects/{project_id}/knowledge/...`，运行环境路径为 `/api/v1/runtime-mode`。旧 `/v1/knowledge/...` 与原 citation 地址仅由 Validation runtime 以 deprecated aliases 装配，映射同一守卫；默认离线契约只导出正式路径。
- HTTP 从可信 ScopeProvider 与共同 AuthorizationPolicy 取得固定验证范围，并核对实际 Document/Answer/Citation repository 的绑定。Project 不匹配、Header/Cookie/query/JSON/multipart 中的身份覆盖均在业务和 Provider I/O 前拒绝。
- 所有 mutation 校验精确 Origin，允许值仅来自已验证的 loopback 配置；拒绝 missing/null/duplicate/malformed/wrong-port，忽略 Host/forwarded 的权威性。纯 ASGI 中间件保留 cancellation，拒绝响应的 body/header correlation 一致。
- Web 取得服务端 runtime DTO 后才创建 Project Knowledge client；URL、query/mutation key、optimistic overlay 均包含 Project。延迟完成的旧 Project mutation 不会写入新 Project cache。
- 沿用 `App → TapperPage → TapProductPrototype` 的 FWD 浅色页面。验证提示永久显示“操作统一记录到固定 Validation Actor，不代表个人身份”；连接中/失败时保留导航，禁用服务器依赖。手机 rail/drawer 与提示区不重叠。旧 TapperWorkspace 仅更新调用兼容签名。

## 验证证据

| 检查                                                      | 实际结果                                                                            |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 最终字面量 Project/Origin contract 选择                   | 41 passed，exit 0                                                                   |
| HTTP/service/cancellation 组合                            | 105 passed；runtime 选择另有 7 passed                                               |
| 实际 repository 错装范围回归                              | 4 例 RED 503 后 GREEN 403，sentinel 验证零 session/authority 调用                   |
| Project client/cache 与兼容组件                           | 104 passed；包含延迟 retry 跨 Project 完成场景                                      |
| 完整 Web suite                                            | 285 passed，17 files；在最后 ID 长度修正前执行                                      |
| 最后 runtime ID 长度修正                                  | 129 字符 Project/Actor RED 2 failed；128 接受、129 拒绝及 providers GREEN 12 passed |
| Vite 契约修正                                             | 2 passed，86 deselected；检查 /api 与 changeOrigin=false                            |
| 原型浏览器检查                                            | 22 passed，刷新原有 40 张 JPG；包含桌面/390px、reduced-motion、运行环境失败导航     |
| `make contracts`、修正后 `make check`、`git diff --check` | passed                                                                              |

完整 owned Backend 回归原始结果为 **2535 passed、9 skipped、1 failed**，6 条已有 Alembic path_separator 弃用警告，489.61 秒，exit 1。唯一失败是 `test_vite_config_is_strict_and_exposes_only_same_origin_api_proxies` 的旧预期列表，修正后的两项 Vite 测试通过。该运行已载入旧断言，最后 ID 长度修正另由 12 项 runtime/provider 测试覆盖；没有重跑最终源码完整 Backend 或 Web suite。九项跳过是明确 opt-in 的真实 Milvus/Entra/Azure、持久化 E2E 阶段、Codex capability 和真实模型门禁，不作为通过证据。此前 Task 2B supervisor 与 Task 2C runtime 旧断言场景本轮均未再失败。

所有真实测试使用独占的一次性 MySQL/Redis/Azurite。runner 返回 `cleanup_complete`，另外按 Compose labels 检查该次项目的 container/volume/network 均为零；本机 receipt 为 `/private/tmp/tap-task3-cleanup-receipt.json`。未操作默认 Demo、真实模型、Milvus Provider、Recorder/Jenkins 或生产资源。

截图以明确的 runtime/document fixture 驱动，用于页面与交互检查，不代表真实后端或业务质量验收。实际 15175 预览在检查时 runtime 不可用，界面如实显示连接失败且可以切换模块。控制器已查看桌面、手机提示布局和手机断开状态。示例图片见[新对话](../assets/prototype-demo/01-tapper-new-chat.png)和[Library](../assets/prototype-demo/14-tapper-library-all.png)。

## 审查与验证边界

初审发现新增 `/api` 代理与旧测试的精确列表不一致；修正保留 loopback、strictPort、target 并补充 Origin 保留断言。另一项 runtime 客户端最大长度原为 256，现与 Backend 128 一致，边界测试已通过。截图进程曾报告 `NO_COLOR`/`FORCE_COLOR` 冲突；这是执行环境警告，不影响截图判定，后续 capture 统一清理冲突变量。既有样式检测的两条 legacy border 警告不在本次修改范围。

旧独立知识页的 `tapper.spec.ts`、`persistence.spec.ts` 未作为本项浏览器证据。它们包含旧 UI selector、旧 response waiter 和缺 Origin 的写请求，需要在 Task 5 对象存储 E2E 接入时按当前产品入口调整。不得把本项原型 fixture 当作这些真实持久化/重启旅程通过；V0 gate 仍要求相应真实隔离证据。

运行环境提示只说明固定 Validation Actor，不代表个人认证。原型本地 Test Plan/Automation/Conversation 行为仍为原型能力，本项没有把它们接成真实工作流。后续 Task 3A 实施 Project Audit；恢复、MinIO、Parser 与 V0 完整出口尚未完成。RFC-009 保持 accepted，实施计划保持 active。

本机日志位于 `/private/tmp/`：`tap-task3-backend-full.log`、`tap-task3-owned-runner.log`、`tap-task3-backend-final-selection.log`、`tap-task3-web-full.log`、`tap-task3-capture.log`、`tap-task3-runtime-bounds-red.log`、`tap-task3-runtime-bounds-green.log`、`tap-task3-vite-contract-green.log`、`tap-task3-fix1-check.log`。临时日志不是永久 CI 制品；稳定结果和命令保存在本记录及[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)。
