# V1 增量验证记录

本轮续接 `feat/v1-completion` 的可靠问答、图谱查询与 Skills/Agents 后端工作，补齐目录管理界面和图谱脉络分析。工程验证与真实资料的业务验证分开，最终状态见 [V1 总纲](../superpowers/plans/2026-09-29-v1-roadmap.md)。

## 原型对照

仍使用 `apps/web` 的同一 `/prototype` 入口和已有产品壳。桌面采集条件为 1280×720 布局视口、2× 像素密度、确定性 fixture。新增目录工具页签、Skill 导入与 Agent 组合编辑器；回答新增关系芯片，点击定位原始快照路径。

| 页面 | 变更前 | 变更后 |
| --- | --- | --- |
| Agents | [截图](../assets/v1-completion-2026-11-04/agents-before.png) | [截图](../assets/v1-completion-2026-11-04/agents-after.png) |
| Skills | [截图](../assets/v1-completion-2026-11-04/skills-before.png) | [截图](../assets/v1-completion-2026-11-04/skills-after.png) |
| Tapper 回答 | [截图](../assets/v1-completion-2026-11-04/answer-before.png) | [截图](../assets/v1-completion-2026-11-04/answer-after.png) |
| 引用图谱 | [截图](../assets/v1-completion-2026-11-04/graph-before.png) | [截图](../assets/v1-completion-2026-11-04/graph-after.png) |

补充状态：[Agent 编辑器](../assets/v1-completion-2026-11-04/agent-editor.png)、[工具与 MCP](../assets/v1-completion-2026-11-04/tools.png)、[Skill 导入预览](../assets/v1-completion-2026-11-04/skill-import.png)。

对照确认：原壳、模块和既有内容保留；新增项集中在本次能力入口。关系路径图中 1 条边、2 个端点被高亮，历史快照明确提示。模块导航与跨模块旅程的最终原型回归 12 项通过，覆盖 Tapper、Agents、Skills、Library、Test Management、Test Insights、Low Code Automation、悬浮助手交接及 Test Plan/Automation 往返。该验证仅证明设计基准没有退化，不表示平台后端交付或真实模型验证。

## 英文演示截图

2026-11-04 按演示要求从同一 `/prototype` 重采 [Agents](../assets/v1-completion-2026-11-04/agents-en.png)、[Skills](../assets/v1-completion-2026-11-04/skills-en.png)、[Agent 配置](../assets/v1-completion-2026-11-04/agent-editor-en.png)、[Skill 导入](../assets/v1-completion-2026-11-04/skill-import-en.png)及 [Tools / MCP](../assets/v1-completion-2026-11-04/tools-en.png)，并更新 [客户演示指南](2026-09-04-customer-prototype-demo-guide.md#agents--skills复用工作配置英文截图)与 README 入口。界面、示例与导入文件正文使用英文；产品默认语言保持英文。本组预览工具导出的 PNG 为 1280×720；上方原始前后对照图保留，不覆盖历史采集证据。本次仅更新文档与截图，未重跑或改变上述工程验收结论。

## 回归发现与处理

- 修复管理页停用依赖无法移除、旧修订无法重新启用的问题；下载保留服务器原始文件字节。
- 工具参数改为完整 JSON Schema 2020-12 校验，禁止获取远程 schema 引用；模型目录中的工具调用能力用于创建与运行时校验。
- 图谱工具新发现的边与证据重复执行发布权限校验，拒绝已撤回的知识，不把未授权事实加入模型上下文。
- 修复关系 SSE 事件在领域白名单中的遗漏；仅选来源时，图谱完成校验使用服务端冻结的解析文档，而非浏览器可省略的文档数组。
- 真实 MySQL 嵌套回归复现工具循环继承父检查点、无法序列化 `_Run` 的问题；临时子图关闭继承并覆盖同步执行策略，外层执行持久化保持有效；真实 MySQL 嵌套回归通过。当前锁定的 LangGraph 版本会对无 saver 子图的显式异步策略给出预期告警，该告警不影响执行或外层持久化。
- Agent 编辑器弹层使用视口高度回退值，真实浏览器验证保存动作在 1280×720 可滚动到达。历史图谱支持返回当前图谱，并按浏览器历史切换原始快照。
- 新增关系类 golden 用例 8 条，关系引用召回率、原文引用召回率均为 1.0；全套 31 条的关键词召回率为 0.9615。确定性抽取夹具不代表真实模型抽取质量。
- 单独运行隔离 MySQL 验证：Skill、Agent、MCP 的 4 项通过；管理历史排序及禁用版本的 2 项通过；关系引用的事务回滚、原文链接与重启还原通过。测试各自拥有独立数据库，没有使用共享演示库。

## 范围决定

`insights.query` 从最初 Skills/Agents 设计中的 V1 内置列表移至 V2.2：V1 总纲排除 Test Insights/ClickHouse 交付路径，现有指标查询需要专门的委托身份与资源授权。V1 工具注册表提供知识、图谱和受配置约束的 MCP 能力，不扩大为指标服务的访问授权。

补充运行的 `make schema-drift` 未通过：仅报告历史 `knowledge_managed_document`、`knowledge_managed_generation` 两张表的主键/唯一约束差异，迁移 0023、相关适配器和元数据与本轮基线 `5e9c16b7` 完全一致，本次新增 `chat_relation_citation` 没有差异。该旧问题需要单独的前向迁移和重复业务键预检，本轮未改动历史迁移或共享数据。

全库运行中发现旧 supervisor 测试夹具的启动屏障在并行 Docker/浏览器负载下耗尽 5 秒预算；该预算调为 15 秒，外层测试上限为 30 秒，被测应用的 2 秒就绪窗口保持不变。两个就绪拒绝回归用例重新通过。

## 交付门禁

| 门禁 | 结果 |
| --- | --- |
| `make check` | 通过；保留既有构建 chunk 大小告警 |
| `VITEST_MAX_WORKERS=2 make test` | 通过：TAP AI 后端 3566、前端 538；TAP 后端 147、Web 228。按环境跳过项分别为 342 和 35，性能用例独立运行 |
| 原型模块与跨模块浏览器回归 | 12 项通过 |
| `make demo-e2e` | 通过：首轮 7 项、应用重启 1 项、中间件重启 1 项、存储复验 29 项；失败/重试/跳过均为 0，所有拥有资源的清理状态均为 `complete` |
| 补充 `make schema-drift` | 未通过：上文两张旧表的既有差异；新增关系引用表无差异 |

重启夹具现对严格限定的隔离 E2E 项目停用全部夹具 Agent/Skill 修订，覆盖本轮 UI 新建和导入的资产；测试未来请求不能复用停用修订，同时历史标签与原始引用仍可恢复。真实业务资料、外部 Skill 与真实模型人工验收没有因确定性测试而被标记通过。
