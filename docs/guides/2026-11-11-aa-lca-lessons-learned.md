# AA / LCA 真实网站自动化：问题、取舍与可复用路径

日期：2026-11-11。面向后续维护 TAP 浏览器、录制器和 LCA 的工程人员。

本文记录可核验的现象、方案选择依据、实现与验收结果。旧尝试按当时证据保留，不用后来成功改写此前失败，也不把一次网站成功推广为任意网站兼容。

## 1. 当前结论

用户这轮确认的交付目标是：**允许先录制，再在 LCA 编辑器补齐步骤、定位器和断言，最终保存并独立回放成功。** 因此，“录制器一次捕获所有操作”不再是这条交付路径的必要条件，但录制器尚未支持的能力必须明确标出。

截至本次核验：

| 路径 | 已验证结果 | 不代表什么 |
| --- | --- | --- |
| 自有站完整 LCA 闭环 | 真实录制、编辑、保存、独立回放、故意断言失败、修正后通过、刷新历史及 MySQL 持久化 | 不代表外部网站天然兼容或生产认证已完成 |
| AIA 正向场景 | 真实 LCA 录制 9 步；编辑增加 3 个断言；版本 3；全新浏览器回放 12/12 通过；官网月保费 HK$60.00 | 不证明此前男性场景 Code 903 的原因，也不证明 AA 模型自行完成 |
| FWD 正向场景 | 真实录制导航；编辑器补两个年龄点击与两个断言；版本 6；全新回放 5/5 通过；Standard 原价 HK$106.20/月、当时优惠价 HK$10.62/月 | **不代表完整下拉交互的实时录制已修好** |
| AA 自主模型执行 | 原生工具、观察反馈和本地运行适配有实现及回归证据；此前真实 FWD/AIA 模型任务仍有失败 | LCA 零模型回放和人工编辑的成功不能转记为 AA 自主能力通过 |

AIA 合成输入为 1996-11-08、女性、不吸烟、保额 HK$500,000。FWD 为 30 岁、男性、不吸烟、保额 HK$2,000,000、10 年续保、Standard 月付。FWD GOODGA 首年优惠在当时页面显示截至 2026-11-11 23:59:59 网站时间；金额是本次测试证据，不能当作长期固定业务规则或当前有效报价。未提交投保、联系资料、付款或真实个人资料；本轮两站 LCA 验收没有模型调用。

可移植的脱敏结果及源文件摘要见[验收摘要](assets/2026-11-11-aa-lca-lessons-learned/acceptance-summary.json)。旧失败见[11 月 10 日真实网站验收](2026-11-10-public-quotation-lca-validation.md)，自有站链路见[LCA 调试验收](2026-11-10-lca-debug-flow-validation.md)。

## 2. 先把容易混在一起的五个问题拆开

1. **网络能否到达页面和报价依赖。** 浏览器能打开首页，不等于任务代理能加载报价 API。
2. **交互是否真的执行。** 工具返回、点击命中、控件状态改变和后端完成回执是不同层次；超时可能是结果未知。
3. **业务输入与结果是否正确。** 到达结果页、HTTP 200 或出现货币符号都不足以证明报价有效。
4. **过程能否成为可维护的定义。** 录制结果需要稳定目标、参数、明确断言和人工可编辑入口。
5. **定义能否独立复现。** 必须冻结版本，在新上下文执行，取得本次证据，不能借用原录制浏览器的 Cookie、旧价格或先前截图。

每次失败先确定层次，再选择最小修复。提高模型预算、换模型、扩大重试，不能替代对这五层的定位。

## 3. 问题与实际解决路径

### 3.1 固定步骤和工具壳不等于完整 Agent 能力

此前尝试包括 browser-use 主控、BDD 执行器、冻结机械步骤，以及 OpenCode 原生浏览器工具。现有 [ADR-038](../decisions/2026-11-08-adr-038-browser-agent-and-script-ownership.md) 已明确默认方向：OpenCode 管主会话、上下文和工具纠错；TAP 管业务目标、授权、费用和证据；Browser API / Playwright 管页面执行。历史兼容实现保留，不应误认成多套同时生效的默认控制器。

