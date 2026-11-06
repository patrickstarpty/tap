# Tapper Agent 工作流：运行与验收边界

本指南描述阶段 1 的原生工作流及既有 Agent 观测。对外助手始终叫 **Tapper**；`orchestrator` 是内部根角色，不是 Agents 名册或 @ 候选中的专业 Agent。当前编码路径的目标 provider 是 OpenCode + LiteLLM，但 OpenCode 阶段 0 尚未验证、阶段 2/3 尚未实施；旧 Copilot 确定性探针只属历史记录。阶段 1 的确定性验收不等于真实 provider、真实模型或生产部署验收。

## 两种任务所有权

阶段 1.3 先固定选择输入与纯路由，1.4 才接实际 Gateway 和持久归属，1.5 发布读合同，1.6 接交互；组件测试先于 1.7 的隔离浏览器验收并不表示浏览器已经通过。`runtime-mode.workflowEnabled` 是原有端点上的可空默认关闭能力位，只说明当前配置允许创建，不证明 worker 健康。前端直接使用生成合同；既有 Conversation 客户端已经转发该类型，未为它添加平行的手写传输类型。代价是阶段间必须保留明确的未验收项，不能凭前一任务的单测提前关闭后续实际调用门禁。

原生任务由 `workflows` 的独立 worker 领取与推进；`request_json.origin=native`。Conversation 入口先冻结根配置、专业 Agent 的资产 ID 与确切修订，再持久关联原 Turn；关联前任务不可领取，旧 Chat worker 也不会执行 workflow-owned Turn。独立 `POST /workflows` 使用相同的冻结构造和受信 scope/授权，且不创建隐藏 Conversation。无协调器时，旧 Chat 无 Agent 的请求仍运行；显式多 Agent/关联任务请求清楚拒绝。

Chat 的公开 admission/link/cancel 端口与 `workflow_task.admission_ready` 把双写崩溃窗口收敛为可修复的源事实：先建但未关联的任务不可执行，确认 `origin_turn_id` 后再激活。原 `chat_turn.workflow_task_id` 是持久只读关联；`TurnInput.workflow_task_id` 只用于显式追问，原冻结摘要不因此改写。桥接分在 Chat 应用/适配器与 workflow admission；原生事务另集中于 `workflows/adapters/native_state.py`，避免 workflow store 读取 Chat 私表。代价是新增一条 admission 位、公开端口与组合接线；没有第二种执行生命周期。固定受信 validation scope 与当前 loopback HTTP 一致，未来多项目调度须先设计受信项目发现，不能把当前局部组合称为生产多租户调度。

观测任务标记 `request_json.origin=observed`，只记录既有 Chat、推荐问题、关系分析和测试方案的实际 Agent 调用。原功能仍是唯一执行者和终态权威；native SQL scan 在 `LIMIT` 前排除它们，锁内 claim/control 及 runtime 入口再拒绝错误所有权。观测任务没有可用的 workflow 控制动作；暂停、重试仍由原功能控制，UI 不给出可成功但无效的回执。关掉原生 admission flag 后，既有任务与观测仍可读取；native 控制动作同时从读模型移除。

| 实际入口 | 稳定逻辑身份与归属 | 观测边界 |
| --- | --- | --- |
| 默认/自定义 Chat | 原 `turn_id`、冻结 Agent revision；默认身份为 `orchestrator`、revision `NULL`、legacy runtime | Knowledge prepare 的直接回答、检索回答和工具循环，只在已授权的实际 chat/structured/stream 调用前开始；模型轮次和 source retry 沿用一个逻辑实例 |
| Insights 问答 | 原 `turn_id`，固定默认身份、revision `NULL` | 专用解释实际 structured 调用；即使输入选择了自定义 revision，也不把未执行的配置归给它。纯指标读取、预算本地退化及授权拒绝不生成实例 |
| 推荐问题 | 原刷新队列的持久 `refresh_cycle_id`，lease token 标识具体尝试 | 仅在实际 generate 前开始；source CAS 接受完成后，同一 AsyncSession 投影观测终态 |
| 关系分析 | Chat 的 `turn_id` + 真实工具 `call_id`；独立检索的冻结 `query_plan_id` + 固定关系阶段 | 现有确定性 Agent 的 `run`，子节点归属 Chat；独立操作可按已知结果结算，不能据此推断整个 Chat 成功 |
| 测试方案 | 原 job ID + 冻结 Agent revision | 现有 generation worker 的实际生成边界；原 job 的持久结果驱动观测终态 |

