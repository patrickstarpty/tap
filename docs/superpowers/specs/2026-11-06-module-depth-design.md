# Tapper 模块接口与职责收敛设计

## 范围与目标

用户在代码架构评估后授权执行全部四项优化：回答编排归属、回答阶段接口、前端会话运行状态、资产维护写入归属。遵循 [codebase-design](https://github.com/mattpocock/skills/blob/main/skills/engineering/codebase-design/SKILL.md) 的 depth/locality 原则，以隐藏协议和顺序约束为目标，不以文件长度为验收标准。

## 方案选择

采用现有应用内的深模块：沿现有调用链迁移职责并收紧接口。只拆文件无法减少调用方知识；新增通用编排框架或拆微服务会扩大变更及部署成本，均不采用。保留现有应用、数据库、网络契约和 UI。

## 后端回答执行

- 回答期限、冻结资源与最终引用授权、回答阶段执行、工具证据合并由应用模块拥有，HTTP 层只做请求/响应转换及转发。
- 图节点实现和草稿批处理从进程入口归入 Chat 应用执行模块，进程入口只组装依赖并处理 worker 生命周期。
- Knowledge 的会话回答实现归入 Knowledge 应用层，Chat 通过明确的公开接口依赖它；禁止为迁移而放宽现有 Chat→Knowledge 内部导入检查。
- 内部 prepare/generate/verify/finalize 阶段保留以支持 LangGraph checkpoint，但每次执行的冻结输入、回答计划和授权函数通过一个类型明确的执行上下文传递。阶段输入/输出类型表达有效状态，持久化 JSON 编解码仅在 checkpoint 边界进行。
- 对话 worker 和推荐问题验证调用应用接口，不再把 HTTP service 当业务运行时。已有 HTTP、SSE、checkpoint schema v3 的字段、语义、预算、授权、重试/取消/所有权规则保持兼容。
- 模型、检索、持久化依赖继续注入；不增加只有转发职责的新端口，不增加依赖。

## 前端会话运行

- 在 features/conversations 内提供会话运行 hook，内部拥有详情/历史事件/实时流的合并、活动 Turn 选择、轮询与重连协调，以及创建/追加/取消的幂等和防重复提交。
- 对页面提供可渲染的当前会话运行结果、发送/取消/切换相关操作；页面不再决定 SSE 游标、历史与实时事件取舍或请求重放键。
- 页面保留来源/Agent/Skill 选择和展示组合。跨 feature 的选择转换仍在 widget 层；conversation feature 不向上导入 widgets 或其他 feature。
- Insights 会话发送亦通过同一运行模块管理持久化请求标识，保持其现有 API payload 和刷新重试语义。
- 保留现有 stream reducer 作为内部纯逻辑；测试通过运行 hook 和实际 UI 断言刷新恢复、会话切换、取消和防重复提交。

## 资产写入归属

- AI 模块拥有 Agent/Skill 修订及其维护记录的持久化定义和写入助手，agent_team/skill_hub 的查询与维护用例消费这一明确接口。
- 迁移事件值类型、表定义和同事务写入实现到 AI 所有的模块（保留原表名、约束和字段），删除 AI adapters 对 agent_team/skill_hub adapters 的反向写依赖。
- 创建修订、维护记录和 owner/profile 更新的原子性、并发冲突与作用域检查保持不变；禁止换成异步事件以绕过事务需求。报表跨模块读依赖不在本次扩展清理范围。

## 验收与测试

1. 原 HTTP/OpenAPI/SSE 生成契约无漂移，checkpoint v3 可继续使用。
2. HTTP service 不含会话阶段/授权编排，worker 使用应用接口；Chat 仍只通过 Knowledge 公开接口访问知识能力。
3. 有明确的执行上下文和阶段类型，外部调用者无需重建阶段顺序。
4. TapperWorkspace 不再协调历史/SSE 恢复或保存发送幂等键；原前端行为测试通过。
5. AI 资产实现不导入报表模块的存储或维护事件类型；修订与维护记录同事务成功/回滚有回归覆盖。
6. 补充依赖检查并带负向样例，避免仅靠源码字符串断言；已有行为测试可迁移，不能以删测试掩盖回归。
7. 分批跑聚焦测试，再执行 make check、make test 和隔离确定性 E2E；服务/环境不可用须明确记录，不能等同于通过。
8. 更新 docs/architecture.md、跨模块 ADR 和产品路线图，不改变 V1 产品范围，不修改 archive。

## 实施顺序与约束

后端回答执行与接口为一个任务，随后前端会话运行，再资产写入归属，最后整体验证与文档收口。无新功能、依赖、数据库迁移、生产部署或共享数据重置。不触碰主工作区已有文档草案、.env 与数据。完成结果须回到 develop，不能只停留在临时工作树。