真实失败暴露了具体接口问题：只读输入被当作 fill；可见弹层和选项落在长页面末尾而被摘要裁掉；局部快照引用失效；非法 BDD 字段只收到笼统错误，模型重复提交同类计划。对应改进是让观察保留 readonly、弹窗根、可见文本、明确截断与字段级错误，并把当前回执交还同一会话纠错。单纯在后台保存完整 DOM，并不代表模型实际看到了它。

[Codex 对照记录](2026-11-09-codex-browser-reference.md)提供了“观察依据 → 动作 → 回读”的可审计摘要。它也记录了修复后仍失败的真实模型尝试。不能据此声称某个模型绝对不能完成，也不能把一次成功解释为另一模型天然更强。用户此前用 GPT 6 Astra 未达到目标，是这次复盘的背景；当前资料不足以做受控模型性能比较。这里可复用的进展在于把链路、责任和验收拆清楚，并允许可编辑定义作为实际交付路径。

### 3.2 普通浏览器能访问，LCA 却失败：先查实际网络路径

Mac 的已有 Clash Verge/Mihomo 路径使用 Fake-IP DNS。系统曾把 AIA 域名解析为 `198.18.0.39`。任务代理拒绝此地址是正确的安全行为；普通浏览器经已有代理访问成功，并不能证明原任务代理的“系统 DNS + 直连数值地址”可用。

解决方式是新增**显式、仅本地 supervisor 可配置**的上游 HTTP 代理适配：任务浏览器仍只通过自己的策略代理；通过既有回环代理完成可信公共 DNS 查询；验证完整 A/CNAME 结果；向校验后的数值公网 IP 发 CONNECT；保留原 Host/SNI 与 TLS 证书校验。失败时不回退到私网、主机名重新解析或直连。

没有放开 `198.18`，也没有修改系统 DNS、代理、VPN、TUN 或信任设置。这修的是传输不匹配，不是绕过目标安全策略。实现及测试见 [upstream-transport.mjs](../../deploy/browser/upstream-transport.mjs)、[回归](../../deploy/browser/test/upstream-transport.test.mjs)与[网络指南](2026-11-10-local-browser-proxy-routing.md)。

### 3.3 页面一直变，刚取的帧也过期：新鲜度有不同的责任

FWD 页面存在持续变化内容。实时人控请求绑定用户看到的帧、目标、控制权和页面版本，整页变化会触发 `frame_revision_changed`。反复刷新帧并不保证下一个点击一定可接受。

已实现的有限优化只允许非常窄的、显式关联的 readonly combobox / listbox / option 目标证明，验证目标身份、语义、值、关联、布局、遮挡及生命周期；不支持的上下文仍回到严格整页验证。截图对应性还需要原生捕获栅栏，不能靠前后两次 DOM 相等就宣称捕获期间没有变化。实现和反例见 [目标证明](../../deploy/browser/frame-target-proof.mjs)、[目标证明测试](../../deploy/browser/test/target-proof.browser.mjs)与[设计](../superpowers/specs/2026-11-10-browser-target-freshness-design.md)。

真实 FWD 仍进入严格回退，实时年龄点击没有完成。保存的 ARIA 结构看似符合部分条件；活动动画是可能因素，但留存证据没有完整 runtime 动画、字体及帧状态，**不能断言已找到确切排除原因**。没有为该站删除校验、豁免倒计时、强制点击或盲目重试。

最终交付选择：录制已可完成的导航，停止并导入；在 LCA 编辑器明确添加后续操作，再通过生成器及受限独立回放执行。回放是用户审查后发起的新执行，不是对未知实时动作的自动重试。它仍遵守定义、目标、网络和执行边界；不需要复用某个已过期的人控截图。这是两个不同合同，而不是对实时保护的绕行。

### 3.4 只读控件应改变交互方法，不应强填

AIA 生日和 FWD 年龄都不是普通可编辑文本。可靠做法是先观察控件属性，再操作真实日历或下拉：AIA 选择年份、月份、日期；FWD 点击 readonly combobox，然后精确选择名称为 `30` 的 option。

