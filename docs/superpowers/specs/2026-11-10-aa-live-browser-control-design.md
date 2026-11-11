# AA 实时浏览器与人工控制设计

## 1. 目标与范围

用户于 2026-11-10 选择最新里程碑：AA 对标 Manus 的实时观看、人工接管、交还后 Agent 继续；暂用 OpenCode + Playwright。首先在隔离自有测试站验收控制权和防重复提交，不调用付费模型，不把单元测试称为真实网站验收。

这是待书面评审的实施设计，尚未交付。保留现有工作树改动、OpenCode 主会话、Browser API/worker、Playwright 和冻结回放。旧 browser-use 主控设计不是本次实现依据。LCA 消费同一浏览器服务，不另建浏览器控制器。

完整路径：创建 AA 任务 → 实时观察 → 请求人工控制 → 安全交接 → 人工修改页面 → 显式交还 → 新观察 → 原任务继续 → 独立业务结果和回放制品。首阶段不承诺进程崩溃后的无损恢复、不重放未确认动作，不接入真实账号或外部报价。

## 2. 方案取舍

推荐延伸现有 Browser API 的受控命令及帧读取接口。它能复用项目权限、动作账本、网络策略和 worker 生命周期，并为 LCA 提供同一会话。

不向 UI 直接开放 CDP：现有 cdp-relay 是内部原始 TCP 转发，没有用户控制代次与命令范围约束。也不在本阶段引入 VNC 或新 Agent 引擎，避免第二控制通道与额外认证面。

## 3. 控制权模型

worker 租约 owner 与页面 controller 分离。session 新增 controller_kind（agent/human）、controller_id、control_epoch、control_state（agent_active/takeover_pending/human_active/handback_pending/blocked/closed）及绝对截止时间。兼容旧会话时只允许旧 agent 模式，不能绕过新会话代次检查。

所有控制请求带 expected_epoch、幂等键；actor 由经过授权的服务上下文派生，不能信任 JSON 自报身份。接管请求在事务中比较代次，封住后续 Agent 动作；worker 结算已派发动作，确认 Agent 写通道没有未决写入后递增 epoch，才公布 human_active。排队未派发旧代次动作明确拒绝。

动作入队和实际派发都验证 controller/epoch；仅检查 HTTP 接口不够。线性化点是在与接管转换相同的 session 行锁事务内创建 dispatch reservation，原子核验 epoch/controller 并标记动作 dispatching。接管先取得锁则拒绝新 reservation；派发先取得锁则属于待结算动作，必须完成或变为 unknown 后才能处理交接。runtime 调用发生在事务外，但 reservation 在确认回执前一直算 outstanding；human_active 只能在不存在 outstanding 且写通道撤销确认后提交。worker 在 reservation 后失联按可能已派发处理，不自动重发。取消与截止时间优先。任何已派发动作结局不明进入 blocked，不允许通过接管、交还或连接重试自动重做。幂等重试只返回已存在回执，不产生第二动作。

交还封住旧 human epoch，结算其动作，刷新页面观察及临时元素引用，递增 epoch 后才恢复 Agent。旧浏览器连接不再能发送写动作。观察流只读，不取得写控制权。

## 4. 共享服务合同

当前 Browser API 使用固定 tap-ai-browser-service 主体，不能把它当作独立人类身份。首阶段仍是可信本机开发范围：TAP 应用网关验证现有项目/任务可见性后，为每个打开的控制面板生成短时、随机、不持久化的 opaque capability，绑定 task/session、human controller_id、允许动作和到期时间。网关仅接受同源本机请求并校验 Origin/CSRF；凭据不进 URL、日志或页面持久存储。Browser API 只接受受信网关验证后的身份委托，不接受页面 JSON 自报 actor；Agent 使用独立服务 actor。两个人工面板有不同 controller_id，竞争同一控制代次。能力到期或取消即撤销；刷新重获观察能力，不自动取得写权限。本设计不宣称已有生产用户认证或多租户人类身份；真实用户接入前需另验认证与会话管理。

新增项目作用域 session control 读取及请求接口（request_takeover/hand_back）。响应含 session_id、task_id、control_state、controller_kind、control_epoch、pending_action_id、deadline_at、可执行控制项。

所有人或 Agent 动作使用统一账本，响应含 action_id、sequence、actor_kind、control_epoch、status、observation。status 明确区分未派发拒绝、已完成、已知失败、结果未知；动作事实包含语义目标、脱敏输入、实际结果及来源。输入值不通过事件日志任意传播。

LCA 在 human_active 会话消费已确认动作，生成可编辑草稿和确定性回放；不会另连 CDP。AA 不负责 LCA 草稿存储、编辑器或正式 Test Plan 发布。与 LCA 实现者已核对的录制回执还需包含 command_id、类型化参数、动作前观察所绑定的稳定语义目标（test-id 或 role/name，CSS 回退明确标识）、动作后 observation sequence/evidence ref。必须在点击导致跳转或 DOM 变化前绑定目标；点击诱发的导航只作元数据，不能重复录制为 goto。断言由用户后续明确编写，不从动作成功推断。未知/失败动作只进日志，不成为成功回放步骤。首个验收只用合成非秘密数据，密码字段在请求持久化前拒绝。