纯解析、摄取、embedding/catalog、guard、planner 与本地回复不是新的 Agent invocation。普通工具和 Skill 激活是当前 Agent 的步骤，不新建 Agent 实例。Chat/Knowledge 只依赖公开应用端口；观察存储与装配在 `workflows/adapters` 和组合根。旧 Chat Turn、Knowledge 查询及测试方案的数据归属没有反转。

## 生命周期与恢复

观测上下文退出只表示一个执行段结束，不表示源任务成功。Chat 的 accepted Turn 完成事实、Test Design 的持久 job 结果及 Suggestions 的已接受 CAS 决定最终状态。Chat/Test Design worker 的有界修复扫描可补观察创建到链接、源完成到观察投影之间的崩溃窗口；扫描采用游标轮转，使长时间未终结的旧前缀不能永久挡住后页。已完成任务不重开；新 source attempt 仍留在同一个逻辑 task/node，旧失败或 unknown 证据保留，迟到旧 attempt 不覆盖获胜状态。

若源 Turn 已接受终态，而一个被观测的关系子操作仍没有持久结果，其节点和当前 attempt 只读状态为 `unknown`，并写安全、幂等的 `node.outcome_unknown` 事件；已结算的兄弟节点保持原结果。主任务不增加 `unknown` 状态，原生调度也不接管该子操作。代价是节点读合同多一个状态，不能把源已结束误画成子操作仍在运行。

推荐问题的 `refresh_cycle_id` 在原队列行锁下首次分配，失败退避和租约重领保持不变，只有接受成功后清空，下一轮再分配。`claimed_due_at` 与 lease token 都会变化，不能当逻辑周期键。同 session 回调仅在原 source CAS 成功后写观察结果；CAS 输家或事务回滚不留下“已成功”，也不在事务提交前发 Redis 通知。迁移 `0041` 允许空周期回退；若持久队列仍有非空周期，downgrade 先拒绝，以免抹掉身份。

## 原生执行与控制的实际语义

根首轮真实 Gateway 响应中的路由建议持久冻结在 workflow 事件中，不改写不可变 TurnInput。显式选择是稳定 Agent 资产 ID，服务端冻结当时可用的修订；旧 `agentRevisionId` 仍是确切修订兼容输入。已有关联任务上的无选择追问保留原候选，重复相同选择合法，变更候选集合需用节点补充要求，当前不替换名册。只有需要的专业 Agent 被委派；独立子节点可并行，依赖节点等待，内部根不显示为专业 Agent 卡片。

独立 HTTP 创建复用会话入口的服务端冻结、范围与当前 `knowledge.answer` 授权，但不创建隐藏 Conversation。会话的首次请求在原 Turn 与任务持久关联前禁止领取；旧 Chat worker 对 workflow-owned Turn 的排队和过期租约都不领取。原任务的追问是源 Turn 的短命令回执（已应用、拒绝或过期），不重复根任务最终答案；任务终止时仍未执行的追问也要得到持久拒绝。代价是 Chat 拥有一条公开工作流桥接和源 Turn 投影租约，不能把 workflow store 直接接入 Chat 私表；源 Turn 的摘要关联来自持久 `chat_turn.workflow_task_id`，不改写冻结输入摘要。

原生 runtime 使用单个 task 执行 epoch，而非独立子 worker 租约。等待可释放执行槽，但先持久写等待状态；pause 要在相应分支安全 checkpoint 后才确认。针对一个节点暂停时，健康兄弟节点的结果先落库；不能证明停下的旧 side effect 维持 `recovering`，不把它改为安全排队。显式失败节点重试在非终态 task 内新建 attempt 与 graph lineage；崩溃恢复复用原 lineage、已结算 action 和 checkpoint，不重发已结算外部调用。整个终态 task 的 rerun 是有来源的新 task，不原地复活。

`dependency_wait` 明确持久化 `running → waiting_dependency`，取消中的恢复也保留原始状态来源，不会把 `canceling → recovering` 误解为可安全排队。单 task epoch 的预算预约、末次动作/lease 交接与控制领取均受同一 task lock/fence；Task 1.2 的恢复测试已验证累计预算不会在重领时归零。代价是无法证明旧执行停下时必须停在隔离状态，不能靠新建子租约掩盖不确定性。

