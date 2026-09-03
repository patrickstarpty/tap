# Low Code Automation 交互原型评审

| 字段       | 结论                                                                                                                                 |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 评审对象   | `http://127.0.0.1:4175/` 的 Athena / Low Code Automation 页面与对应 React 原型                                                       |
| 评审日期   | 2026-09-03                                                                                                                           |
| 产品基线   | [RFC-008：TAP 产品壳层与 Low Code Automation 交互原型](../proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md) |
| 评审方式   | 桌面与 `390 × 844` 页面检查、DOM/可访问语义、交互路径、代码与 Web Interface Guidelines 对照                                          |
| 评审时结论 | **需要结构性调整**：两级导航可保留；Low Code 资产、创建、详情、执行与 Athena 编排仍是旧的单草稿模型                                  |
| 产品决定   | 2026-09-03 已批准本 Review 的结构性调整方向，并要求按行业通用方式完成 Conversation 历史、BDD/动作映射和关联 Run 记录                 |
| 跟进状态   | 结构性原型调整已在同日内联完成；本 Review 保留调整前证据，实施结果见文末复核                                                         |
| 运行状态   | 调整后桌面与 `390 × 844` 复核未观察到 console error 或 warning；所有运行仍为 `Simulated`                                             |

## 执行摘要

当前两级产品导航已经建立了正确壳层：一级 Rail 承载 Athena、Test Management 和 Low Code Automation；Athena 激活时显示 New Chat、Agent、Skills、Library 的上下文 Sidebar。桌面与窄屏的基础焦点、抽屉、触控尺寸和 reduced-motion 处理可以继续使用。

Low Code 内容层仍需结构性调整。点击一级入口后，页面直接显示硬编码的 `Life insurance application automation`，同时又声明 `No automation draft yet`；用户既看不到 Automation Library，也无法判断自己是在模块首页还是某个资产详情。Athena 收到 Automation 意图后立即生成固定步骤，没有先询问 Test Plan、判断 Web/Mobile、创建稳定资产或返回两张资产卡片。

生成后的详情把 `Navigate/Click/Fill/Assert + Locator + Value` 作为主编辑界面。它是有价值的实现绑定编辑器，但不是业务用户所理解的 BDD Builder。桌面上脚本预览占据主要空间；移动端所有技术字段纵向堆叠，形成很长的操作路径，而 Scenario、Copilot、执行目标和 Test Plan 关系全部缺失。

因此建议保留导航壳层和已有技术动作能力，重做 Low Code 的信息架构与状态模型，而不是继续在当前单详情页上增加按钮。

## 已对齐、可以保留

1. **一级 Rail + Athena 上下文 Sidebar**：模块层级符合 `NAV-001`、`NAV-002`。
2. **Athena Sidebar 响应式行为**：窄屏遮罩、Escape、焦点恢复、背景 inert、滚动锁定和 `44px` 触控目标已有基础。
3. **意图识别入口**：已有 `answer / test-plan / automation` 初步分类，可扩展为多轮编排状态机。
4. **技术动作编辑器**：Action、Locator、Value 和 Generated Script 可以保留，但应进入 BDD Step 的 `Implementation binding / Advanced`。
5. **双语文案与基本焦点处理**：可沿用，不需要随 Low Code 重构推翻。

## P0：下一版原型必须调整

### P0-1：Low Code 默认落点改为 Automation Library

**现状**：`LowCodeAutomation` 只有空状态或单份详情；无草稿时仍显示具体寿险标题，只提供 `Start in Athena`。

**证据**：

- `apps/web/src/widgets/tap/TapProductPrototype.tsx:421-501`
- `apps/web/src/widgets/tap/prototype/copy.ts:385-404`

**目标**：点击 Low Code Automation 永远先进入带总数、既有资产和 `New automation` 的 Library。详情只能由列表行、新建完成或资产深链进入。空 Library 同时提供 `Create manually` 和 `Ask Athena`。

### P0-2：建立 Automation 资产与稳定身份

**现状**：全局只有一份 `automationSteps | null`；Athena Turn 保存步骤快照，打开历史结果会重新复制并覆盖当前单例。

**证据**：

- `apps/web/src/widgets/tap/TapProductPrototype.tsx:689-691`
- `apps/web/src/widgets/tap/TapProductPrototype.tsx:883-899`
- `apps/web/src/widgets/tap/prototype/model.ts:25-39`