FWD 最终定义将后一过程写成编辑器中的两个 click，而非 fill、页面脚本赋值或猜测坐标。导航超时由编辑器从 10 秒调为 30 秒；先前失败历史保留。此次成功没有证明每个网站都应使用 30 秒，后续应按实际加载状态和步骤成本设置有界超时。

定位优先 role/name 或 test ID；必要 CSS 明确标记 review_required。不要把临时 DOM 引用当长期资产，也不要无声把失效定位器换成第一个相似元素。

### 3.5 动作通过仍可能业务失败：AIA 负向验收很有价值

11 月 10 日 AIA 男性合成场景完成操作并到达结果页，官方 API 却返回业务 Code 903，保费为空。HTTP 本身是 200。9 个动作及 URL/标题断言通过，正数保费断言失败，最终 11/12；这是有效的失败检测，不是产品报价通过。

11 月 11 日女性合成场景在录制及独立回放均得到月保费 HK$60.00，官方业务结果 success=true、code=200，12/12 通过。场景发生了变化，故不能倒推出男性、网络或某一字段就是之前失败的根因。

当前 LCA 只有六种操作：navigate、click、fill、select、assert_text、assert_url。没有原生 numeric assertion。AIA 的正数条件借助经 13 个自有样例验证的 Playwright selector 表达，覆盖空白、占位符、零及负值；文档不得把它说成已经实现通用数值断言引擎。FWD 本轮验证精确的当时优惠值；优惠到期导致断言失败时，应先核对新业务结果，再显式修改定义，不自动“修正”期望值。

### 3.6 录制器负责事实，编辑器负责意图，生成器负责一致执行

最小可靠设计以一份 canonical definition 为事实来源。录制器仅把已成功确认的动作回执投影为步骤，保留来源；编辑器允许改参数、目标、顺序与断言；Playwright 脚本是派生产物，不成为第二份可手改的真相。

- **录制不是截图或 UI 操作日志。** 只有已确认的成功回执才能导入；失败、未知和取消不能伪造成成功步骤。
- **断言不自动推断。** 点击 Get a quote 只表明动作，业务目标需另写 URL、标题、有效保费等检查。
- **保存不执行。** Save、Validate、Generate 不触发业务请求；显式 Replay 才冻结定义、输入摘要和生成器版本，创建新执行。
- **失败不覆盖。** 历史指向不可变快照，改草稿不会改变旧结果；每个 canonical step 有稳定 ID 和结果映射。

代码入口：[definition.py](../../apps/backend/src/tap_platform/lca/definition.py)、[generator.py](../../apps/backend/src/tap_platform/lca/generator.py)、[recording.py](../../apps/backend/src/tap_platform/lca/recording.py)、[DefinitionEditor.tsx](../../apps/web/src/features/lca/DefinitionEditor.tsx)。

### 3.7 控制权、停止和恢复决定“跑通”能否重复

自有站验收还发现 Take control 交接和重复录制生命周期问题。正常人控路径需保留 capability 并等待控制确认；停止需先关闭准入、固定 watermark、排空已接纳动作，再等待运行时确认清理，不能 UI 一按 Stop 就宣布已停止。新的 LCA 录制使用服务端清理标记；旧录制没有标记时需显式取消并确认结束，读取不会偷偷补写或关闭它。

执行前先持久化 DebugRun 意图与稳定幂等键，队列与数据库关联之间发生超时/崩溃时，按原 frozen request 和原 key 对账。未知执行不能当失败后自动重发。网关更新授权不改变 owner/controller 身份；只有后端持有服务凭据，前端拿短时、限定作用域的能力。

对应自有站测试统计了真实 submit 数量，验证 Save/Generate 不提交、每个新回放恰好一次、四次执行使用四个独立 Cookie 上下文，并保存故意失败的旧快照。真实 MySQL 测试确认 0009 → 0010、六张表、重连持久化及有数据时拒绝破坏性降级。见[完整验收](2026-11-10-lca-debug-flow-validation.md)。同时首次创建跨进程串行化仍是已知限制；不能从单进程重复录制成功推断任意并发安全。