`waiting_dependency` 是持久状态，不是内存中的等待标签；取消中的不明旧执行也不能直接重排队。单 task epoch 简化租约与预算 fencing，却限制跨 worker 子节点接管：无法证明旧动作停下时，任务会留在 `recovering` 等待证据，可能暂时阻塞推进。工具与模型的 journal key 同时绑定 graph lineage，显式重试才允许新外部调用；只更新 attempt 编号会误把崩溃重放当新调用。原生目录只向模型提供已冻结白名单与真实装配能力的交集，未实现的工具不会作为可用能力发布；这保留了旧 Chat 工具实现，未来新工具需逐项适配。

预算从创建起累计。截止时间包含等待和暂停，不因恢复或 retry 归零；因此较长人工暂停可能耗尽当前任务，需新建任务。`KnowledgeAPI.search` 当前可能调用一次 embedding，原生 runtime 在授权派发前保守预约一次 model call，未知/失败的已预约调用不退还；预约是预算上界，不证明实际模型调用。这可能减少剩余可执行次数，但避免嵌套模型绕过预算。费用未知保留为未知，不当作零；并行已发调用与无法估价的调用还受次数、截止时间约束。原生工具只提供实际装配并处于冻结白名单内的公开 Knowledge search；编码/已发布制品执行不伪装成该路径的能力。

公开 `node.tool_started/finished` 与 `node.skill_started/finished` 只在真实执行边界记录安全的工具或 Skill ID 与结果。私有 action 预约不是调用事实；checkpoint 回放已结算工具不再新增“执行”事件。公开时间线和节点日志只显示这些已投影的固定字段，不展示查询参数、结果、prompt、内部 route/action key 或原始 provider 错误。HTTP/SSE 按 MySQL 序号回放，Redis Stream 仅以非消费组 `XREAD` 唤醒每个独立订阅者，不争抢 worker 消费组；block 低于 socket timeout，通知损坏或丢失后仍有界轮询 `events_after`。代价是每个订阅者有独立读游标和回退轮询，Redis 不能被当成持久事件权威。

列表只返回有界安全摘要、参与者、近期活动与待处理项，不能让空目标的原生任务全部显示为同一条“Tapper”；按稳定 Agent ID 过滤时在服务端按任务页批量投影匹配节点，因此同一 Agent 的多个实例不会被合并。节点详情保留冻结来源、确切修订与有类型的输入引用，但不泄露对象路径或完整 prompt；事件只有在发生时已知的节点状态和安全摘要，不把当前状态倒填到历史。读模型多了一些可空元数据与生成合同，换来任务进展、Agent 档案和历史时间线均能依据已提交事实显示。前端侧栏只读使用上述投影，关联字段由持久 Chat Turn 查询给出，不扫描全部任务页猜测归属。

原生任务完成后，Chat 在接受工作流 `task.completed` 事实的同一投影事务中保存原有答案快照，并发 `turn.completed.workflowResult={taskId, answer}`。此结果与检索型 `answer`、历史 `state` 完成事实严格互斥；实时流和历史事件用同一结果恢复文本。它不宣称经过 corpus、搜索或引用，空文本没有可显示结果。代价是完成事件合同与前端读模型增加一个明确的原生结果分支；无第二份答案存储或第二套会话生命周期。

## 裁定索引与代价

下表按原裁定编号保留阶段 1 的工程取舍；上文给出最终组合语义。编号只用于追溯，不是另一个执行计划。