**目标**：建立 `Automation[] + stable Automation ID + ArtifactRef`。Library、Athena 卡片和详情都读写同一个资产 ID；Test Plan、类型、支持平台、执行目标和 revision 都属于资产或 Run，而不是聊天步骤副本。

### P0-3：实现 Athena 的 Test Plan 优先编排

**现状**：`sendMessage` 识别 Automation 意图后立即附带固定步骤；生成卡片直接提供导入和打开按钮。

**证据**：

- `apps/web/src/widgets/tap/TapProductPrototype.tsx:178-255`
- `apps/web/src/widgets/tap/TapProductPrototype.tsx:869-888`

**目标**：实现 `Automation 意图 → 是否先建 Test Plan → Test Plan Review/跳过 → 类型判断或澄清 → Automation Review → 两张资产卡片`。跳转必须打开同 ID 的 Test Plan / Automation。

### P0-4：页面位置必须支持资产深链

**现状**：列表、聊天和详情都只依赖 `activeModule` 页面状态，浏览器 URL 始终为 `/`；产品模块使用 `<button>` 导航。

**证据**：

- `apps/web/src/widgets/tap/TapProductPrototype.tsx:670-708`
- `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx:121-150`

**目标**：至少为 Automation Library、New Automation、Automation Detail 和 Test Plan Detail 建立可恢复的位置状态；正式交互使用 `<Link>/<a>`，支持刷新、前进后退、复制地址和从 Athena 深链。

## P1：完成 Low Code 核心体验

### P1-1：BDD Builder 成为详情主工作区

**现状**：聊天中 Given/When/Then 是静态预览；详情数据只有 Action、Target、Value，两者没有关联。

**目标**：详情主结构采用 `Feature → Scenario → Given/When/Then/And`。用户可增删 Scenario 和业务步骤；技术动作进入每步的 Advanced 实现绑定；Generated Script 降为次级、可折叠视图。

### P1-2：增加完整的新建流程

**目标**：从 Library 进入独立创建 Surface，支持：

- 选择 Web 或 Mobile；
- 可选关联尚未占用的 Test Plan；
- 创建空白 Automation 并手写 BDD；
- 输入 Goal 后点击 `✨` 生成草稿；
- 类型无法判断时原地要求用户选择，不能猜测。

### P1-3：增加详情 Copilot

**目标**：Automation Detail 始终可打开 AI Agent 对话。对话基于当前 Automation ID；调整先呈现建议或差异，由用户 Apply/Reject，不静默覆盖手工内容。

### P1-4：增加执行配置与模拟 Run

**Web**：选择 `Execution Agent`；其产品定义就是 Azure DevOps Pipeline Agent。

**Mobile**：同一 Automation 可支持 iOS、Android 或两者；每次 Run 选择一个受支持平台和具体 Device。

Run 在当前纯前端原型中必须标为 `Simulated`，并展示禁用原因、运行状态和有限日志，不能声称产生真实执行证据。

### P1-5：实现 Test Plan 严格 `1:1`

Test Plan 与 Automation 的关联可选但严格双向 `1:1`。两边详情都显示可点击关联；选择器排除已占用资产；解除关联后才重新可选。一个 Test Plan 的多个 Scenario 对应一个 Automation 内的多个 BDD Scenario。

## P2：体验与规范加固

### 资产管理

- Library 增加 Search、Type、Status、Test Plan 筛选和最近运行信息。
- 覆盖 Loading、Empty、Error、Disabled reason 和超长标题/目标。
- 大列表进入真实实现时再加入分页或虚拟化，原型不提前增加复杂度。

### 编辑安全

- 删除 Step/Scenario 当前立即生效，应增加确认或 Undo。
- Save 需要建模 `dirty / saving / error / saved`；保存结果使用 `aria-live="polite"`。
- 有未保存修改时，离开详情需要提醒。

### 表单与导航语义

- Automation 和 Athena 表单控件增加稳定 `name` 与适当的 `autocomplete`。
- 产品模块和资产跳转使用 Link，而不是只触发 `onClick` 的 button。
- 增加跳到主内容的 skip link 与主内容锚点。
- Athena picker 当前声明可多选但选中一项即关闭，需让 ARIA 语义与真实行为一致。

### 视觉与响应式