## 4. 下一次照着走的排障与交付顺序

1. **固定最小验收合同。** 写明入口、合成输入、业务成功条件、允许的站点/操作、是否录制、是否可编辑补齐，以及独立回放要求。提前区分 AA 自主、LCA 录制和 LCA 回放。
2. **核验环境与来源。** 记录实际运行源码路径、版本/摘要、浏览器版本、代理模式；先确认请求走到预期代码。跨工作区不要把一处修改和另一处通过混成证据。
3. **先查网络，再查交互。** 分别核对入口、页面依赖及 API；传输错误、安全拒绝和 HTTP 200 中的业务失败分别记录。
4. **每次写入后读回。** 观察当前控件、弹层、readonly、目标可见性；未知动作先对账，不重复写入。业务结果未更新时不能读取旧价格当新结果。
5. **最小复现后修产品共性。** 将观察裁剪、只读控件、目标替换、倒计时、过期帧、重复请求等做成自有 fixture；不要在运行时硬编码网站报价和专用放行规则。
6. **录制边界无法覆盖时转编辑器。** 停止并核对录制状态，导入可靠步骤；显式补目标、点击、参数、断言与 timeout。保持 recorded/manual 来源可见。
7. **冻结后在新上下文回放。** 以实际结果、规范步骤状态、截图及适当脱敏接口证据验证；刷新检查版本与历史。必要时故意改错断言，证明系统会失败。
8. **按层报告结果。** 给出场景、定义摘要、版本、run ID、通过/失败步骤、未解决边界。浏览器单测、自有站 E2E、真实网站 LCA 和真实模型验收各列一项。
9. **最后再集成发布。** 审查原工作区未提交文件与候选来源；排除 .env、缓存、依赖、原始 trace/数据库及会话信息；检查远端分支变化，普通快进推送，最后核对远端 SHA。不得覆盖用户现场或 force push。

## 5. 这次保留的取舍与后续边界

- 本次没有继续把“全自动录制全部网站”作为阻塞交付的条件；通过编辑器完成明确步骤是支持的产品路径。FWD 仍需要这一路径。
- 对实时帧保持保守校验会拒绝部分实际可点的页面；这是已知兼容性成本。后续若继续修，应先增加不含页面值的固定诊断原因，再针对可复现条件改进证明与测试。
- Local 模式方便 Mac 开发，但不具有 Docker 等同的进程隔离；当前可信本地验证身份不是生产多用户认证。
- 凭据网站、任意 iframe/popup/上传下载、正式发布测试资产和 Test Plan 集成，不因本次两站通过而变成已交付。
- 两个网站的成功证明了这两组输入、这条产品路径和留存版本；不证明价格仍有效、不证明男性 AIA 故障已解决，也不证明 AA 自主实跑已经通过。
- 原始 trace、数据库和浏览器会话材料可能包含临时匿名会话信息。仓库只保留经筛选的脱敏摘要，不发布这些运行数据。

## 6. 集成验证记录

本次在候选工作区、既有冻结依赖上重新核验；使用 `UV_NO_SYNC=1`、`UV_OFFLINE=1` 和指向候选源码的 `PYTHONPATH`，没有更新依赖、加载用户 `.env`、运行共享数据库迁移或重跑付费模型。不同组与定向复验有重叠，不能把下表相加为独立总数。

| 门禁 | 本次结果与口径 |
| --- | --- |
| `make test` | 完整通过：AI Python 461、AI frontend 83、backend 70、Web App 9 |
| `make check` 各组成检查 | lint、format、mypy、边界、契约、前端 build、品牌及确定性质量检查通过。backend 首轮 449 passed / 47 skipped / 9 failed；9 个失败来自外层沙箱禁止 Unix socket，相关文件在获准环境复验 22/22，通过后补完余下 recipes。不是一次 uninterrupted `make check` 绿灯 |
| Browser runtime `node --test test/*.test.mjs` | 首轮 97 passed / 2 failed，两个 Unix socket 环境失败在定向文件复验 3/3 通过，覆盖全部 99 个原始用例 |
| 全量 Web Vitest | 40 文件、304 用例通过 |
| AA/LCA 前端定向 | 4 文件、27 用例通过 |
| AI Python 定向 | 首轮 674 passed / 3 skipped / 10 failed；9 个本地进程/嵌套沙箱失败由相关文件 12/12 复验覆盖；另 1 个 Langfuse 构造测试因继承 SOCKS 代理且无可选 socksio 包，在清除测试进程的代理变量后通过。覆盖 684 个原始用例，3 个跳过 |
| 文档及发布审查 | `git diff --check`、相对链接、脱敏摘要 9 个源文件 SHA-256 核验通过；源码来源核对和有限凭据/图片审阅未发现发布阻塞 |

