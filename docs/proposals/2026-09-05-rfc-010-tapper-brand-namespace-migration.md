---
id: RFC-010
status: accepted
date: 2026-09-05
related-adrs:
  - ADR-026
---

# RFC-010：Tapper 品牌与运行命名空间迁移

## 摘要

TAP 继续作为平台品牌，智能工作区统一命名为 Tapper。本次迁移采用验证阶段的 clean cut：当前工作树内的产品文案、源代码符号、配置键、运行标识、文件路径、文档、图表和客户截图一次性切换到 Tapper，不保留旧命名兼容层，也不迁移本地验证数据。

更名前的字节级历史事实固定在 Git commit `0eab801`。本次迁移不改写 Git 历史、不主动删除旧 Compose 卷或外部存储；新版本只使用 Tapper 命名空间重新建立可重建状态。

## 背景

当前实现把产品名称写入了 UI 文案、React/Python 符号、环境变量、进程入口、脚本、Compose project、Blob container、Milvus collection/alias、Redis consumer group、版本标识、测试、文档和截图。只替换界面文案会留下相互矛盾的配置与客户材料；盲目保留旧持久化标识又会让验证阶段长期承担无价值的兼容复杂度。

仓库已有 Tapper 的方形 mark 与横向 wordmark，包含 `ink`、黑色、橙色和白色变体。当前产品壳是浅色、低饱和、Codex 式操作界面，`ink` 版本与现有视觉层级最一致。

## 目标

- 明确品牌层级：TAP 是平台，Tapper 是平台内的智能工作区与 AI Agent 入口。
- 让当前工作树中的用户可见内容、活动技术命名空间和受版本控制路径统一使用 Tapper。
- 使用现有 Tapper SVG 品牌资产，保持一级产品栏、二级工作区栏和内容区的现有布局与交互。
- 让配置、运行时、测试、文档、图表和演示截图在一次提交序列内保持一致。
- 用自动化守卫阻止已退役名称重新进入受版本控制的文字或路径。

## 非目标

- 不重写 `.git` 历史。
- 不删除旧 Compose 卷、对象容器、Milvus collection 或 Redis 状态。
- 不为旧环境变量、Python import、脚本路径或持久化 ID 提供 alias、dual-read 或 backfill。
- 不改变现有 provider-neutral HTTP 路由、OpenAPI operation ID、`tap` Python package、`knowledge_*` 数据表或 TAP 平台名称。
- 不借品牌迁移重做页面布局、颜色系统、交互流程或产品能力。
- 不在本次迁移中实现认证、RBAC、Mobile Automation 或生产数据迁移。

## 方案

### 品牌层级与资产使用

| 位置                     | 内容                  | 实现约束                                                                                     |
| ------------------------ | --------------------- | -------------------------------------------------------------------------------------------- |
| 最左侧产品栏顶部         | `TAP` 平台标识        | 延续现有深色方形容器，以完整 `TAP` 字样替代单字母；可访问名称为 `TAP platform`。             |
| 一级产品栏 Tapper 入口   | Tapper `ink` mark     | 使用 `tapper-mark-ink.svg`，替代字母占位符；图像本身装饰性隐藏，按钮提供 Tapper 可访问名称。 |
| 展开的 Tapper 二级栏标题 | Tapper `ink` wordmark | 使用 `tapper-wordmark-ink.svg`，保持标题高度和折叠按钮位置；不得重复显示文字标题。           |
| 收起状态、窄屏和图标语境 | Tapper mark           | 保持方形比例、清晰焦点和至少 24px 可辨尺寸。                                                 |
| 深色背景候选             | Tapper white 版本     | 仅在实际深色背景需要时使用；本次不引入橙色品牌强调。                                         |

只纳入实际使用的 SVG 源文件；PNG 导出和 `.DS_Store` 不进入版本控制。SVG 使用显式尺寸，外层交互元素承担 `aria-label`，避免重复朗读内嵌品牌名称。

### 代码与配置命名

- React 页面、组件、类型、模块 key、CSS/DOM selector、测试和 E2E 文件全部改为 Tapper 命名。
- Python entrypoint、settings/runtime 类型、Milvus adapter 类型、确定性模型、测试和脚本全部改为 Tapper 命名。
- 活动环境变量统一使用 `TAPPER_*`、`TAP_TAPPER_*`、`LITELLM_TAPPER_*` 与 `TAP_RUN_TAPPER_*`；冲突或旧键不做兼容解析。
- 默认 Compose project 使用 `tap-tapper-demo`；reset 安全闸只接受这一精确名称与新的显式许可变量。
- LiteLLM alias、Blob container、Milvus collection/alias、Redis group、lock name、policy/version/corpus/worker ID 和内部 fence value 一次性切换到 Tapper 命名。
- Alembic `0003` revision 与文件名同步切换；已有本地数据库被视为不可升级的验证状态，必须使用新命名空间重新初始化。
- provider-neutral HTTP API、数据库表、领域概念与 `tap` package 保持不变，避免把品牌写入稳定业务边界。