- 桌面详情减少大标题与脚本预览对主任务的竞争；建议 Scenario 列表 + BDD 主区 + 可切换 Copilot/Run，而不是永久展示多个同权重面板。
- 移动端保留产品 Rail，但把 Scenario 列表收敛为选择器，把 Copilot/Run 放入全屏抽屉；避免技术字段产生超长页面。
- 用户消息、Goal、BDD 和 Locator 需要 `overflow-wrap`/截断策略，防止长 URL 或无空格内容撑破布局。
- Dialog/Picker 增加背景滚动锁定与 `overscroll-behavior: contain`。

## 推荐调整顺序

```text
Automation 资产模型 + 页面位置
        ↓
Automation Library
        ↓
New Automation + BDD Detail
        ↓
Test Plan 1:1 + Web/Mobile Run 配置
        ↓
Athena 多轮编排 + 两张资产卡片
        ↓
Copilot、移动端与状态/可访问性加固
```

这个顺序先建立资产身份，再连接 Athena，避免先做漂亮卡片、后续却因数据模型变化返工。

## 评审结论

- **不需要重做**：两级导航、Athena Sidebar 基础交互、双语、技术动作编辑和脚本预览能力。
- **必须结构性调整**：Low Code 默认落点、Automation 资产模型、Athena 编排和深链。
- **必须补齐**：BDD、新建、Copilot、Execution Agent / Device、Test Plan `1:1` 和模拟 Run。
- **实施前门禁**：产品方向已经批准；代码变更前仍须建立并评审独立实施 Plan。本 Review 本身不是实施步骤清单。

## 2026-09-03 实施后复核

本节记录同日获批原型调整后的结果，不改写前文的调整前评审证据。

| RFC-008 条目        | 复核结果                                                                                                                     |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `AUT-001`           | Low Code Automation 默认进入带总数、搜索、既有资产和新建入口的 Automation Library。                                          |
| `AUT-011`           | Automation 详情以 Scenario/BDD 为主结构；每个 BDD Step 下直接显示并可编辑有序实现动作。                                      |
| `AUT-004`–`AUT-005` | 新建和 Athena 编排都推断 Web/Mobile；无法可靠判断或同时命中两种渠道时要求用户显式选择。                                      |
| `AUT-006`           | Test Plan 与 Automation 选择器排除已占用资产，reducer 原子维护严格双向 `1:1`。                                               |
| `AUT-003`           | Web 选择 Azure DevOps Pipeline Agent；Mobile 选择 iOS/Android 和可用设备后才启用 Run。                                       |
| `AUT-010`           | Run 明确显示 `Completed · Simulated` 与无真实执行证据说明。                                                                  |
| `AUT-012`           | `AutomationRun.testPlanIdAtRun` 固化关联；同一 Run ID 投影到 Automation 与 Test Plan 两侧历史，未关联 Run 不回填。           |
| `ATH-001`–`ATH-004` | Athena 的 Automation 意图先询问 Test Plan；Yes 路径先 Review Test Plan，再交付两个资产入口；Skip 路径交付未关联 Automation。 |
| `ATH-005`           | Conversation 首轮后入列，当前项可见高亮，跨模块和页面刷新恢复。                                                              |

### 验证证据

- 相关 Vitest：`101 passed`；Web 全量 Vitest：`226 passed`。
- ESLint、Prettier、架构边界与 Web production build：通过。
- 桌面浏览器：Automation Library、BDD/动作映射、Automation Run → Test Plan Execution History 路径通过。
- 窄屏浏览器：`390 × 844` 页面宽度等于 viewport 宽度，无横向溢出。
- 浏览器 console：未观察到 error 或 warning。
- 自审发现并修复两个遗漏：Athena 子页面保持当前 Conversation 高亮；Athena 对 Web/Mobile 做可解释推断并在不确定时询问用户。
- 当前结果只证明纯前端交互原型成立，不证明 Azure DevOps、浏览器或移动设备真实执行。

## 2026-09-03 Athena Library 与视觉一致性复核

本次复核覆盖 RFC-008 后续确认的 Athena 上下文、Library、Knowledge Graph 与跨模块视觉约束。

