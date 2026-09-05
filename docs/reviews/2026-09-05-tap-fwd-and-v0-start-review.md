# TAP 原型浅色改造与 V0 启动验收

评审日期：2026-09-05。结论：**本次启动增量通过**；UI 完成，V0 Task 1 完成。V0 其余任务、VG 与生产化尚未完成。

## 基线与范围

- Planning baseline：`a54ab433eae52500683a5ff6ff9d79466a30e1ca`；父工作区计划、RFC-009、架构、核心契约和 ADR-020–025 无差异后，创建 `codex/tapper-platform-v0` 独立 worktree。
- [RFC-009](../proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md) 与[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)补充 TAP / Tapper 品牌层级、当前运行命名空间、FWD 浅色规则和启动交付边界。计划保持 `active`，不把 55 项平台任务整体标为完成。
- 视觉唯一基线是此前确认的 `TapProductPrototype`：双层导航、Tapper 对话、Library/Graph、Agent/Skills、Test Management、Low Code Automation。用户明确要求不基于旧独立知识页改造；临时旧页检查入口已删除，旧 `.tapper-*` 专属样式修改已撤回。
- V0 Task 1 的实现提交：`ad3cf99`。没有更改 API、生成合同、迁移 revision、默认服务配置或生产边界。

## 已交付

### 现有 TAP 产品原型

共享 CSS tokens 与 Ant Design theme 使用暖白、白色、深灰与克制橙色。主要按钮以深墨文字搭配橙色；欢迎和工作区标题使用无衬线。图谱、代码与日志使用浅色，保留图谱社区色与业务状态语义。组件细节与规则记录在 [DESIGN.md](../../apps/web/DESIGN.md) 和[视觉规范](../reference/2026-09-05-tap-fwd-light-design.md)。

来源面板在 1100px 以下默认收起为抽屉，避免三栏挤压输入区；导航在 640px 以下延续原有抽屉。新测试先证明 1024px 下来源仍常驻导致失败，再验证展开、inert、Escape 与焦点恢复。保留双语、模型、会话、上下文、资产链接和模拟执行行为。

40 张[客户演示截图](../reference/2026-09-04-customer-prototype-demo-guide.md)已全部从最终原型重新采集。用户已有未跟踪品牌 PNG / SVG 文件未纳入或删除。

### V0 Task 1

Alembic 从明确的 `load_authoritative_metadata()` 获取独立 metadata，包含现有 14 张 Outbox/Chat/Knowledge/Projection 表；不再依赖运行时偶然 import。Projection ORM 补齐既有迁移中的唯一约束和五个时间戳默认值，没有新增迁移。

`make schema-drift` 与 `make migration-check MIGRATION=0005_projection_lineage` 启动唯一命名的一次性 MySQL project/database，只发布 loopback 端口，拒绝调用者数据库 URL、远程 Docker context 和非字面量 revision，退出和信号中断只清理自身资源。固定旧数据 fixture 覆盖所有 14 张表，并校验升级前后值保持。

## 验证证据

| 检查                                                                  | 实际结果                                                                                                           |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Task 1 窄测试（metadata / drift / upgrade）                           | 21 passed；已观察缺 registry/harness 的 RED，及安全边界追加测试的 RED → GREEN                                      |
| 模块依赖边界                                                          | 79 passed                                                                                                          |
| `make migration-check MIGRATION=0005_projection_lineage`              | passed；14 张表各保留非空 fixture 行                                                                               |
| `make schema-drift`                                                   | passed；14 张表，差异为空                                                                                          |
| `make check`                                                          | exit 0；Ruff、mypy、契约、Web 构建与品牌检查通过                                                                   |
| 最终 Web check                                                        | exit 0；lint、format、architecture、TypeScript/Vite build 通过                                                     |
| 最终 `corepack pnpm --filter @tap/web exec vitest run --maxWorkers=2` | 14 files / 250 passed                                                                                              |
| 最终 `corepack pnpm --filter @tap/web run prototype:capture`          | 20 passed；40 张截图完整且字节互异，含 1280×720 / 390×844 reduced-motion 检查                                      |
| 浏览器人工检查                                                        | 原型桌面、390px 移动端与 1024px 中等屏幕；1024px 时来源默认关闭、输入区约 613px、页面 scrollWidth = viewport width |
| Impeccable detector                                                   | 本轮机械扫描返回空 findings 数组                                                                                   |
| 独立代码/视觉评审                                                     | 未发现后端隔离、数据保持或响应式缺陷；主按钮 pressed 对比度问题已修正并复核为 4.89:1                               |
| 文档与 diff                                                           | 修改文档的相对链接、格式、品牌检查与 `git diff --check` 通过                                                       |

### 全量 Backend 回归的环境修正

全量 Backend 使用一次性 MySQL、Redis、Azurite，并显式启用三类集成测试；没有使用默认 Demo 数据库。首次输出为 `2293 passed, 3 failed, 9 skipped`。三项失败都属于 Azurite：其服务端 copy 读取宿主 loopback URL 时，临时容器内外端口不同导致 deadline，随后留下 staging 数据使 scavenger 断言失败。

仅修正临时测试 wrapper 的 Azurite 内外端口映射，复跑整个 `apps/backend/tests/integration/test_azurite_artifacts.py` 得到 `7 passed`，清除了这三项失败；没有修改业务代码，也没有把首次全量命令写成一次全绿。复跑命令使用独立 wrapper 启动已销毁的临时服务，再执行：

```sh
TAP_RUN_MYSQL_INTEGRATION=1 TAP_RUN_REDIS_INTEGRATION=1 TAP_RUN_AZURITE_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests -v
TAP_RUN_AZURITE_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_azurite_artifacts.py -v
```

以上命令必须由隔离 wrapper 注入该次服务的数据库、Redis 与 Blob endpoint，不能直接对默认服务执行。wrapper 只作为本次验证工具，不是新的仓库产品脚本。

9 个 skip 来自四个显式 Milvus 集成模块、Entra policy、Azure ACL、隔离持久化 E2E、Codex conformance smoke 和真实模型 smoke；相应 opt-in 未打开。首次高并发 Web 运行也出现过既有上传进度断言的时序波动，最终使用两个 worker 的完整 250 项回归通过，未修改该上传行为或测试。

## 未运行与完成边界

本次没有运行 `make demo-e2e`、真实模型、Milvus 完整门禁、Jenkins/Recorder、容量或生产化验证。V0 Task 1 的真实数据库门禁已完成，但不是 V0 里程碑出口；下一平台任务仍是 Task 2A。RFC-009 保持 `accepted`，默认界面仍为原型，Run 保持 `Simulated`。