以上环境复验仅改变测试运行方式，不修改产品源码或安全边界。未重新运行 opt-in 自有站真实浏览器、MySQL 或真实外部网站验收；相关真实旅程使用前述已留存且源码匹配的历史证据，不把 skip 算作通过。源代码核对时，原候选 2,817 项 post-acceptance 清单逐文件 SHA-256 一致；本次新增或更新的是复盘、脱敏摘要及文档状态入口。

原 develop 工作区的未提交现场保持不变；隔离候选用于集成。推送使用普通快进，不改写远端历史。远端提交及 CI 状态在交付回执中单独核验，不在提交前预先宣称已发布。

## 6. 产品集成复盘：真实执行不等于完整交付（同日补充）

用户看完录屏指出：“我不满意，你这功能都不是做在TAP平台里的”“我要的不是一个原型而已”。核对后确认：虽然代码在 `apps/web`，真实功能被单独挂在无 TAP 导航的 `/lca`；正常 Low Code 模块仍读取浏览器模拟资产并创建模拟结果。录屏证明了真实浏览器执行，却没有证明用户从 TAP 正常入口可完成工作。此前将这两种完成度混同，是交付判断错误。

修复路径不能只加一个标题或换皮肤：

1. **入口接实际能力。** TAP 默认 Low Code 页面使用真实授权上下文、SQL 草稿、录制与执行 API；保留产品导航及设计 token。旧模拟资产迁移工具移到显式 `/prototype/legacy`，不删除用户历史数据，也不在后端不可用时退回“模拟成功”。Tapper 产品导航直接链接真实 TAP 模块。
2. **保存与状态完整。** 模块切换不卸载草稿和录制状态；刷新可重开服务器保存版本。离开页面对未保存或进行中工作发出提示。读、写、执行、发布分别走现有项目授权，不再把 knowledge.answer 当成浏览器授权。
3. **有版本化资产。** 增加已验证快照的不可变发布版本。发布需同版本真实执行和规范步骤全部通过、证据有效；发布不执行。正式版本的回放读取它自己的参数定义，不拿后来草稿的参数模式代替。项目和 owner 隔离不变，不隐含向团队分享。
4. **用真实入口验收。** 还发现标准 `tap_platform.app` 未向浏览器路由传 gateway/worker token，只有定制验收 harness 的路由能录制。补齐标准入口后再测，不能用“相同底层类”替代真正的装配验证。
5. **分别报告范围。** 已实现的产品工作流不等于企业 SSO、生产部署、跨用户共享、Test Plan 绑定或完整 AA 自主能力。架构接缝和未部署部分必须如实列明。

本轮产品集成中，FWD 在 TAP 导航内录制→编辑→保存→跨模块返回→刷新重开→独立回放 5/5。另将通过的 v2 发布后修改草稿为 v3，发布 v2 的新回放仍使用原快照并 5/5；保存、发布、查看版本没有触发执行，仅显式确认时产生一次新执行。编辑超时 31 秒被后端 30 秒上限拒绝，改为合法的 29 秒后保存成功，这也是保留参数边界的验证，不应扩大运行时限制来让演示通过。详细最终门禁与标准入口证据见后续产品集成验收记录。

日常启动还必须单独验收：补齐 supervisor 对 TAP Web、标准浏览器 API/worker 的启动与生命周期管理、明确 LCA 开关和环境 allowlist、数据库迁移前置检查后，使用真实 `run-tapper-dev.sh` 隔离实例重跑 FWD 全链路及 AIA 既有资产12步回放，均通过；不能以临时应用 harness 代替用户实际启动方式。