| 编号 | 保留的决定 | 代价或限制 |
| --- | --- | --- |
| R01 | 1.6 组件门禁可先于 1.7 浏览器验收。 | 两种证据须分开记录。 |
| R02 | `dependency_wait` 及取消恢复来源须持久化。 | 状态合同增加明确等待转换。 |
| R03 | 累计预算恢复由 1.2 测试关闭。 | 不以 1.1 单次存储测试代替恢复证据。 |
| R04 | 终态节点可在非终态 task 内 retry；终态 task rerun 新建 task。 | 重跑 UI/API 要保留来源映射。 |
| R05 | `requestedAgentIds` 是稳定资产 ID，旧 revision ID 是单独兼容输入。 | 客户端须映射稳定 ID。 |
| R06 | Chat Turn 与 workflow admission 持久桥接，旧 worker 排除 workflow-owned Turn。 | 增加 owner/link 与修复端口。 |
| R07 | 原生执行采用单 task epoch，不增子 worker 租约表。 | 未证实停止时可能隔离阻塞，跨 worker 子接管留待以后设计。 |
| R08 | 1.3 的纯路由/输入与 1.4 实际调用、1.6 UI 分阶段验收。 | 评审时间按依赖分开，不提前宣称闭环。 |
| R09 | 首轮模型路由建议冻结在工作流事件，不改不可变 TurnInput。 | 读侧从 task 事件投影路由。 |
| R10 | 截止时间从创建起计算，attempt 限额含第一次。 | 长暂停可能耗尽任务，需要新任务。 |
| R11 | `admission_ready` 与 Chat 公开授权/取消/链接端口保证先链接再领取。 | 新增一列和装配修复，不读 Chat 私表。 |
| R12 | Chat 桥接分应用/适配器，workflow admission 独立。 | 多三个小模块，避免入口承载事务。 |
| R13 | 原生事务集中在 `native_state.py`，共用任务锁。 | 多一层适配接线，不能复制 fencing。 |
| R14 | 已有关联任务沿冻结委派集继续；改选须定向节点补充。 | 未来换 roster 需版本化控制扩展。 |
| R15 | Knowledge search 的 embedding 在授权派发前保守预约 model call。 | 缓存/早退也可能耗槽；预约不证明实际调用。 |
| R16 | 仅原 Turn 接收完整答案；后续 Turn 接收持久短命令回执。 | 命令到 Turn 的投影须修复和结算。 |
| R17 | 复用 `runtime-mode.workflowEnabled` 可空默认关闭能力位。 | 合同/parser 加字段；不代表 worker 健康。 |
| R18 | 模型可见工具取冻结白名单与真实装配能力交集。 | 未支持工具以后逐项加适配器。 |
| R19 | worker 与 HTTP 使用当前受信本地验证 scope。 | 多项目调度需新的受信发现设计。 |
| R20 | `graph_run_id` 绑定私有 journal 和下游调用；显式 retry 新 lineage。 | 恢复与用户 retry 的键须分别维护。 |
| R21 | `request_json.origin` 区分 native/observed，观察不由 native 领取或控制。 | 将来大规模查询才考虑索引列；原功能仍管控制。 |
| R22 | Chat 在实际授权模型边界 lazy start，同 Turn 循环共一实例。 | 需要跨图节点的公开观察端口。 |
| R23 | Suggestions 不以会变的 `claimed_due_at` 作逻辑键。 | 增持久 `refresh_cycle_id`。 |
| R24 | refresh cycle 首 claim 生成，退避/重领保留，成功后清空。 | 队列行需受原行锁更新并暴露周期。 |
| R25 | Suggestions 源 CAS 成功后同 session 回调结算观察；Chat/Test Design 用源事实修复。 | 回调须守锁顺序，提交前不发 Redis 提示。 |
| R26 | SSE 用每订阅者独立 XREAD 唤醒，MySQL 游标为权威。 | 增读提示端口和有界轮询回退。 |
| R27 | 独立 POST 与 Chat 共用冻结构造，派发前重查受信授权。 | 增小型 builder/授权端口，不创建隐藏会话。 |
| R28 | 定向停止未知时保留同 task epoch 的 `recovering`。 | 证据不足会暂时阻塞调度。 |
| R29 | 任务/节点/事件只增安全的冻结输入及事件时元数据。 | 生成合同加可空字段，不泄露私有资源。 |
| R30 | Agent 档案用现有列表的稳定 ID 过滤与页内批量 matching runs。 | 读查询多一步批量投影，不全量客户端扫描。 |
| R31 | ConversationTurn 摘要暴露持久只读 task 关联，区别于 TurnInput 追问字段。 | 运输合同加字段，不改冻结摘要或执行 owner。 |
| R32 | RunList 用有界目标/参与者/活动/待办，避免空目标全叫 Tapper。 | 读 DTO 加安全元数据。 |
| R33 | 已使用生成类型的会话客户端保持原传输，不作装饰性修改。 | 以请求体回归证明字段未丢。 |
| R34 | Chat 作用域包住 Knowledge prepare/generate，装配根包实际 gateway。 | 新 scope/wrapper seam；绝不重复模型调用。 |
| R35 | 旧默认 Chat 归内部 `orchestrator`、revision 空，非当前原生根修订。 | 投影必须区分默认根与无父自定义节点。 |
| R36 | Insights 固定配置真实调用按默认身份观察，即使输入选了 custom。 | 单独身份 scope；不把未执行修订归因给模型。 |
| R37 | 观察 SQL 放 `workflows/adapters/mysql_observations.py`。 | 多一个适配文件与接线，应用层保留策略。 |
| R38 | Relation 用 Chat turn/call 或独立冻结 query-plan 阶段键。 | 源需显式透传 operation ID，不能按 query 文本去重。 |
| R39 | 源已终态而子操作无结果时只读节点 `unknown`。 | 节点合同、生成类型与文案增加状态，task 状态机不变。 |
| R40 | 原生会话答案用互斥 `workflowResult` 完成事实恢复。 | 事件合同和前端投影增加分支，不伪造检索证据。 |
| R41 | E2E 六个中间件数据卷仅在自有项目覆写到私有宿主持久目录。 | runner 须验挂载/UID/清理；不能降低存储保护。 |
| R42 | 发布 fixture 只额外接受严格 UUID 且 marker/凭据/容器标签一致的自有项目。 | 多一条受限测试入口，不能开放任意项目写入。 |
| R43 | 原生 rerun 创建独立 Runs task，保留冻结业务输入与来源，清除旧 Chat 执行链接；新 admission 冻结执行时长，重跑从新 epoch 计算 deadline 并按当前 standalone 权限执行。旧任务仅从持久 deadline 与创建时间恢复正时长，无 deadline 保留原语义，同键取既有 winner。 | 结果不回写旧 Conversation；无法恢复正时长的旧任务须重新提交；未来若要 Chat 关联重跑，需要明确的新 Turn 流。 |
| R44 | 终态专业节点返工只从原持久 `node.delegated` 复制可核实的冻结输入，在同事务建新实例并关联本次指导；原节点和 attempt 保留，root 用原 call ID 的新结果替换旧摘要。无执行输入的结构节点返回 rejected。 | 旧的合成或缺证据节点不能直接返工，须新请求；真实专业委派仍可返工。 |