共享类型固定为 browser-control-v1：ControlState = agent_active | takeover_pending | human_active | handback_pending | blocked | closed；closed 的 terminal_reason 包括 canceled、expired、completed，blocked 保留原因。Receipt = {schema_version, command_id, action_id, sequence, actor_kind: agent|human, control_epoch, status: rejected|completed|failed|unknown, pre_observation_sequence, target: {strategy: test_id|role_name|css, value, fallback}, arguments, post_observation_sequence, evidence_refs}。原始按键在敏感筛查前不可持久化，arguments 使用经过批准的类型化非秘密数据；目标解析失败不得伪造 target。运行中的 pending/dispatching 属于账本状态，不能当作 acknowledged receipt。LCA 停止录制时冻结动作 watermark，等待所有此前已准入动作结算；未结算或 unknown 明确保留，不进入成功草稿。正式实现通过生成合同与合同测试固定字段。

## 5. 实时查看和人工操作

由浏览器 worker 生成有界、带 frame_sequence/时间/viewport 的临时画面，通过已授权的应用代理传给前端；不把画面存成永久证据，不在 URL 中附带长期令牌。只允许当前任务授权观察者读取，取消/到期立即关闭通道。连接断开显示画面停滞，不把旧图称为实时。

人工输入限定当前 human epoch。点击包含所见 frame_sequence 和视口信息，画面不匹配时拒绝并要求刷新；键盘/滚动等走受控 Playwright 命令。首个自有站旅程仅覆盖页面点击、输入、滚动；上传下载、复杂输入法和系统弹窗不伪装成已支持。

人工模式停止模型观察与新模型调用，等待不消耗调用预算；已经在途的模型响应可以结算但不能提交新的页面动作。保留绝对资源期限，不因刷新/交还重置预算。超时或未确认控制权显示 blocked 或 closed（terminal_reason=expired）。

敏感人工操作的键值、画面、trace、视频不能落入默认日志、模型上下文或自动生成脚本。实现必须验证现有连续录像/trace 可在人工阶段安全分段；不能证明时人工登录保持不可用，不能只隐藏 UI 输入。交还后的观察过滤密码与凭据字段。无法可靠记录的人工业务步骤作为 replay 的人工前置条件保留。

## 6. OpenCode 衔接

不杀死现有主会话来实现暂停。工具派发和模型网关均检查任务控制状态；人工期间以等待协议阻止新调用，并保持取消/绝对期限可响应。交还后将最新观察及人工修改摘要送回原 session，而非从头重新执行用户目标。未知结果仍由现有 unknown latch 阻断。

当前 runtime 的 finally 清理会终止浏览器/容器，不能把人为暂停映射为终态返回。先用确定性 runtime 证明等待与控制合同，再用固定 OpenCode 版本的无付费协议探针验证其继续能力；探针必须证明交还前后 provider_session_id 和 browser_session_id 相同、human_active 期间无新模型请求、在途旧动作不能在交还后执行、新写动作前已有新观察、等待期间取消和绝对超时仍能及时结束。该探针在完整 UI 开发之前完成；无法原会话继续时报告阻塞，不静默启动新任务冒充恢复。

## 7. UI

RunDetail 增加当前页面、画面时间、控制权与截止时间。按钮根据服务器能力展示：请求接管、正在交接、人工操作、交还、正在恢复。按钮请求成功不等于控制已转移；以持久状态确认。关闭面板只关闭观察，不取消任务，也不隐式交还。连接丢失不自动恢复 Agent。

BDD 与现有逐步证据继续显示。实时业务完成、脚本整理、独立回放分开。没有新增每次截图审批，未明确批准的 BDD 审批策略不在本里程碑暗中引入。

## 8. 存储、兼容与安全

使用显式数据库迁移及合同版本，不依赖 create_all 修改已有表。控制请求与状态转换原子提交，重复幂等键须校验请求摘要。控制 epoch 与 action sequence 不混用。旧 bundle 仍可读和回放。

仅绑定现有本地开发边界，不扩大外网访问或站点网络权限。测试 actor 授权与跨项目隔离、旧 epoch、伪造 actor、取消后帧和输入访问。不要把本地开发隔离称为生产安全保证。

## 9. 验收

自有站给出已知业务状态与提交计数。确定性 Agent 填第一步，用户接管修改第二步，交还后 Agent 读取新值并完成；生成制品，可完整录制的合成非秘密人工操作带 actor=user 来源并进入支持的回放步骤；重置自有站后独立零模型回放应成功。另设不能可靠记录的人工步骤案例，回放在该前置条件明确停止，不伪造自动化。页面操作只能由一方执行。

必测：接管与提交同发；两个接管者竞争；排队旧 epoch；人工输入重复请求；交还后旧连接；画面过期；模型在途；断连；取消；绝对超时；未知回执；worker 丢失；敏感阶段证据隔离。失败不能被重新派发遮掩。成功旅程外部提交计数恰好为 1；重复请求、旧 epoch 和重连带来的额外提交计数为 0。接管与提交竞争时允许已取得 dispatch reservation 的唯一提交先结算，但不能重复。未知回执场景不重新派发，保留实际计数与未知状态，而非只有 UI 文案正确。

门禁：存储/服务/worker 合同测试、OpenCode 无付费协议测试、前端状态测试、隔离真实 Chromium 旅程、make check 与 make test。实际环境阻塞与未执行阶段单独列明。真实网站及付费模型另行授权与验收。

## 10. 实施顺序和完成边界

1. 控制权账本、迁移及两阶段动作 fencing，使用确定性 runtime 验证竞态。
2. worker 受控画面/人工命令和敏感阶段证据隔离。
3. OpenCode 等待/继续协议与取消/预算衔接。
4. RunDetail 和 LCA 共享会话接入。
5. 完整无付费本地旅程及独立安全复核。

每一层先完成测试；前两层不冒称 Manus 式全链路已交付。未经书面设计评审，不开始该架构增量。现有已批准安全修复保持不变。