## 7. LCA 内嵌 Tapper：必须验收真实 iframe 边界

正式内嵌闭环于源码 `bc956669` 通过自有站完整产品验收：用户在 LCA 页内
输入自然语言及审核值，Tapper 自动生成 BDD 并执行 AA；Hide/Open、Reconnect
保留原会话，实际打开截图，再显式预览/导入、编辑 Then 精确断言、保存和
当前合同回放。11/11 canonical actions 通过，account/amount 两个当前要求
均通过，唯一 admission，模型计数在导入/回放前后为 11 → 11。
这是 synthetic provider 的真实后台/浏览器闭环，不是 FWD/AIA 的 Qwen 自主
报价验收。准确 task/run、脱敏摘要与录像信息见[内嵌验收](2026-11-11-lca-inline-tapper-validation.md)。

两类失败只有真实 iframe 暴露，不能被组件绿灯掩盖：

1. **load 不等于子应用监听就绪。** API、项目、actor 和 gateway binding
   全部匹配仍会丢初始化消息。父侧在十秒内重发同一 nonce/context，子侧仅
   对合法同 nonce 重试补发 ready；成功后停止，不转成持续广播。不放宽
   origin/source/schema/context 校验，也不把重连变成新任务。
2. **sandbox 权限会在 React 之外阻断表单。** 已填值、已审核、Send 可点且无
   应用 alert，浏览器仍因缺 `allow-forms` 阻断 submit，因而根本没有 POST。
   按 console 确证只补必要 token，保留禁止 top-navigation 和 popup escape；
   同时实际点击证据 popup，不能只检查链接可见。

回归要保存失败证据、先让对应测试变红，再修最小机制，并用同一最终组合
源码串行重跑正式入口。原统一 make check/test 与受影响补丁门禁分别记录，
不因 docs-only 跟进重复大套，也不把早先失败的录像作为最终通过录像。
临时恢复状态会复制用户目标、notes 和审核值，不含传输层凭据；它不是秘密
识别功能，不能宣称可以自动过滤用户在文字中输入的密码。

## 8. 真实模型验收：先确认装配，再限制无进展消耗

2026-11-12 的正式 LCA 内嵌 FWD 两轮仍未通过。第一轮我遗漏已有的显式
upstream 配置，首个 navigate 返回 unknown 后停止，消耗 5 次调用；不能
把后续独立代理导航成功当成旧动作未执行的证明。修复是在任何模型调用前
核验显式 loopback upstream 可达并确认传到实际 worker，不把特定端口变成
通用产品要求，也不在 unknown 原任务上重试。

第二轮正确传入代理，但 25 次调用后仍没有有效计划或浏览器动作，因调用
预算停止。仅凭最终 budget_calls 不能解释此前每次返回。限时检查自有数据库
副本证明原记录未保存工具转录；52 条 activity 实际包含预留/结算和生命周期
事件，不是 52 次工具执行。具体错误无法恢复，应承认诊断缺口，而不是推测
“合同缺 selector”或降低业务要求来让验收变绿。

因此只做两个通用小修：保留最多 32 条固定 allowlist 工具名、状态、错误类别
及序号，不留原始参数/提示词/错误文本；在连续四次模型请求没有可信进展时，
于第五次转发前停止并报告 no_progress。首次有效计划和成功动作可重置窗口，
反复改计划、工具拒绝或模型声称成功不可重置，unknown 的处理不变。它是独立
于金额预算的止损机制，不是实际自主询价成功。确定性测试验证 25 轮空转样本
只转发四次，诊断文件坏了也不会覆盖停止原因；未再花钱验证真实模型。