| RFC-008 条目 | 复核结果                                                                                                                                                                                                         |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NAV-003`    | `New chat` 使用方框铅笔图标、透明背景和普通字重；Athena 二级菜单当前项使用整行浅灰圆角背景，不再显示紫色侧边线，Hover 与键盘 Focus 仍可辨识。                                                                    |
| `ATH-006`    | Knowledge、AI Agent、Skill 被选中后均显示类型图标、名称与独立删除按钮；删除调用既有选择状态，不影响其他上下文。                                                                                                  |
| `ATH-007`    | 一级产品 Rail 的 Athena 入口和二级 Athena 标题均使用白色背景的装饰性 `A`；紫色只用于一级入口的外层选中态，Welcome 与 Assistant Turn 不再重复显示头像。                                                           |
| `ATH-008`    | Composer 底部提供 Codex 式模型触发器；触发器不显示闪电，模型名称统一使用 `GPT-` 前缀。菜单只列模型，不出现 Fast、Ultra 或 reasoning effort。模型按 Conversation 保存，新对话默认 `GPT-5.6 Sol`，旧快照安全迁移。 |
| `LIB-001`    | Library 首个页签为 `All`；关键字、Type、Status 取交集，并提供结果计数与清空筛选。                                                                                                                                |
| `LIB-002`    | Knowledge Graph 已替换为深色社区关系图：圆形节点按连接度分级、Community 可开关、搜索高亮、节点 Inspector、缩放/重置、拖动画布及 `EXTRACTED / INFERRED` 关系来源均可操作。                                        |
| `VIS-001`    | Test Management 与 Low Code Automation 保留业务结构，同时共用 Athena 的中性页面、靛蓝强调、白色圆角卡片、边框、按钮与响应式节奏。                                                                                |

### 复核证据

- 交互测试 `38 passed`；相关测试 `105 passed`；Web 全量 `230 passed`。
- ESLint、Prettier、架构边界检查、production build 与 `git diff --check` 通过。
- 桌面与 `390 × 844` 浏览器实测无横向溢出；图谱节点、关系和 Inspector 在两种尺寸下均可读。
- Console 未观察到 error 或 warning；图谱网格只用于实际关系画布。
- 图谱视觉与交互语义参考 Graphify 的社区、连接和节点探索模式，但未复制其实现，也未引入新的布局依赖。
- 本次结果仍是纯前端原型；Library 来源、图谱关系和执行状态不构成真实后端能力。

### 模型选择与导航标识补充复核

- 模型触发器不显示闪电；菜单包含 `GPT-5.6 Sol`、`GPT-5.6 Terra`、`GPT-5.6 Luna`、`GPT-5.5`、`GPT-5.4`，没有推理强度或速度模式。
- 选择 `GPT-5.6 Luna` 后发送首条消息，新建 Conversation 恢复默认 `GPT-5.6 Sol`；返回原 Conversation 后恢复 `GPT-5.6 Luna`。
- 桌面和 `390 × 844` 下模型触发器与菜单均保持在 Composer 内，无横向溢出；一级、二级导航中的 `A` 均使用白色背景且不污染按钮的可访问名称，紫色只表达一级入口的外层选中态。
- 本补充 TDD 红灯为 `5 failed / 71 passed`；定向绿灯为 `77 passed`，Web 全量为 `14 files / 234 tests passed`；ESLint、Prettier、架构边界与 production build 均通过。
- 白色徽标 follow-up 的样式契约测试先以 `1 failed` 捕获二级紫色背景，修改后定向测试 `1 passed`；浏览器计算样式确认两处均为纯白，一级入口取消选中后仍保持纯白。Web 全量 `14 files / 234 tests passed`，Console 无 error 或 warning。
- 模型命名 follow-up 的定向测试先以 `1 failed` 捕获旧名称和闪电，修改后定向测试 `1 passed`、交互测试 `41 passed`、Web 全量 `14 files / 234 tests passed`；桌面与 `390 × 844` 浏览器确认五个菜单项均使用 `GPT-` 前缀、闪电数量为 `0`，窄屏无横向溢出且 Console 无 error 或 warning。
- Athena 导航 follow-up 的定向测试先以 `1 failed` 捕获旧 `New Chat` 文案，修改后定向测试 `1 passed`、交互测试 `41 passed`、Web 全量 `14 files / 234 tests passed`；浏览器确认 `New chat` 默认透明且使用方框铅笔图标，当前项为 `12px` 浅灰整行圆角且无紫色侧边阴影，桌面和窄屏均无横向溢出，Console 无 error 或 warning。