## 验收与适用范围

`make workflow-e2e` 为每次运行分配 UUID Compose 项目、动态 loopback 端口和私有临时目录，显式启用 DeterministicModelGateway，启动真实 API、Relay、native worker 与 Web；Playwright 走真实 Conversation/workflow HTTP 和界面，最后仅清理自有容器与进程组。它不读取共享 dotenv，也不操作默认 Demo。当前 Docker VM 空间不足，runner 只把六个自有中间件数据挂载覆盖为其私有目录内的宿主持久目录；启动前核对有效 Compose 挂载与容器 UID 写入，停掉自有容器后再删除目录。代价是宿主挂载语义与清理权限需由 runner 验证，不能绕开对象存储空间保护或改共享 Docker 设置。

真实 Knowledge 工具旅程需要测试专属的已发布资料。发布 fixture 只接受旧固定 E2E 项目，或同时具有 `TAP_DEMO_MODE=e2e`、`TAPPER_WORKFLOW_E2E=1` 且满足严格 UUID 前缀的本 runner 项目；环境、凭据、MySQL 容器标签与项目名必须一致，并保留本机端口、进程、marker 权限及镜像身份校验。代价是 fixture 多一条严格的自有项目入口，不允许任意项目借用该发布能力。owned MySQL 测试另核对持久状态、迁移、重领与并行 barrier。`make contracts`、`make check`、`make test-full` 是阶段 1 仓库门禁；这些确定性结果不能充当真实模型或 OpenCode 能力验证。

2026-11-07 工程证据：`make contracts`、`make check` 和 `make workflow-e2e` 通过，后者为 3 条真实 API/Relay/worker/Web 的确定性 Playwright 旅程。全量组成项为 TAP AI 后端 **5587 passed、585 skipped**，TAP AI Web **1119 passed**，TAP 后端 **147 passed、35 skipped**，TAP Web **240 passed**。单次 `make test-full` 在后端通过后因旧前端精确对象断言缺少两个既有清空字段而停止；修正测试预期后，前端与后续两套测试按 Makefile 顺序单独补齐通过，不能把原命令记成 exit 0。默认 skip 主要是未启用的 owned MySQL/Redis 和真实模型 opt-in；实际调用、迁移回退、源事实修复与并行 barrier 已在相应隔离窄测验证。真实模型及 OpenCode 探针依用户要求未执行，阶段 2/3 未完成。

当前 HTTP 与 worker 组合沿用本地 Demo 的受信验证 scope；多项目生产调度需要另行设计。后续 OpenCode 阶段 0 必须实测预算执行、BYOK 全调用覆盖、子 Agent 身份与并行、权限、控制回执、会话和工作区恢复、失联核对，才能进入阶段 2；阶段 3 的跨应用测试资产单写归属和真实执行闭环仍在计划中。