两轮合计 30 次调用、实际 $0.6200112，FWD 次数额度已用尽；AIA 此新额度未动。
只统计可核验 TAP API 的累计数为 140 次、2,105,239 tokens、$2.42895160，
不能说这是全部助手 credits。逐轮 token、费用、任务身份和回归范围见
[内嵌验收记录](2026-11-11-lca-inline-tapper-validation.md#2026-11-12-bounded-real-fwd-attempts-not-accepted)。

随后的零费实验不再只 mock Python：真实 OpenCode 1.19.5 经原生 tap_browser、
mailbox、HTTP 服务和 owned Chromium 跑通五类协议场景。JSON-looking 文字没有
产生任何 tool/admission；工具 completed 也可能只是返回了服务端 error。
纠正被拒计划后，真实点击和 DOM 读回可以成功，原拒绝诊断仍保留。实际缺
selector 的原生错误还暴露了过粗分类，已用固定 validation 类别修复。提示
同样必须遵守冻结合同的 checked/selected_label/text 属性，不能统一要求 value。
此实验的模型是合成服务，模型转发没有走财务 BudgetGate；它证明协议和诊断
机制，不证明 Qwen 自主性，也不重复声称四次无进展保护已获真实模型验证。

## 9. 先获取事实，完成后再整理脚本

前置 BDD 确实会阻止第一次浏览器观察，但不能因此撤掉业务授权和最终验证。
本次最小调整只允许无计划时 snapshot，以及精确冻结起始 URL 的一次导航；
导航槽在 dispatch 前消耗，unknown 不可重试，业务写入仍需计划。服务端用
不可重写的系统 Given 保留真实探索 receipt，导入时可见，不冒称用户先前已
审阅，也不虚构 Then 通过。重复观察不能不断重置费用独立的无进展保护。

脚本不再是执行中必须手写的产物：live 结果与冻结合同验证后，服务端才按
真实动作确定性编译。遇到无法无损冻结的 aria-ref、无语义保护的选择或不支持
动作，保留已验证业务结果但标明脚本不可用，不用重新操作业务来补脚本。
正式 LCA 自有站完整闭环通过，真实导航/点击各一次，finish 后派发为零；
之后只有显式编辑保存后的 LCA 回放。合成 provider 与真实 OpenCode 的协议
证明、正式产品合成验收和真实网站自主性必须继续分开报告。

## 10. 定位失败不等于已经写入，也不等于业务成功

真实 AIA 首轮在入口点击前发现目标数不唯一，但 inspector 丢失了“尚未进入
actuator”的结构化事实，保守变成 unknown。不能事后靠错误文案改写历史；
通用修复只在真实计数失败分支生成私有身份标记，跨边界保留固定类别证明，
核对会话后才把新动作记为 failed。零匹配、多匹配、伪造异常和已点击后失败
分别测试，真正 unknown 仍停止。

第二轮确认该证明已传到实际产品：模型在介绍页直接找生日输入框，preflight
明确 target_missing，实际 fill 未派发。但 API 409 仍终止了该任务，尚未形成
可纠正输入预检错误的自主闭环。此处不能把“错误分类正确”包装成报价成功，
也不能用额外人工 selector 救场。两轮各六次调用，全结算并停机；余量不代表
可以自动追加试验。精确成本和验收边界见内嵌验收记录末节。

下一处修复不是泛化“409 可以重试”：客户端必须核对自己提交的 preflight
身份、同会话/epoch/操作及原始未执行证明，才返回独立 preflight_rejected，
明确业务动作未 admit。它不是伪造的 fill receipt，也不进入成功脚本。
此后必须同 epoch 的较新真实快照才允许再次写入；缺证明、未知和超时继续
停止。零费真实 OpenCode→HTTP→受控 Chromium 已证明拒绝时 fill 行为零，
观察后唯一纠正 fill 和读回成功；该协议实验绕过 CodingRuntime/BudgetGate，
四次止损另有真实 BrowserProgress 状态机回归，不能混同财务全链验收。
人工接管交回导致 epoch 改变时，此最小补丁不会恢复旧拒绝上下文，这是当前
明确限制，不得描述为已经支持跨 epoch 自动恢复。

## 11. 不要把安全命中检查当成应关闭的障碍

零费同页几何诊断表明，nearest 滚动会把按钮放在固定底栏下面；标准
Playwright 会换滚动对齐方式。通用修复仅给 agent 同一个已绑定节点一次
center 回退，继续核对身份、敏感状态和最终 hit-test；全屏遮挡依然拒绝，
不 force、不删覆盖层、不替换目标，也不改历史 unknown。88 项受控/人工
安全回归与独立强审通过，当前 AIA 只读测量支持这个具体机制。

随后唯一真实轮次却由模型选了另一条无效 selector，未走 Buy Now 几何恢复。
必须分别报告“已证明通用修复”和“网站自主验收仍失败”，不能把后者换成
人工点击救场的演示，也不能继续默默追加付费轮次。

## 12. 区分完整观察、模型紧凑视图和真正选择原因

不能因为完整快照有 Buy Now 就说模型一定看到它。离线重建显示紧凑视图保留了
前面的 Get in touch，截掉了后面的 Buy Now；完整证据文件仍提供两者，但没有
保留原模型读取转录，无法证明是否读过。目标可发现性是一个真实的设计问题，
不应通过把某站 Buy Now 永久置顶来掩盖，也不能把模型选择原因写成确定事实。

本轮仅修已明确声明非法却未拦截的 aria/ DSL：执行前返回固定可纠正拒绝，
不进入真实动作 ledger，不替换业务目标。真实 OpenCode 本地合成重放已证明
收到拒绝后可用新快照真实 ref 纠正，原 unknown 不改。所有实验零外部模型费用；
后续若改善紧凑视图，应采用站点无关的控件可发现性设计并独立验证。

## 13. 用中性、可分页的安全目录补足可发现性

修复不是挑选某站按钮，而是把当前有界观察中的安全控件身份按原顺序公开成
可读目录。结构选择器不复制属性里的用户值，标签不聚合输入/选项或受保护内容；
缺安全身份的旧记录不猜测补全。显式告诉模型候选总数、遗漏、页大小及这是
静态有界观察，DOM变化后需重观察，目录不是点击许可或Then证明。

真实OpenCode用原生read顺序读四页，再从返回记录中选后段目标，实际浏览器
仅点击一次并读回结果；合成provider只证明协议路径，没有证明真实模型自主性。
字节预算、写失败、敏感内容和原scope优先级同样属于交付条件，而不是为了
多显示几个按钮就放宽。此轮没有新增真实模型费用。

最终审阅还发现：四页分四个模型轮读完再点击，会撞上原四轮无进展保护。
不应为了测试通过放宽止损。四页保留为分页协议证据；另用两页真实原生读取
加同一guard逐轮核验，在原窗口内完成唯一点击。目录提示明确文件读不是业务
进展，应定向read/grep，不把可分页误解成可以无限空转。

### Full-chain follow-through

The catalog protocol test is now complemented by a formal LCA → production
CodingRuntime/BudgetGate → native OpenCode → controlled browser → explicit
import/edit/replay test (`d4e63c1d`). Two native reads share one provider response,
so the original 12-call cap remains unchanged; 11 reservations/settlements match.
The current contract and all 11 replay actions passed with synthetic decisions.
See the [validation record](2026-11-11-lca-inline-tapper-validation.md#full-production-chain-catalog-acceptance-2026-11-13).

Two test-environment gaps were caught rather than hidden: workspace pnpm does not
install the browser runtime's separate npm lockfile; and Playwright.fill in a
fixture is not equivalent to production controlled fill. The latter requires
viewport hit checks. Keep inputs visible for a directory-focused fixture, and test
it with the production dispatcher. Preserve old UNKNOWN evidence and use a new
owner after a demonstrated setup correction. Do not infer real-model autonomy
from a complete synthetic production path.

### Real-provider diagnostic durability remains a separate boundary

The separately approved AIA catalog run failed with `no_progress` after seven
settled real calls (USD 0.1460064), before any business write. Buy Now was present
in the safe candidate source, but no durable native-tool transcript proves which
catalog records the model consumed. Bounded diagnostics produced inside a runtime
are insufficient if the persisted result projection drops them. Preserve that
uncertainty; do not invent a selector/entrypoint explanation or spend another
round to investigate it. The next narrow check should exercise sanitized terminal
diagnostics through the real persistence boundary with a local synthetic provider.