### 文档与历史事实

- README、PRODUCT、AGENTS、当前架构、RFC、ADR、Plan、Review、Reference、Mermaid、Draw.io/SVG 和索引统一采用 Tapper。
- 文件名中的退役名称使用 `git mv` 迁移，并一次性修复所有相对链接、锚点、命令和索引；不创建重定向页、符号链接或重复正文。
- 受治理约束的历史文档只做术语与仓库标识归一化，不改变日期、状态、决策、范围或评审结论。受影响文档增加标准说明：当前文本是 2026-09-05 的命名归一化转写，字节级原文以 commit `0eab801` 为准。
- 原先声称 byte-exact 的证据块在改名后标记为 identifier-normalized transcription；不可继续用旧 digest 声称校验当前文本。

### 图片与演示材料

- 40 张客户演示截图全部从改名后的原型重新截取，而不是只改文件名；所有页面都要检查一级栏、二级栏、折叠状态和可访问名称。
- 文件名中包含退役名称的截图同步改为 `tapper`；其余截图保留原编号与主题名。
- 旧视觉探索图若仍被当前 Impeccable 配置引用，则生成 Tapper 版本并更新引用；若已不再提供当前设计事实，则从当前工作树删除，历史仍可由 Git 读取。
- Draw.io 源文件和导出的 SVG 必须同时更新，避免源与导出物漂移。

### 迁移顺序

1. 增加 Tapper 品牌与命名空间的自动化失败测试，证明旧文字、路径和 UI 标识仍存在。
2. 纳入所需 SVG，更新 Web 产品壳、文案和测试。
3. 原子迁移后端符号、entrypoint、脚本、配置与运行命名空间。
4. 迁移测试、fixture、部署文件和 Alembic 验证链。
5. 迁移规范文档、历史记录、文件路径、索引、图表与引用。
6. 重新生成全部客户截图与仍在使用的设计参考图。
7. 运行零残留、全量构建测试、Demo E2E、文档治理和视觉检查。

## 替代方案

### 只改用户可见文案

改动最小，也能快速改变演示效果，但代码、配置、日志、文件路径和客户文档会继续暴露互相冲突的名称，无法满足完整更名。

### 分阶段兼容迁移

先引入 Tapper 主键并兼容旧环境变量、进程入口和存储 ID，再在后续版本清理。它适合已有生产数据与外部调用方的系统，但会引入 dual-read、alias、回填和滚动部署复杂度；当前仍处于快速验证阶段，没有足够收益。

### Clean cut（采用）

一次性更改当前工作树和活动运行命名空间，以新命名空间重建本地验证状态。它最符合当前阶段和零残留目标，代价是旧本地状态不能直接升级。

## 风险与缓解

- **本地数据看似丢失**：新 Compose project 与存储标识不会读取旧数据；README 和启动检查必须明确这是预期的验证环境重建，旧卷不会被自动删除。
- **遗漏隐藏引用**：同时扫描受版本控制内容与路径，覆盖大小写变体、二进制文件名、图表源和生成物。
- **历史证据失真**：保留 pre-migration commit，并给被归一化的历史记录增加统一说明，不改写状态与结论。
- **Logo 在窄栏不可辨**：一级栏只用方形 mark，横向 wordmark 只进入有足够宽度的二级栏。
- **品牌层级混淆**：最左上角只表达 TAP，Tapper mark/wordmark只表达智能工作区，禁止互换。
- **截图与实现漂移**：40 张演示截图统一从最终构建重新采集并按页面清单复核。

## 迁移或发布方式

本次只面向本地验证环境。合入后使用新的示例环境变量和 Compose project 启动，执行 migration、ingestion、Milvus rebuild 与 Demo E2E。旧命名空间资源保留为人工可清理的孤立状态，不由普通启动或 reset 命令触碰。

若未来需要迁移真实租户数据，必须另行提出生产数据迁移 RFC，不得把本次 clean cut 当作生产升级流程。

## 验收标准

- 最左上角显示完整 TAP 平台标识；Tapper 一级入口使用 mark，展开的二级栏使用 wordmark。
- Tapper 页面、Agent、Skills、Library、Knowledge Graph、Test Management 与 LCA 的现有交互、折叠动画和键盘能力不回退。
- 受版本控制的当前文字与路径不含已退役产品名称，自动化品牌守卫通过。
- 活动环境变量、运行入口、Compose project、Blob/Milvus/Redis/LiteLLM 标识只使用 Tapper 命名。
- 40 张客户截图来自最终原型，图片内容、文件名、说明和 README 链接一致。
- Web lint、format、architecture、build、unit/integration tests、backend check/test、Demo E2E、Markdown link/index/lifecycle 与 XML 校验全部通过。
- Git 历史未重写，旧外部资源未被自动删除。

## 未决问题

无。产品负责人已确认 clean cut、本地验证数据可重建，以及 TAP/Tapper 两级品牌关系。
