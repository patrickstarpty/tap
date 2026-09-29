# BrowserStack Test Management / Jira App 逐页分析与 TAP 设计映射

本报告研究公开文档中的产品行为，不代表登录实测或 BrowserStack 对 TAP 的接口承诺；所有 TAP 项均为设计建议。研究日期：2026-09-17。正式交互、技术归属与迁移规则见 [RFC-011 测试管理专题](../reference/2026-09-22-rfc-011-testing-execution-design.md#测试管理)。

## 核心结论与产品边界

TAP 最需要补齐的是独立用例资产库和手工执行闭环，不只是给生成计划增加更多字段。当前计划内嵌用例可以保留为旧版本快照，新设计应让需求、用例版本、计划版本、执行周期、执行实例、每次结果各自有稳定身份。BrowserStack 的 [三种用例模板](https://www.browserstack.com/docs/test-management/test-cases/create-test-cases)、[评审](https://www.browserstack.com/docs/test-management/test-cases/review-and-approve-test-cases)、[数据集](https://www.browserstack.com/docs/test-management/test-cases/test-datasets)和 [Jira 手工运行](https://www.browserstack.com/docs/test-management-jira-app/test-runs/create-manual-test-runs)给出了完整交互参照。

| 方面 | 独立 Test Management | 新 Test Management for Jira | TAP取舍 |
| --- | --- | --- | --- |
| 资产事实 | 项目、用例库、字段/模板、评审、版本、数据集、运行、计划 | Jira项目与issue内创作/执行/查看，同BrowserStack双向同步 | TAP AI是需求/用例/计划/手工结果事实方；Jira只是外部需求/缺陷和可选入口 |
| 安装与身份 | 直接使用BrowserStack；连接器各自认证 | 管理员安装，按Jira email关联BrowserStack账号，逐项目启用 | 身份映射和项目映射分开，未授权不能靠技术连接成功绕过 |
| 旧Jira Integration | Cloud app配置BrowserStack key；DC上传jar；文档写默认启用所有项目 | 新App写自动用户配置与管理员逐项目开启 | 两套说明不可揉成单一“Jira支持”承诺 |
| 分析 | 管理类widgets + 自动化分析视角 | Jira gadgets混合管理数据与自动化专属数据 | Insights统一ClickHouse事实；测试管理只显示操作摘要与分析深链 |
| 接口 | 有公开REST，多项写接口，但端点能力不齐 | 当前文档树没有独立完整CRUD参考 | 公开产品能力≠公共API；连接器逐操作验证 |

来源：[新App安装](https://www.browserstack.com/docs/test-management-jira-app/overview/getting-started)、[新App定位](https://www.browserstack.com/docs/test-management-jira-app/overview/what-is-test-management-for-jira)、[旧Integration App](https://www.browserstack.com/docs/test-management/jira/jira-app)、[API参考](https://www.browserstack.com/docs/test-management/api-reference/introduction)。

## 建议的完整交互

1. **用例库**：在TAP AI测试管理内提供“用例、计划、执行”工作区。用例可按文件夹与保存视图组织，支持标题/编号检索、筛选、批量操作、侧栏查看及版本对比。用例字段包含模板、前置条件、步骤及各步预期、数据引用、优先级/类型/负责人/标签、需求关系和自动化状态。Text用于检查清单，Steps用于手工细化，BDD用于现有Gherkin；已有BDD保存方式兼容。
2. **AI创作**：Tapper或测试管理均进入同一个后台生成任务。选择已授权资料版本→确认需求清单和范围→生成场景→查看来源片段→编辑/接受/拒绝→继续定向修改。接受的内容不被下一轮覆盖；维护旧用例要显示差异。BrowserStack允许刷新结束会话的限制不应继承；TAP使用既有持久任务与LangGraph恢复机制。[生成](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs)、[迭代](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/iterative-prompts)、[维护](https://www.browserstack.com/docs/test-management/browserstack-ai/test-case-maintenance-agent)。
3. **共享内容与数据**：共享步骤、共享前置条件、BDD背景独立管理；草稿可以采用新版本，已发布用例和开始执行的实例固定版本。数据集列定义、行身份、选行及预期值单独存储；展示“用例×数据行组合×配置”实际实例数，多个数据集笛卡尔积必须先预览和限额。不要把参数值直接复制出一批新的永久用例。[共享字段](https://www.browserstack.com/docs/test-management/test-cases/shared-fields)、[数据集](https://www.browserstack.com/docs/test-management/test-cases/test-datasets)。
4. **评审与发布**：作者提交→独立reviewer查看差异/来源/覆盖→批准、要求修改或拒绝（后两项必须理由）→发布版本。修改内容创建新版本和待审状态，不能复用旧批准。导入/API生成与UI创建一致执行发布门禁；计划引用case version。不要直接复制供应商特定电子签名方案，它不支持SSO/2FA且审计记录默认30天；TAP仅在明确合规需求时另行设计。[评审](https://www.browserstack.com/docs/test-management/test-cases/review-and-approve-test-cases)、[电子签名限制](https://www.browserstack.com/docs/test-management/test-cases/part-11-compliance)。
5. **计划与执行周期**：设计计划包含范围/风险/进入退出标准；执行周期包含日期/负责人/配置/用例版本实例清单。选例可筛选或AI推荐，开始前物化；每步填写实际结果/备注/附件/缺陷，聚合状态按规则计算。新回归周期可按上次失败/阻塞克隆，但结果重置未执行。关闭后保只读快照；更正采用追加审计，不覆盖旧证据。[手工周期](https://www.browserstack.com/docs/test-management-jira-app/test-runs/create-manual-test-runs)、[动态选例](https://www.browserstack.com/docs/test-management-jira-app/test-runs/dynamic-test-case-selection)、[结果](https://www.browserstack.com/docs/test-management-jira-app/test-runs/add-a-result)、[关闭](https://www.browserstack.com/docs/test-management-jira-app/test-runs/manage-test-run-state)。
6. **自动化映射与分析**：用例批准版本通过接口交给TAP，创建/关联脚本版本；调试由独立调试服务处理，Jenkins只承担正式执行。脚本发布、正式运行和重试各有身份，人工结果与自动运行统一到执行实例，不能由“自动化标记”推断已经运行。Insights按确认的需求版本计算设计覆盖、已执行覆盖和通过覆盖，保留样本数、时间窗口、配置和证据。自动化覆盖与需求覆盖分开。

## 技术职责与迁移

| 数据/流程 | 事实所有者 | 跨产品交换 |
| --- | --- | --- |
| 来源版本、需求清单、用例与步骤版本、共享内容、数据集、计划及评审 | TAP AI / MySQL，文件在其对象存储；知识检索Milvus | 发布后的只读版本快照、项目权限、引用与变更通知 |
| 手工执行周期、实例、步骤结果、人工缺陷关系 | TAP AI测试管理 | 结果事件/更正事件/证据引用到Insights |
| 自动化脚本、设备/浏览器调试、正式运行和attempt | TAP自动化，正式调度Jenkins，调试服务独立 | 稳定实例ID + 用例版本 + 脚本版本 + 配置 + 数据行 + attempt回执 |
| 指标、趋势、质量规则与数据完整性 | TAP Insights / ClickHouse | 原始事实可重放；向TAP AI返回授权指标及证据 |
| Jira/ADO等外部实体 | 来源系统 | connector存provider/host/project/external-ID/更新时间/权限/同步状态；各方只写自己的数据库 |

迁移现有计划内嵌BDD用例时，以 `(原计划ID, revision, 用例原ID)` 建立可重跑映射；创建独立case及不可变version，计划版本保存引用。旧快照和生成来源保持可查，不能按相同标题直接合并。先加只读映射和一致性校验，再将新编辑/发布转到资产API。没有手工结果历史就显示未接入，不补造结果。跨项目复制生成新ID和来源关系，不携带旧批准/旧通过结果。

导入先预检字段/ID/用户/权限/附件→展示映射与不支持项→后台逐项导入→失败重试→计数校验。Cloud/Data Center必须是连接能力的一部分：Xray两部署型均只承诺用例迁入；Zephyr Scale/Essential Cloud只用例，Essential Server/DC才包括用例、运行、计划，认证也不同。[Xray](https://www.browserstack.com/docs/test-management/quick-import/xray)、[Zephyr](https://www.browserstack.com/docs/test-management/quick-import/zephyr)。Jira App迁移页的通用日志出现运行/计划数量，不能扩大成任何来源都支持；Zephyr Scale/Essential Cloud API迁不了附件，Essential Server页却明确支持附件，必须按来源与部署型分别承诺。TestRail主教程称仅Cloud，但排障页又写On-Prem≥7.5及附件cookie认证，应标支持待验证；运行/里程碑默认近一年可申请扩展且受订阅保留期限制；qTest默认近两年release执行，release→plan、cycle→run、源run→case execution。Xray Server/DC要求Jira DC9.x及Xray6.x–8.x；Essential Server要求Jira DC≥10.3.16及插件≥10.2.2，周期可选近一年。[Jira Quick Import](https://www.browserstack.com/docs/test-management-jira-app/quick-import)。

## 公开API边界

| 能力 | 本次正文确认 | 不能推断的部分 |
| --- | --- | --- |
| 项目/文件夹/用例 | 多种CRUD、批量、归档和历史接口 | 不把操作页所有跨项目move/copy/UI复用能力视为公开API等价 |
| 字段/配置 | 自定义字段CRUD；配置list/get/create | 配置更新/删除未在该页公开 |
| 计划 | 父计划list/create/update/get、运行与附件查询；子计划CRUD | 父计划删除不能因子计划存在DELETE就类推 |
| 评审 | reviewer名单/操作、send_for_review、项目allowed-reviewers | 实际批准、电子签名不能只靠写review_status或UI推断API可调用 |
| 用例版本 | history及指定版本快照；稳定版本号 | 快照字段约64KB截断，Free/Team Pro只30天；TAP须自有固定快照 |
| 运行 | 创建/查询/关闭/删除/克隆/更新；增删实例202异步 | POST update替换集合，异步受理不是完成；克隆可能短暂读到0用例 |
| 结果 | 按运行/用例查询，单/批/步骤新增及PATCH | 单次最多300 unique用例ID；自定义状态须先配置；bulk步骤+附件不支持 |
| 附件 | 文件归属、上传/删除、结果multipart | 50MB文件上限，部分绑定失败与结果保存分开；下载URL可过期 |
| Jira Cloud/DC | Cloud OAuth/PAT；自托管host/token，私网Local Binary；DC插件jar | 未发现新Jira App的独立完整公开CRUD；Jira REST不等于插件资产API |

Jenkins/Azure CI教程采用旧`/api/v1/import/results/xml/junit`与API-TOKEN上传报告；公开JSON API采用HTTP Basic username/access-key，不能混用认证，也不能从教程概述推断测试管理拥有CI调度权。[Jenkins](https://www.browserstack.com/docs/test-management/integrations/jenkins)、[Azure](https://www.browserstack.com/docs/test-management/integrations/azure)。

重要连接器约束：普通分页30–300，但global search固定30；step_id稳定而step_index会随重排改变；run `fetch_steps=true`只返回前30步骤且不支持分页；429按group共享配额300/min并等待60秒。所有这些是具体端点限制，应记录为能力矩阵并做合约测试，不能由一个统一客户端假定全部支持。[用例API](https://www.browserstack.com/docs/test-management/api-reference/test-cases)、[运行API](https://www.browserstack.com/docs/test-management/api-reference/test-runs)、[结果API](https://www.browserstack.com/docs/test-management/api-reference/test-results)、[分页](https://www.browserstack.com/docs/test-management/api-reference/pagination)、[限流](https://www.browserstack.com/docs/test-management/api-reference/rate-limit-for-api-calls)。

## 文档冲突与TAP取舍

| 冲突/限制 | 证据 | TAP取舍 |
| --- | --- | --- |
| 跨项目move段落有“只能copy”又有“move保ID”；folder move一处原件保留一处删除 | J025/J026，对应独立产品页也需分别看最新正文 | 不继承矛盾；定义项目内move、跨项目copy的明确语义 |
| 共享步骤/字段修改传播、删除从全部关联例消失 | J032/T177 | 发布与运行固定版本；删除只停用新引用 |
| 去重archive/merge会删源执行；discard段又误写删执行、末句称用例不变 | T049，与API unarchive保history描述亦需谨慎区分 | 去重必须人工确认，保别名/旧版本/历史证据 |
| AI来源表允许图片，限制段又说嵌入图未语义解析；BDD区提供数据编辑却说BDD不支持datasets；多选issue步骤与单ticket会话上限并存 | T035 | 按保守边界展示未覆盖内容，不许以成功上传等同成功理解 |
| TestRail主教程仅Cloud，排障页给On-Prem≥7.5与密码附件方案 | T136/T218 | 标明文档冲突，按真实部署做连接合约验证后承诺 |
| public含义在报告页不同：执行报告组织可见，普通分享链接匿名可见；表格临时过滤不改汇总 | T153/T154/T156/T158 | 分享显式private/org/link-public三种策略；筛选范围和指标口径一同展示 |
| 三模板创建页与“两系统模板”自定义模板说明不完全一致 | T004/T169/J027 | TAP自身schema明确Text/Steps/BDD，转换有校验 |
| 用例Active/Draft/Archived、API review枚举及run业务状态不是同一维度 | T010/T017/T023/J027/J044 | 资产状态、审批状态、周期状态、测试结果、技术任务状态分开 |
| “Jira双向同步”不说明对象冲突、删除、历史及所有字段写权限 | T109–T115/J010 | 每类实体指定事实所有者、幂等键、水位、冲突与补偿；禁止跨产品写DB |
| 新App/旧Integration安装身份、项目默认启用不同 | J007/T110 | 建立独立产品/连接器矩阵，不把部署体验自动合并 |
| 作者/评审电子签名仅密码账户，审计导出非自助且默认30天 | T174/T175 | 不继承与TAP OIDC冲突的方案，不承诺合规认证 |

优先级：先用例库/版本/评审/需求覆盖→手工周期/结果/附件/配置与数据快照→自动化映射/幂等回执→Insights统一分析。自定义模板编辑器、跨项目实时共享、匿名分享、探索测试、Jira嵌入UI、AI去重可后置；这些不应拖慢基础闭环。

## 覆盖方法与记录

已发现 295 个去重 URL：**269 篇正文已读、22 个官方跳转、4 个 HTTP 404**。失败页和跳转不计作正文。初次并发遇到 HTTP 429 后停止，后续单路每次至少间隔 5 秒补读；最终没有限流遗留或待研读正文。

| 产品 | URL 数 | 正文已读 | 跳转 | 404 |
| --- | --- | --- | --- | --- |
| 独立 Test Management | 244 | 219 | 21 | 4 |
| Test Management for Jira | 51 | 50 | 1 | 0 |

两套sidebar分别发现196/44个URL，官方sitemap发现290个URL；正文递归新增及校验后形成清单。仅扩展两个产品前缀；外链引用不展开其他产品全树。稳定ID在首次清单按URL排序分配，后发现页面追加，不重排既有编号。

## 逐页对照

| ID | 原URL / 标题 | 阅读状态与关键能力、边界 | TAP设计映射 |
| --- | --- | --- | --- |
| J001 | [Test Management for Jira](https://www.browserstack.com/docs/test-management-jira-app) | Jira App公开入口；具体能力依子页，不替代独立产品API。 | 测试管理仍归TAP AI，Jira作为可选工作入口。 |
| J002 | [Dashboards and gadgets](https://www.browserstack.com/docs/test-management-jira-app/dashboards) | Jira dashboard添加/配置28个BrowserStack gadgets；每组件时间窗及刷新周期。 | TAP在Insights保存视图，不复制Jira容器。 |
| J003 | [BrowserStack gadgets reference](https://www.browserstack.com/docs/test-management-jira-app/dashboards/gadgets-list) | 28 gadgets分管理类和自动化分析类；后者无手工运行数据时为空。 | 手工/自动化数据覆盖明确，不能空白冒充0。 |
| J004 | [Manage dashboards and gadgets](https://www.browserstack.com/docs/test-management-jira-app/dashboards/manage-dashboards) | Jira权限控制分享/编辑，默认私有；垃圾箱60天后永久删。 | 视图共享与源数据权限分别检查。 |
| J005 | [User Access Control in Test Management](https://www.browserstack.com/docs/test-management-jira-app/overview/access-control) | RBAC/UDAC/地区访问控制概览，细则跳外部页。 | 这里只证明存在，不推断Jira App独立权限API。 |
| J006 | [Test Management demo](https://www.browserstack.com/docs/test-management-jira-app/overview/demo) | 文字仅演示视频入口，未登录观看实测。 | 不将演示等同验收。 |
| J007 | [Install and configure the BrowserStack Test Management for Jira](https://www.browserstack.com/docs/test-management-jira-app/overview/getting-started) | Jira管理员安装并逐项目启用；按Jira邮箱自动建/关联BrowserStack账号。 | 身份映射/权限失败需显式状态，不双写两库。 |
| J008 | [Manage manual test runs](https://www.browserstack.com/docs/test-management-jira-app/overview/manual-test-runs) | 手工运行概览，分派人员须先登录；正文误混用例管理描述。 | 以详细手工运行页面定义行为。 |
| J009 | [Redirecting…](https://www.browserstack.com/docs/test-management-jira-app/overview/reports-and-dashboards) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management-jira-app/dashboards) | 只计目标正文，不重复声称新功能。 |
| J010 | [What is Test Management for Jira?](https://www.browserstack.com/docs/test-management-jira-app/overview/what-is-test-management-for-jira) | Jira内创作/执行/结果/缺陷/报告，双向同步至独立Test Management。 | 将Jira集成作为同一资产入口，不另建事实库。 |
| J011 | [Import test data with Quick Import](https://www.browserstack.com/docs/test-management-jira-app/quick-import) | Jira内Quick Import Xray/Zephyr/Essential，保ID或留原ID映射；附件支持依来源。 | 导入任务留每对象结论和数据缺口。 |
| J012 | [Quick Import from Xray](https://www.browserstack.com/docs/test-management-jira-app/quick-import/xray) | Xray用Jira+client ID/secret连接，后台任务/日志/重试，导入时勿改源结构。 | 快照或记录水位，完成后校验数量和来源ID。 |
| J013 | [Quick Import from Zephyr](https://www.browserstack.com/docs/test-management-jira-app/quick-import/zephyr) | Zephyr token导入；其API不能迁移附件。 | 附件缺口保留清单，不报全量成功。 |
| J014 | [Quick Import from Zephyr Essential](https://www.browserstack.com/docs/test-management-jira-app/quick-import/zephyr-essential) | Essential token导入；API同样无法迁移附件。 | 按产品/部署型别能力矩阵执行迁移。 |
| J015 | [Reports](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports) | 六报告：运行摘要/详情、需求、用例活动、计划、探索。 | 基础报告按使用目标分类，跨产品数字统一Insights口径。 |
| J016 | [Advanced filters in reports](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/advanced-filters-in-reports) | 报告高级过滤按状态/类型/优先级/人员/自动化/时间，生成时生效。 | 报告留筛选快照和数据截至时间。 |
| J017 | [Create a report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/create-a-report) | 创建/编辑/删报告；日报周报用UTC，默认开启schedule；可动态关联未来运行。 | TAP调度默认显式开启，记录时区与收件范围。 |
| J018 | [Exploratory session summary report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/exploratory-session-summary-report) | 探索报告计时长、日志结果与发现缺陷，独立于脚本运行。 | 探索数据单独维度，不能提高脚本需求覆盖。 |
| J019 | [Requirement traceability report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/requirement-traceability-report) | 需求→用例→最近运行结果，可含缺陷/配置/计划；最多2万行、epic最多200子项。 | 分母用确认需求版本，历史结果不代替新版本覆盖。 |
| J020 | [Share and download reports](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/share-and-download-reports) | 私有组织链接/互联网公开链接、邮件、CSV/PDF；CSV最多2万行。 | 下载截断/权限可见，匿名分享另行配置。 |
| J021 | [Test case activity report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/test-case-activity-report) | 用例新增/改删、运行覆盖、自动化与AI来源比例。 | 创作方式与质量效果分开统计。 |
| J022 | [Test plan summary report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/test-plan-summary-report) | 计划逾期/近期完成、所含运行和各结果进度。 | 计划进度来自执行实例，非生成任务完成度。 |
| J023 | [Test run detailed report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/test-run-detailed-report) | 运行明细、结果趋势和关联问题，逐例核对。 | 下钻到固定caseversion/配置/数据行/证据。 |
| J024 | [Test run summary report](https://www.browserstack.com/docs/test-management-jira-app/reports-and-analytics/reports/test-run-summary-report) | 运行active/closed、结果分布及问题优先级/状态摘要。 | 业务周期状态、技术attempt、测试结果三层分离。 |
| J025 | [Manage and organize test cases with copy or move](https://www.browserstack.com/docs/test-management-jira-app/test-cases/copy-and-move-test-cases) | 复制独立新ID；move保ID的说明与前段只能copy跨项目相冲突。 | TAP项目内move保ID，跨项目先copy，受限操作明确策略。 |
| J026 | [Copy and move folders across projects](https://www.browserstack.com/docs/test-management-jira-app/test-cases/copy-move-folders-across-projects) | 文件夹跨项目复制/移动；共享步骤复制；move原件是否保留正文矛盾。 | 迁移预览字段/权限/引用，禁止假定历史随copy转移。 |
| J027 | [Test Cases: The building blocks of testing](https://www.browserstack.com/docs/test-management-jira-app/test-cases/create-test-cases) | Text/Steps/BDD，标题必填及状态/owner/优先级/类型/自动化/需求/估算。 | 从计划内BDD拆出独立用例库与三模板。 |
| J028 | [Edit or Delete test cases](https://www.browserstack.com/docs/test-management-jira-app/test-cases/edit-and-delete-test-cases) | 详情侧栏编辑/删除，步骤与共享步骤拖拽；bulk delete正文缺步骤。 | 草稿编辑固定版发布，步骤稳定ID不依顺序。 |
| J029 | [Export test cases](https://www.browserstack.com/docs/test-management-jira-app/test-cases/export-test-cases) | CSV全类型，.feature仅BDD；可跨页选中导出。 | 显式选择范围/版本/格式，导出标来源与限制。 |
| J030 | [Filter Test Cases](https://www.browserstack.com/docs/test-management-jira-app/test-cases/filter-test-cases) | 字段间AND/OR，多值包含/排除；folder始终AND。 | 过滤表达式统一前后端语义并在选例处复用。 |
| J031 | [Manage test cases](https://www.browserstack.com/docs/test-management-jira-app/test-cases/manage-test-cases) | 用例管理概览含编辑/组织/导出/过滤/共享步骤。 | 独立资产库承载完整生命周期。 |
| J032 | [Shared steps in test cases](https://www.browserstack.com/docs/test-management-jira-app/test-cases/shared-steps) | 共享步骤修改实时传播；可跨项目copy/clone，删除从所有关联移除。 | 共享步骤独立版本，运行/发布用例冻结引用。 |
| J033 | [Add Test Runs to Test Plan](https://www.browserstack.com/docs/test-management-jira-app/test-plans/add-test-runs-to-test-plan) | 计划内建运行或关联已有运行。 | 设计计划与执行周期引用关系显式，复用已批准用例。 |
| J034 | [Create test plans](https://www.browserstack.com/docs/test-management-jira-app/test-plans/create-test-plans) | 计划有标题/日期/描述，最多10附件各50MB，初始无运行。 | 测试设计文档与执行组织同入口不同对象。 |
| J035 | [Delete a test plan](https://www.browserstack.com/docs/test-management-jira-app/test-plans/delete-test-plan) | 计划可永久删除。 | 归档优先，保已运行记录的计划快照。 |
| J036 | [Edit a test plan](https://www.browserstack.com/docs/test-management-jira-app/test-plans/edit-test-plan) | 计划可改标题/日期/描述/关联运行，即时保存。 | 草稿编辑与已冻结执行范围分开。 |
| J037 | [Test Plans: The strategy behind testing](https://www.browserstack.com/docs/test-management-jira-app/test-plans/what-is-test-plan) | Active/Completed计划，Start/Complete及逾期、15天最近结果。 | 计划状态与周期结果分别计算。 |
| J038 | [Add a result](https://www.browserstack.com/docs/test-management-jira-app/test-runs/add-a-result) | 步骤全Pass→Passed；任Fail→Failed；无Fail有Blocked→Blocked；Skip/Retest→In Progress。 | 聚合规则显式版本化，业务结果不由模型决定。 |
| J039 | [Add notes and attachments](https://www.browserstack.com/docs/test-management-jira-app/test-runs/add-notes-and-attachments) | 用例和步骤可记录备注/附件/问题。 | 手工结果与证据在TAP AI，Insights接事实引用。 |
| J040 | [Clone test runs](https://www.browserstack.com/docs/test-management-jira-app/test-runs/clone-test-runs) | 按全部或结果克隆，可复制人员/tag/issue；新run总为active。 | 回归复用范围但新实例初始未执行，旧结果不带成功。 |
| J041 | [Configurations](https://www.browserstack.com/docs/test-management-jira-app/test-runs/configurations-in-a-test-run) | OS/browser/device配置使每用例展开多份；自定义配置不能启动Live。 | 配置仅标识环境，能执行与否由TAP适配器验证。 |
| J042 | [Create a manual test run](https://www.browserstack.com/docs/test-management-jira-app/test-runs/create-manual-test-runs) | 手工运行选择用例、配置、分派、tag和问题。 | 业务周期创建后冻结caseversion/配置/数据行。 |
| J043 | [Dynamic test case selection](https://www.browserstack.com/docs/test-management-jira-app/test-runs/dynamic-test-case-selection) | 动态选择自动增补未来匹配例；已加入例即使不再匹配仍保留。 | 动态范围在开始前物化；运行中变化显式确认。 |
| J044 | [Manage test run state](https://www.browserstack.com/docs/test-management-jira-app/test-runs/manage-test-run-state) | Active可改结果、Closed锁结果。 | 关闭周期保快照；修正走追加更正记录。 |
| J045 | [Search and filter](https://www.browserstack.com/docs/test-management-jira-app/test-runs/search-and-filter) | 运行与运行内用例筛选，批量加结果/分派/移除；状态筛选可传播。 | 批量动作预览实际实例数与筛选来源。 |
| J046 | [Share your test run publicly](https://www.browserstack.com/docs/test-management-jira-app/test-runs/share-test-runs) | 可启用匿名公共运行链接。 | 默认不公开，分享权限/撤销/到期可审计。 |
| J047 | [View test cases in a test run detail page](https://www.browserstack.com/docs/test-management-jira-app/test-runs/view-test-cases-in-a-test-run) | 目录与平铺视图，搜索自动切平铺。 | 用例库/运行详情共享搜索语义但不同结果状态。 |
| J048 | [Troubleshoot](https://www.browserstack.com/docs/test-management-jira-app/troubleshooting) | 故障排查入口，细节在子页。 | 连接健康与业务故障分别显示。 |
| J049 | [How to generate a HAR file for support](https://www.browserstack.com/docs/test-management-jira-app/troubleshooting/generate-a-har-file) | 支持HAR采集、刷新前启用preserve log。 | 日志导出清理令牌/个人数据后提供。 |
| J050 | [Installation and project configuration issues](https://www.browserstack.com/docs/test-management-jira-app/troubleshooting/install-and-configure-issues) | 版本核对、项目启停刷新配置排查可见性。 | 连接版本/项目启用状态可诊断。 |
| J051 | [Resolve access issues for a specific user](https://www.browserstack.com/docs/test-management-jira-app/troubleshooting/resolve-access-issue) | 单用户访问需Jira与BrowserStack相同email、正确team和产品授权。 | 显式身份映射和权限错误，不能只验证技术连通。 |
| T001 | [Test Management](https://www.browserstack.com/docs/test-management) | 独立产品入口：集中管理手工/自动化测试，导入导出、关联问题、API与报告入口；入口不代替操作页。 | 沿用TAP AI测试管理一级入口，补用例库与执行周期。 |
| T002 | [Custom Form Fields](https://www.browserstack.com/docs/test-management/advanced-features/custom-form-fields) | 自定义字段九类、必填与项目分配；管理员管理，dropdown最多30个值集合、每项目一个集合。 | 字段定义、项目绑定、取值分开存储；停用保留历史值。 |
| T003 | [Custom Result Fields](https://www.browserstack.com/docs/test-management/advanced-features/custom-result-fields) | 结果字段与用例字段分开；可加结果状态但系统状态不可改删。 | 结果扩展字段独立schema，状态需映射统一分析口径。 |
| T004 | [Custom test case templates](https://www.browserstack.com/docs/test-management/advanced-features/custom-test-case-templates) | 自定义模板可配置字段/顺序/默认项目，系统和默认模板不可删除；Team Pro能力。 | 支持Text/Steps/BDD与版本化模板，先补基础模板再考虑自由表单。 |
| T005 | [System Defined Form Fields](https://www.browserstack.com/docs/test-management/advanced-features/system-defined-form-fields) | 系统字段固定，优先级/类型可扩展；删枚举使原值为空，默认枚举不可删。 | 枚举停用优于清空既有业务数据；版本记录显示原始值。 |
| T006 | [Attachments](https://www.browserstack.com/docs/test-management/api-reference/attachments) | 用例/结果附件list/add/delete；单文件50MB，inline图片引用独立data-id。 | 对象存储+归属/授权/扫描元数据；证据引用不可裸露。 |
| T007 | [Authentication](https://www.browserstack.com/docs/test-management/api-reference/authentication) | REST使用username/access-key的HTTP Basic Auth。 | 外部连接凭证只在后端保存；TAP自身接口使用统一身份。 |
| T008 | [Configurations](https://www.browserstack.com/docs/test-management/api-reference/configurations) | 公开配置list/get/create；OS/浏览器/设备组合，不见配置update/delete。 | 建立能力清单，不以UI能力推断CRUD。 |
| T009 | [Custom fields](https://www.browserstack.com/docs/test-management/api-reference/custom-fields) | 自定义字段list/create/PATCH/delete，项目映射与枚举校验；说明大小写存在不一致。 | 按实际schema验证，连接器保留原值并显式转换。 |
| T010 | [System-defined Enum values](https://www.browserstack.com/docs/test-management/api-reference/enums) | 用例状态含Active/Draft/In Review/Rejected/Outdated；系统值不可改删。 | 资产生命周期、评审状态、执行结果分别建模。 |
| T011 | [Folders](https://www.browserstack.com/docs/test-management/api-reference/folders) | 文件夹CRUD及同项目move；删除级联相关用例；RBAC约束。 | 文件夹只组织资产，归档/删除影响预览并保运行快照。 |
| T012 | [Global search](https://www.browserstack.com/docs/test-management/api-reference/global-search) | 跨授权项目搜索标题/ID/步骤/前置/说明；至少3字符、固定30页容量。 | 搜索始终权限过滤；精准ID定位与全文检索分开。 |
| T013 | [Test Management API reference](https://www.browserstack.com/docs/test-management/api-reference/introduction) | REST/JSON入口描述项目、运行和结果，实际写操作以子页为准。 | 建立逐端点契约，不根据简介承诺完整API。 |
| T014 | [Pagination](https://www.browserstack.com/docs/test-management/api-reference/pagination) | 普通分页p/page_size 30–300，next=null终止；不同端点分页参数不统一。 | 连接器逐端点分页、增量水位与完整性校验。 |
| T015 | [Project](https://www.browserstack.com/docs/test-management/api-reference/projects) | 项目list/get/create/PATCH/delete；删除不可恢复并移除关联数据。 | 保项目身份映射，不从外部删除直接连带删除内部历史。 |
| T016 | [Rate limits for API calls](https://www.browserstack.com/docs/test-management/api-reference/rate-limit-for-api-calls) | 外部REST按group限制300/min；429要求等60秒后再请求。 | 限速器共享配额、退避、可重试任务和审计。 |
| T017 | [Reviewers](https://www.browserstack.com/docs/test-management/api-reference/reviewers) | 分配/增删/替换reviewers、send_for_review、项目allowed-reviewers；case与reviewer各有review_status。 | 评审者名单与每人决定分开，不用单布尔approved。 |
| T018 | [Status code](https://www.browserstack.com/docs/test-management/api-reference/status-code) | 使用标准HTTP状态并附JSON错误信息。 | 业务错误、权限失败、限流和可重试故障分别处理。 |
| T019 | [Sub test plan API endpoints](https://www.browserstack.com/docs/test-management/api-reference/sub-test-plans) | 子计划公开CRUD；属于父计划，可关联运行。 | 先让计划组织周期，层级需求明确后再扩子计划。 |
| T020 | [Test cases](https://www.browserstack.com/docs/test-management/api-reference/test-cases) | 用例CRUD/批量/归档/移动/BDD导出/历史/评论读取；bulk create>30异步，history快照约64KB截断及套餐窗口。 | 独立case/version；固定内容、稳定步骤ID、增量同步与异步结果核对。 |
| T021 | [Test plans](https://www.browserstack.com/docs/test-management/api-reference/test-plans) | 父计划list/create/update/get及运行/附件读取；公开此页未见delete端点。 | 父计划API能力逐项验证，不假定与子计划同构。 |
| T022 | [Test results](https://www.browserstack.com/docs/test-management/api-reference/test-results) | 结果查询/新增/批量/步骤/PATCH；最多300用例ID、稳定step_id优于index，附件支持有组合限制。 | 手工结果由TAP AI保存；事件入Insights，附件失败和业务结果分开展示。 |
| T023 | [Test runs](https://www.browserstack.com/docs/test-management/api-reference/test-runs) | 运行创建/查询/克隆/更新/关停；POST替换用例，增删接口202异步；fetch_steps仅前30且无分页。 | 业务周期固定实例清单；技术attempt独立；只按确认事件更新状态。 |
| T024 | [Update BDD test case](https://www.browserstack.com/docs/test-management/api-reference/update-bdd-test-case) | BDD仅可更新feature/scenario/background，不能混写普通test_steps。 | BDD与步骤模板用明确联合类型及转换预览。 |
| T025 | [Azure integration with Test Management](https://www.browserstack.com/docs/test-management/azure-devops/ado-integration) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T026 | [Manage your Azure DevOps integration with Test Management](https://www.browserstack.com/docs/test-management/azure-devops/azure-devops-app) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T027 | [Link Test Results to Azure work items](https://www.browserstack.com/docs/test-management/azure-devops/link-test-results) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T028 | [Link Azure DevOps work items to test cases](https://www.browserstack.com/docs/test-management/azure-devops/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T029 | [Link Azure work items to test runs](https://www.browserstack.com/docs/test-management/azure-devops/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T030 | [Manage test cases](https://www.browserstack.com/docs/test-management/azure-devops/manage-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T031 | [Manage test results](https://www.browserstack.com/docs/test-management/azure-devops/manage-test-results) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T032 | [Manage test runs](https://www.browserstack.com/docs/test-management/azure-devops/manage-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T033 | [Generate test artifacts with BrowserStack AI Agents](https://www.browserstack.com/docs/test-management/browserstack-ai) | AI覆盖生成、维护、数据、去重、选例和自动化转化，各自独立流程。 | 共享模型/检索基础，业务入口和接受操作仍在测试管理。 |
| T034 | [Test case generator agent](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases) | 生成器从需求/图片/问题生成Text/Steps/BDD草稿。 | 资料确认→需求清单→草稿→逐项接受，不跳过评审发布。 |
| T035 | [Generate test cases from requirements and links](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs) | 多来源生成带来源片段和场景分组；自定义字段不生成，上传/来源有上限，浏览器刷新停止。 | 复用知识库已解析版本并可离页恢复；明确完整性/冲突，不复制其会话脆弱性。 |
| T036 | [Redirecting…](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-jira-confluence-links) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs) | 只计目标正文，不重复声称新功能。 |
| T037 | [Redirecting…](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-requirement-document-image-prompt) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs) | 只计目标正文，不重复声称新功能。 |
| T038 | [Generate test cases using the create test case form](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-the-create-test-case-form) | 在创建表单用摘要/前置条件生成并编辑；自定义模板只部分支持。 | 生成建议进入同一编辑器，保留用户已有字段。 |
| T039 | [Generate test cases using prompt in the listing view](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/generate-test-cases-from-the-listing-view) | 列表短提示快速生成，加入前可编辑。 | 快捷草稿仍可回溯来源和正式发布。 |
| T040 | [Integrate Confluence with Test Management](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/integrate-confluence) | Confluence通过OAuth连接、选择有权页面提供上下文。 | 后端连接器按用户授权取内容并固定来源版本。 |
| T041 | [Integrate Figma with Test Management](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/integrate-figma) | Figma读取frame上下文，每次最多50 frame。 | 设计稿作为需求证据；权限和解析遗漏可见。 |
| T042 | [Refine your test cases with Iterative prompting](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/iterative-prompts) | 接受锁定、拒绝反馈、选中定向修改、未选保留；迭代仅Type/Priority可改，模板/语言锁定。 | 自然语言修改输出差异，保护人工接受内容；不可静默改检查条件。 |
| T043 | [Test case generator agent - FAQs](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/test-case-generator-faq) | 局部范围靠prompt，无文档区块选择UI；多文档不承诺原文顺序。 | TAP提供显式需求范围选择及覆盖缺口，避免只靠prompt。 |
| T044 | [Generate Test Runs using AI](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-runs) | 从Jira故事和已有运行建议用例生成运行。 | AI仅推荐候选及依据，用户确认后冻结周期范围。 |
| T045 | [Test data generator agent](https://www.browserstack.com/docs/test-management/browserstack-ai/generate-test-datasets) | AI参数化、边界/负例数据、Outcome列；保存数据快照，每行展开运行实例。 | 数据集独立版本、行ID和预期；运行绑定快照。 |
| T046 | [Test Deduplication Agent](https://www.browserstack.com/docs/test-management/browserstack-ai/identify-duplicate-test-cases) | 精确/语义重复建议含相似度和原因；不等同ID冲突检查。 | 去重建议进入审阅，不自动删除测试覆盖与历史。 |
| T047 | [De-duplicate test cases after quick import](https://www.browserstack.com/docs/test-management/browserstack-ai/identify-duplicate-test-cases/deduplicate-testcases-after-import) | 导入后全项目语义去重与导入ID冲突是独立过程。 | 结构校验先行，内容相似度后审。 |
| T048 | [Identify duplicate test cases - FAQs](https://www.browserstack.com/docs/test-management/browserstack-ai/identify-duplicate-test-cases/identify-duplicate-test-cases-faq) | 去重定时扫描显示时间，discard针对特定pair，新pair仍会提示。 | 建议记录来源版本和忽略原因，版本变化重新评估。 |
| T049 | [Review and resolve duplicate test cases](https://www.browserstack.com/docs/test-management/browserstack-ai/identify-duplicate-test-cases/review-resolve-duplicates) | 并排比较/方向/合并预览；源归档90天后删，源执行立删；discard文案自相矛盾。 | TAP合并保留别名与历史，绝不套用立删执行证据。 |
| T050 | [Low Code Authoring Agent](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) | AI由手工步骤转低代码脚本，输入URL/登录/本地连接；beta且跳转LCA查看。 | TAP AI提交已批准用例版本，TAP工作台调试后发布独立脚本版本。 |
| T051 | [Redirecting…](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-automation) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) | 只计目标正文，不重复声称新功能。 |
| T052 | [review-and-resolve-duplicates](https://www.browserstack.com/docs/test-management/browserstack-ai/review-and-resolve-duplicates) | HTTP Error 404: Not Found | 正文未核实，不据URL推断能力。 |
| T053 | [Test Case Maintenance Agent](https://www.browserstack.com/docs/test-management/browserstack-ai/test-case-maintenance-agent) | 按选定文件夹维护旧例并补新例，差异审阅后接受；仅系统字段，维护与生成数据互斥。 | 资料变更标影响、保旧版、AI差异草稿，按确定scope复核。 |
| T054 | [Test selection agent](https://www.browserstack.com/docs/test-management/browserstack-ai/test-selection-agent) | Jira上下文匹配现有仓库建立关联运行，生成后可改。 | AI选例不能定义覆盖率分母或自动批准计划。 |
| T055 | [Project dashboard](https://www.browserstack.com/docs/test-management/dashboard/project-dashboard) | 项目总览/自动化健康/唯一错误三种视角，分别筛选保存。 | 操作态在测试管理，跨运行分析链接至TAP Insights。 |
| T056 | [Project insights dashboard](https://www.browserstack.com/docs/test-management/dashboard/project-insights) | 模板+保存视图、手工/自动化widgets、布局/过滤/分享；公开视图与互联网公开链接不同。 | Insights保存视图与权限分离，默认项目授权而非匿名链接。 |
| T057 | [Active test runs](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/active-test-runs) | 活动运行结果分布和筛选。 | 显示业务周期当前进度，不混技术任务状态。 |
| T058 | [Automation coverage widget](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/automation-coverage-widget) | 自动化覆盖可按全部或选择的自动化状态定义分母。 | 覆盖指标公开分子/分母，区分可自动化与需求覆盖。 |
| T059 | [Closed test runs](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/closed-test-runs) | 关闭运行数量趋势及分段比较。 | 周期关闭事件形成事实，重跑不冒充新增覆盖。 |
| T060 | [Defects logged widget](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/defects-logged) | 关联缺陷随时间趋势。 | 唯一缺陷ID去重，缺陷量不是失败次数。 |
| T061 | [Results from closed test runs widget](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/results-from-closed-test-runs) | 已关闭运行结果按时间/状态堆叠。 | 固定关闭快照，历史修正显式审计。 |
| T062 | [Statistics cards](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/stat-card) | 单指标卡支持独立分子/分母过滤，未设置会得到无意义100%。 | 指标定义先于图表，零分母/缺数据展示未知。 |
| T063 | [Test cases breakdown](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/test-cases-breakdown) | 用例库按属性分布为all-time，单图最多20属性值。 | 用例库存量与期间执行量不可混算。 |
| T064 | [Test execution distribution](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/test-execution-distribution) | 执行按人员分布，手工/自动化筛选不同，可下钻项目和运行。 | 用于分配/阻塞定位，不以执行量推断个人绩效。 |
| T065 | [Test executions](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/test-executions) | 执行趋势计每次执行而非唯一用例；手工可叠计划日期，最多5分段。 | ClickHouse区分实例/attempt/唯一用例口径并可回证据。 |
| T066 | [Trend of test cases widget](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/trend-of-test-cases) | 用例创建/变化数量趋势。 | 资产事件单独入Insights，勿与执行成功量混淆。 |
| T067 | [Redirecting…](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/type-of-test-cases) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/test-cases-breakdown) | 只计目标正文，不重复声称新功能。 |
| T068 | [Create an Exploratory Session](https://www.browserstack.com/docs/test-management/exploratory-sessions/create-an-exploratory-session) | 探索任务含mission/timebox/配置/人员/需求/附件，首次日志才进入进行中。 | 作为后续可选能力，不阻塞基础用例和手工执行。 |
| T069 | [Edit, close, and delete an Exploratory Session](https://www.browserstack.com/docs/test-management/exploratory-sessions/edit-close-delete-exploratory-sessions) | 探索关闭即只读不可重开；删除失去日志但不删除外部issue。 | 如实施探索，保不可变结项记录与另开会话。 |
| T070 | [Link Issues to an Exploratory Session](https://www.browserstack.com/docs/test-management/exploratory-sessions/link-issues-to-an-exploratory-session) | 探索会话关联需求/缺陷；关闭后不能改链接。 | 来源关系与执行证据分开，外部issue仍由原系统负责。 |
| T071 | [Manage Exploratory Sessions](https://www.browserstack.com/docs/test-management/exploratory-sessions/manage-exploratory-sessions) | 探索会话列表区分活动/关闭、状态数量；独立于结构化测试运行。 | 探索日志与用例执行实例分别统计。 |
| T072 | [Run an Exploratory Session](https://www.browserstack.com/docs/test-management/exploratory-sessions/run-an-exploratory-session) | 探索日志按Pass/Fail/Bug等记录，timebox仅提醒不强制结束。 | 人工探索若纳入，日志追加并注明非脚本覆盖。 |
| T073 | [Redirecting…](https://www.browserstack.com/docs/test-management/generative-ai) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai) | 只计目标正文，不重复声称新功能。 |
| T074 | [Redirecting…](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-cases) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases) | 只计目标正文，不重复声称新功能。 |
| T075 | [Redirecting…](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-runs) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-runs) | 只计目标正文，不重复声称新功能。 |
| T076 | [Redirecting…](https://www.browserstack.com/docs/test-management/generative-ai/lcnc) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) | 只计目标正文，不重复声称新功能。 |
| T077 | [Redirecting…](https://www.browserstack.com/docs/test-management/generative-ai/low-code-automation) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) | 只计目标正文，不重复声称新功能。 |
| T078 | [Global search](https://www.browserstack.com/docs/test-management/global-search) | 跨项目全局搜索，按实体和可访问项目定位。 | 搜索按当前权限过滤；区分用例标题与来源内容。 |
| T079 | [Integrate issue trackers](https://www.browserstack.com/docs/test-management/integrate-issue-trackers) | 原生issue连接器和自定义URL链接并列；覆盖需求与缺陷。 | 接口能力分级：URL/读取/写入/回调，不能统一宣称实时双向。 |
| T080 | [Asana integration with Test Management](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/asana) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T081 | [Link Asana tasks to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/asana/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T082 | [Link Asana tasks to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/asana/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T083 | [ClickUp integration with Test Management](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/clickup) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T084 | [Link ClickUp issues to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/clickup/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T085 | [Link ClickUp issues to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/clickup/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T086 | [Integrate a custom issue tracker with Test Management](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/custom-issue-tracker) | 自定义tracker仅URL模板与ID，无API所以无标题/状态/优先级；IAM admin配置。 | URL-only标明数据不可同步，不计缺陷状态分析。 |
| T087 | [Link custom issues to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/custom-issue-tracker/link-with-test-cases) | 用例Requirements可链接自定义issue，但只显示ID。 | 外部引用含provider/host/project/id及用户可点原件。 |
| T088 | [Link custom issues to test runs and results](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/custom-issue-tracker/link-with-test-runs) | 运行关联需求，结果/自动运行关联缺陷；URL-only仍只存ID。 | 关系类型明确，不将缺陷当需求分母。 |
| T089 | [Integrate with DevRev](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/devrev) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T090 | [Link DevRev issues to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/devrev/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T091 | [Link DevRev issues to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/devrev/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T092 | [Integrate with GitHub](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/github-issues) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T093 | [Link GitHub issues to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/github-issues/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T094 | [Link GitHub issues to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/github-issues/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T095 | [Integrate with GitLab](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/gitlab-issues) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T096 | [Link GitLab issues to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/gitlab-issues/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T097 | [Link GitLab issues to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/gitlab-issues/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T098 | [Linear integration with Test Management](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/linear) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T099 | [Link Linear tasks to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/linear/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T100 | [Link Linear tasks to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/linear/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T101 | [Integrate with Trello](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/trello) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T102 | [Link Trello cards to test cases](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/trello/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T103 | [Link Trello cards to test runs](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/trello/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T104 | [Integrate with Zoho BugTracker](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/zoho) | 外部问题工具认证与连接；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T105 | [Link Zoho issues from a test case](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/zoho/link-with-test-cases) | 测试用例需求关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T106 | [Link Zoho issues from a test run](https://www.browserstack.com/docs/test-management/integrate-issue-trackers/zoho/link-with-test-runs) | 测试周期/结果缺陷关联；能力依此连接器，不能类推其他工具。 | 保来源系统/host/project/ID与权限，需求和缺陷分别建关系。 |
| T107 | [Integrate Azure DevOps with BrowserStack Test Management](https://www.browserstack.com/docs/test-management/integrations/azure) | Azure pipeline执行后上传JUnit；旧API-TOKEN导入入口，先建项目/流水线。 | CI保有执行权；结果适配层区分旧导入与新Basic API认证。 |
| T108 | [Jenkins Integration with BrowserStack Test Management](https://www.browserstack.com/docs/test-management/integrations/jenkins) | Jenkinsfile参数化/非参数化上传结果；新project_name可创建项目。 | Jenkins正式运行；凭据托管与显式项目映射，禁意外创建项目。 |
| T109 | [Two-way Jira binding](https://www.browserstack.com/docs/test-management/jira/2-way-jira-binding) | Jira双向关联用例/运行、导航更新和失败追溯；未定义完整字段同步契约。 | 显式对象主责、冲突、幂等、水位与删除语义。 |
| T110 | [BrowserStack Integration app for Jira](https://www.browserstack.com/docs/test-management/jira/jira-app) | 旧Integration app：Cloud装Marketplace并配置key，DC装jar；默认全项目启用可禁用。 | 与新Jira App安装/用户流程分开评估。 |
| T111 | [Jira integration with Test Management](https://www.browserstack.com/docs/test-management/jira/jira-integration) | 每用户独立连接Jira，私有DC还需Local Binary；支持用例/运行需求、结果缺陷。 | 连接凭证与访问边界按用户/host控制，网络联通是前提。 |
| T112 | [Configure Jira host and project mapping for a Test Management project](https://www.browserstack.com/docs/test-management/jira/jira-mapping) | 项目可限制Jira host/project白名单；变更仅限制新链接，历史链接保留。 | 租户/项目来源映射前置，历史引用与当前新写权限分开。 |
| T113 | [Manage test cases in Jira app](https://www.browserstack.com/docs/test-management/jira/manage-test-cases) | Jira内快速/完整/AI建例、编辑/解绑/删除；无项目时后台新建项目和文件夹。 | 不得隐式扩大项目归属，TAP要求选择已映射项目。 |
| T114 | [Manage test results in Jira app](https://www.browserstack.com/docs/test-management/jira/manage-test-results) | 用例结果关联Jira缺陷，issue侧展示来源运行及用例。 | 缺陷链接保provider/host/id，结果仍由原事实方持有。 |
| T115 | [Manage test runs in Jira app](https://www.browserstack.com/docs/test-management/jira/manage-test-runs) | Jira内建/AI选例/改/关/解绑/删运行。 | 嵌入入口调用统一业务API而非复制运行数据。 |
| T116 | [Manage your Jira integration with Test Management](https://www.browserstack.com/docs/test-management/jira/overview) | Jira连接、旧App、用例/运行和双向绑定入口概述。 | 区分远程连接器与Jira内嵌产品，能力按部署方式确认。 |
| T117 | [User Access Control in Test Management](https://www.browserstack.com/docs/test-management/overview/access-control) | RBAC/UDAC/地域控制与个性视图入口；非完整权限契约。 | UI偏好与真正鉴权分离，服务端统一权限。 |
| T118 | [Execute automated test runs](https://www.browserstack.com/docs/test-management/overview/automated-test-runs) | 自动化build、重跑、分析、告警和门禁概述；新分配用户须先登录。 | TAP自动化/Insights持技术能力，TAP AI呈现业务状态引用。 |
| T119 | [Integrate Test Management with CI/CD Tools](https://www.browserstack.com/docs/test-management/overview/ci-cd-integration) | CI/CD支持Jenkins和Azure教程，概述未证明远程调度API。 | 执行入口明确归TAP/Jenkins，管理侧只下达授权命令与收事件。 |
| T120 | [Debug overview](https://www.browserstack.com/docs/test-management/overview/debug) | 时间线、缺陷、标签、失败分析、静音、日志源码入口。 | Insights负责失败诊断；人工结果附证据不替代技术日志。 |
| T121 | [Test Management demo](https://www.browserstack.com/docs/test-management/overview/demo) | 仅嵌入演示视频，正文无额外行为契约；视频未逐帧分析。 | 不凭视频入口增加能力承诺。 |
| T122 | [Exploratory testing](https://www.browserstack.com/docs/test-management/overview/exploratory-testing) | 探索会话为无预先用例的独立任务；mission/timebox/log，计时不自动终止。 | 后续独立探索会话模型；不伪造成已设计用例或正式覆盖率。 |
| T123 | [Get started with Test Management](https://www.browserstack.com/docs/test-management/overview/getting-started) | 注册引导选择角色与既有工具，可用预置演示项目或导入。 | 入门模板与真数据分隔，允许先空项目再逐步配置。 |
| T124 | [Keyboard shortcuts](https://www.browserstack.com/docs/test-management/overview/keyboard-shortcuts) | 上下文快捷键、结果后自动前进、编辑框内屏蔽单键；前两会话提示。 | 执行工作台支持键盘连续录入，防焦点错位误提交。 |
| T125 | [Manage form fields](https://www.browserstack.com/docs/test-management/overview/manage-form-fields) | 系统、用例自定义及结果自定义字段分别配置。 | 用例属性与执行结果属性分schema、独立验证。 |
| T126 | [Manage Test Data / BrowserStack Docs](https://www.browserstack.com/docs/test-management/overview/manage-test-data) | 项目容纳用例/运行/计划；用例是运行基础，计划组织策略。 | 迁出计划内嵌用例，独立资产再由计划和周期引用固定版本。 |
| T127 | [Redirecting…](https://www.browserstack.com/docs/test-management/overview/manage-test-runs) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/overview/manual-test-runs) | 只计目标正文，不重复声称新功能。 |
| T128 | [Manage manual test runs](https://www.browserstack.com/docs/test-management/overview/manual-test-runs) | 人工运行组织预定义用例、执行人/日期、结果附件、动态选择及配置。 | TAP AI执行周期与手工结果拥有者；执行人身份预校验。 |
| T129 | [Reports and dashboards overview](https://www.browserstack.com/docs/test-management/overview/reports-and-dashboards) | 报告/仪表盘总览，管理报告与稳定性、flake等技术分析并列。 | ClickHouse统一事实并按用户任务分入口，不在TAP AI重复分析存储。 |
| T130 | [Test Management-specific dashboard widgets](https://www.browserstack.com/docs/test-management/overview/test-management-specific-widgets) | 7类管理widgets：活动/关闭运行、用例分布、缺陷、自动化覆盖、执行分布/次数。 | 指标显式定义分子分母，实例与唯一用例分开计。 |
| T131 | [What is Test Management?](https://www.browserstack.com/docs/test-management/overview/what-is-test-management) | 独立产品统一手工与自动用例、导入、报告、Jira和根因分析。 | 统一导航与身份，事实职责仍分测试管理/自动化/Insights。 |
| T132 | [Import projects and test cases](https://www.browserstack.com/docs/test-management/quick-import) | 按来源工具和hosting选择迁移；CSV另一路径。 | 迁移向导首先选择来源版本/部署型并显示能力缺口。 |
| T133 | [Custom test case ID](https://www.browserstack.com/docs/test-management/quick-import/custom-test-case-id) | 自定义ID仅全新无数据账户可开、项目内唯一且不可退回group ID。 | 内部稳定ID与展示ID分离；来源ID别名保留，迁移不改主键。 |
| T134 | [Import test data from CSV or Feature files](https://www.browserstack.com/docs/test-management/quick-import/import-csv) | CSV最多5000用例/10MB；feature最多20文件且不可混类型；外链图仅publicURL；保未注册email待映射。 | 导入预览/字段映射/失败报告/幂等重试，BDD必填Feature+Scenario，外部图片明确缺失。 |
| T135 | [Quick Import data using qTest](https://www.browserstack.com/docs/test-management/quick-import/qtest) | qTest release→plan/cycle→run/run→case execution；默认近2年release；新ID+Imported Test Case ID，保步骤结果/历史/链接。 | 迁移按来源语义而非同名字段映射；范围/年份/ID变化展示并核对。 |
| T136 | [Quick Import data using TestRail](https://www.browserstack.com/docs/test-management/quick-import/testrail) | TestRail主教程称仅Cloud（与T218 On-Prem说明冲突）；case不限时，runs/milestones默认近1年；ID保留可冲突。 | 默认显式选项目；保源ID映射，时间范围与未迁历史显示缺口。 |
| T137 | [Quick Import from Xray](https://www.browserstack.com/docs/test-management/quick-import/xray) | Xray Cloud/DC均仅用例；Cloud双token，DC PAT，后台迁移无需文件映射。 | 按部署型验能力，不因日志通用计数扩大到运行/计划。 |
| T138 | [Quick Import from Xray Cloud](https://www.browserstack.com/docs/test-management/quick-import/xray-cloud) | Xray Cloud需同一账号Jira API token+Xray clientID/secret，Xray管理员及Jira管理/浏览用户权。 | 同账号凭据匹配预检，迁移权限不转授普通用户。 |
| T139 | [Quick Import from Xray Server/DC](https://www.browserstack.com/docs/test-management/quick-import/xray-server) | Xray Server/DC仅用例；Jira DC9.x、Xray6.x–8.x、PAT；可选保ID否则源ID另存。 | connector约束版本矩阵，源ID与资产ID独立。 |
| T140 | [Quick Import from Zephyr](https://www.browserstack.com/docs/test-management/quick-import/zephyr) | Zephyr Scale/Essential Cloud只用例；Essential Server/DC含用例/运行/计划，认证不同。 | 三来源能力独立，不能用同一Zephyr支持标志。 |
| T141 | [Quick Import from Zephyr Essential (Squad) Server](https://www.browserstack.com/docs/test-management/quick-import/zephyr-essential-server) | Essential Server要求Jira≥10.3.16、插件≥10.2.2；迁自定义字段/附件，周期可选近1年。 | 部署版本/对象/日期/附件覆盖逐项预检，与Cloud缺口分列。 |
| T142 | [Quick Import data using Zephyr Scale](https://www.browserstack.com/docs/test-management/quick-import/zephyr-scale) | Zephyr Scale用Jira+Scale双token，管理员；API不支持附件迁移。 | 迁移报告单独列未迁附件，不将case成功等同完整证据。 |
| T143 | [Quick Import from Zephyr Essential (Squad) Cloud](https://www.browserstack.com/docs/test-management/quick-import/zephyr-squad) | Essential Cloud双token需同账号/管理员权限，仅用例且附件不迁；可后台去重扫描。 | Cloud与Server能力分档，导入后去重独立人工确认。 |
| T144 | [References](https://www.browserstack.com/docs/test-management/references/overview) | 条款等引用入口，未给新业务行为。 | 接口与条款链接分开维护，不据索引推断API。 |
| T145 | [Additional Terms for Test Management](https://www.browserstack.com/docs/test-management/references/terms-and-conditions) | 导入包含个人数据；保留/删除及第三方限制条款，未给精确统一保留天数。 | 导入记录授权与来源，删除/保留规则由TAP明确设计，不照抄供应商条款。 |
| T146 | [Reporting dashboard overview](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard) | 项目dashboard全局时间与owner筛选；owner对不同widget指case assignee/run assignee/case owner。 | 每指标显示owner语义，跨报表筛选不混淆责任人角色。 |
| T147 | [Project Dashboard - Defects Logged](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-defects) | 汇总Jira/ADO/Asana缺陷并声明实时更新；CSV遵循当前时间范围。 | 明确数据新鲜度/同步失败，导出应用同一权限与筛选。 |
| T148 | [Redirecting…](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-jira) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-defects) | 只计目标正文，不重复声称新功能。 |
| T149 | [Redirecting…](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-test-cases) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/dashboard/project-insights) | 只计目标正文，不重复声称新功能。 |
| T150 | [Redirecting…](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-test-runs) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/test-runs/test-run-list-view) | 只计目标正文，不重复声称新功能。 |
| T151 | [Reports](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports) | 项目级6类报告：用例活动、运行概要/详细、需求、计划、执行；可定时分享下载。 | 报告任务保存查询条件/生成时间/权限与数据水位。 |
| T152 | [Understand and use advanced filters in your reports](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/advanced-filters-in-reports) | 报告创建时应用状态/类型/优先级/执行人/自动化/创建更新日期过滤。 | 区分快照报告与实时视图，记录过滤schema与时区。 |
| T153 | [Requirement traceability report](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/requirement-traceability-report) | 需求→用例/运行/缺陷，latest case+configuration result；2万行，JQL/epic子项200；表格过滤不改顶部汇总。 | 设计/执行/通过覆盖分开，口径和截断显式，临时筛选与报告快照区分。 |
| T154 | [Share and download reports](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/share-and-download-reports) | 公开link任何人可看；普通link组域权限；详细/需求CSV或PDF，概要仅PDF，CSV最多2万行。 | 默认授权链接；公开分享独立开关/到期/撤销，导出继承筛选与权限。 |
| T155 | [Test case activity report](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/test-case-activity-report) | 用例创建/修改/归档/删除趋势、自动化和来源分布；动态纳入未来用例，UTC日报/周报。 | 资产来源与执行方式分开维度，报告动态查询保存规则版本。 |
| T156 | [Use test execution reports in Test Management](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/test-execution-report) | 跨build按test hash去重显示latest；手工hash含case/data row/OS/browser/device；flake等3指标仅自动化；public仅组织。 | 实例身份固定版本/数据/配置；latest视图不覆盖attempt事实；组织分享与匿名链接分开。 |
| T157 | [Test plan summary report](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/test-plan-summary-report) | 计划概要可按创建期或指定计划，支持日报/周报及收件人。 | 计划视角聚合周期事实，保报告来源范围与生成水位。 |
| T158 | [Test run detailed report](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/test-run-detailed-report) | 详细运行报告含用例与问题，动态纳入runs不适用Created过滤；查看过滤不改汇总/保存。 | 区分运行选择、用例过滤、临时表格筛选，统一展示有效范围。 |
| T159 | [Test run summary report](https://www.browserstack.com/docs/test-management/reports-and-analytics/reports/test-run-summary-report) | 概要按active/closed、执行结果、问题优先级统计；UTC调度，last-executed-by含未执行。 | 手工周期状态与用例结果分开计；unassigned不等于未授权。 |
| T160 | [Integrate Test Reporting & Analytics with Test Management](https://www.browserstack.com/docs/test-management/reports-and-analytics/test-observability) | SDK收集本地/CI/云运行，管理run图表图标进入TRA；支持多个框架及JUnit/Allure上传。 | 管理操作与Insights分析深链衔接，技术数据单写TAP分析链路。 |
| T161 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-cases/ai-powered-test-cases) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-cases) | 只计目标正文，不重复声称新功能。 |
| T162 | [Archive or Restore test cases](https://www.browserstack.com/docs/test-management/test-cases/archive-retrieve-test-cases) | 归档可恢复90天，之后永久删除。 | TAP停用资产保审计，不默认按供应商90天删除。 |
| T163 | [Column preferences](https://www.browserstack.com/docs/test-management/test-cases/column-preferences) | 用例列表可隐藏/显示/重置列。 | 保存个人视图，不改变权限或实际选例范围。 |
| T164 | [Comment in test cases](https://www.browserstack.com/docs/test-management/test-cases/comment-in-test-cases) | 评论富文本/@mention/通知与自有评论编辑删除；付费功能。 | 评审意见绑定版本，编辑留记录，通知按偏好。 |
| T165 | [Manage and organize test cases with copy or move](https://www.browserstack.com/docs/test-management/test-cases/copy-and-move-test-cases) | 跨项目copy新ID、move保标识与关联；需共同字段映射，否则丢字段；目录排序影响运行。 | TAP冻结运行顺序与版本，迁移前预览字段丢失。 |
| T166 | [Copy and move folders across projects](https://www.browserstack.com/docs/test-management/test-cases/copy-move-folders-across-projects) | 文件夹跨项目复制/移动及共享步骤复制，move原件说明存在矛盾。 | 明确copy新身份，跨项目move独立治理不静默启用。 |
| T167 | [Projects: The foundation of Test Management](https://www.browserstack.com/docs/test-management/test-cases/create-projects) | 项目容纳用例/计划/运行，层级文件夹有直接/累积数量。 | 既有项目复用，权限与产品间project映射显式。 |
| T168 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-cases/create-projects-and-test-cases) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/test-cases/create-test-cases) | 只计目标正文，不重复声称新功能。 |
| T169 | [Test Cases: The building blocks of testing](https://www.browserstack.com/docs/test-management/test-cases/create-test-cases) | Text/Steps/BDD三模板；需求关系与结果缺陷分开；元数据和步骤预期。 | 独立case/version并兼容原BDD。 |
| T170 | [Edit or Delete test cases](https://www.browserstack.com/docs/test-management/test-cases/edit-and-delete-test-cases) | 侧栏行内自动保存、全编辑附件、批量变更与删除；步骤重排。 | 草稿自动保存需并发版本检查，发布版本不可就地改。 |
| T171 | [Export test cases](https://www.browserstack.com/docs/test-management/test-cases/export-test-cases) | CSV全类型，BDD可.feature，支持跨页选择和过滤导出。 | 导出用例版本/范围可预览，报告与资产导出分开。 |
| T172 | [Filter Test Cases](https://www.browserstack.com/docs/test-management/test-cases/filter-test-cases) | 过滤支持字段间AND/OR及多值包含/排除；目录始终AND。 | 统一可保存过滤表达式并显示范围。 |
| T173 | [Manage test cases](https://www.browserstack.com/docs/test-management/test-cases/manage-test-cases) | 用例管理概览汇总CRUD/组织/导出/复用。 | 完善测试管理资产工作区。 |
| T174 | [Part 11 Compliance](https://www.browserstack.com/docs/test-management/test-cases/part-11-compliance) | 按项目启电子签名门禁；仅密码账户不支持SSO/2FA，审计需客服提取默认30天。 | 不作为TAP OIDC合规实现蓝图，合规需求另审。 |
| T175 | [Review and approve test cases](https://www.browserstack.com/docs/test-management/test-cases/review-and-approve-test-cases) | 评审者白名单、作者不可自审、改内容回Pending；拒绝/改动须评论；copy重审。 | 审批绑定不可变内容版本，全入口一致门禁。 |
| T176 | [Share your test case publicly](https://www.browserstack.com/docs/test-management/test-cases/share-test-case) | 可生成任何人能打开的公共用例链接。 | 默认私有，分享需要权限/到期/撤销。 |
| T177 | [Shared fields in test cases](https://www.browserstack.com/docs/test-management/test-cases/shared-fields) | 共享步骤/前置条件/BDD背景为引用，编辑传播、删除关联内容消失。 | 共享内容版本化，影响预览后更新草稿，运行固定引用。 |
| T178 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-cases/shared-steps) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/test-cases/shared-fields) | 只计目标正文，不重复声称新功能。 |
| T179 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-cases/test-case-history) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/test-cases/test-case-versioning) | 只计目标正文，不重复声称新功能。 |
| T180 | [Prefix custom test case ID](https://www.browserstack.com/docs/test-management/test-cases/test-case-id-prefix) | 项目ID前缀仅影响新用例，序号不中断；旧ID不改。 | 内部稳定ID与显示编号分开，不因标题/前缀改映射。 |
| T181 | [Test case versioning](https://www.browserstack.com/docs/test-management/test-cases/test-case-versioning) | 逐字段/附件/模板/位置历史，可比较/恢复；copy不继承history。 | 恢复生成新修订，历史运行仍绑定原caseversion。 |
| T182 | [Test datasets](https://www.browserstack.com/docs/test-management/test-cases/test-datasets) | 复用数据集，选行链接，多集笛卡尔积，再乘用例和配置。 | 独立dataset version/row ID，运行前预览规模及组合策略。 |
| T183 | [View test cases across folders and subfolders](https://www.browserstack.com/docs/test-management/test-cases/view-test-cases) | 目录/递归平铺、保存视图、批量处理。 | 过滤范围和跨页选择数量始终可见。 |
| T184 | [Add Test Runs to Test Plan](https://www.browserstack.com/docs/test-management/test-plans/add-test-runs-to-test-plan) | 在计划中创建新run，或编辑现有run选择计划关联。 | 计划与周期独立身份，通过归属关系组织，不复制历史结果。 |
| T185 | [Clone test plans](https://www.browserstack.com/docs/test-management/test-plans/clone-test-plan) | 计划克隆含手工runs/配置/元数据，可选runs；自动化runs不克隆。 | 复制生成新计划/周期身份，运行证据保持原归属。 |
| T186 | [Create test plans](https://www.browserstack.com/docs/test-management/test-plans/create-test-plans) | 计划是项目内runs容器，日期/描述/附件10×50MB，先空计划再加run；删除永久。 | 设计计划与执行聚合兼容，附件生命周期及删除引用明确。 |
| T187 | [Delete a test plan](https://www.browserstack.com/docs/test-management/test-plans/delete-test-plan) | UI列表或详情永久删除计划，需确认；未声明级联run的完整语义。 | 定义删除与归档区别，先查结果/周期引用，保审计。 |
| T188 | [Edit a test plan](https://www.browserstack.com/docs/test-management/test-plans/edit-test-plan) | UI立即更新计划title/dates/description/runs等信息。 | 草稿允许编辑，已发布版本固定并产生新修订。 |
| T189 | [Link automated test runs to a Test Plan](https://www.browserstack.com/docs/test-management/test-plans/link-automated-runs) | SDK/TRA testPlanId与旧JUnit test_plan_id；TP/STP均可；异步关联失败不使build失败。 | 业务关联状态与技术执行结果分开，ID/权限校验和补偿对账。 |
| T190 | [Create sub-test plans](https://www.browserstack.com/docs/test-management/test-plans/sub-test-plan) | 子计划可分feature/squad/sprint，结果汇总父计划；active子计划阻止父完成。 | 先约束层级与汇总防重复，完成门禁校验全部子周期。 |
| T191 | [Manage sub-test plans](https://www.browserstack.com/docs/test-management/test-plans/sub-test-plan/manage-sub-test-plan) | 子计划单独编辑/删除，删除永久且仅解绑runs；结果汇总父计划，编辑不自动完成父。 | 解绑不删除执行证据，层级变更重算投影并保历史关联。 |
| T192 | [Test Plans: The strategy behind testing](https://www.browserstack.com/docs/test-management/test-plans/what-is-test-plan) | 计划active/completed、start/complete、进度及最近15天结果；active子计划阻止完成。 | 策略版本与执行生命周期区分；历史视图标明15天窗口非全部历史。 |
| T193 | [Add a result](https://www.browserstack.com/docs/test-management/test-runs/add-a-result) | 用例/步骤结果，原作者可改手工结果，状态规则聚合。 | 步骤稳定ID与结果审计，不覆盖原记录。 |
| T194 | [Add defects and custom fields in step results](https://www.browserstack.com/docs/test-management/test-runs/add-defects-and-custom-fields-in-step-results) | 步骤结果可挂缺陷和自定义结果字段。 | 失败定位到具体步骤，缺陷关联与结果事实分开。 |
| T195 | [Add execution details](https://www.browserstack.com/docs/test-management/test-runs/add-execution-details) | 运行内实例可单/批分派执行者与计划执行日期。 | 用例owner与本周期tester分开，计划/实际时间分别统计。 |
| T196 | [Add notes and attachments](https://www.browserstack.com/docs/test-management/test-runs/add-notes-and-attachments) | 用例结果及单步骤可备注/截图/日志附件。 | 对象证据附归属与授权，Insights保引用。 |
| T197 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-runs/ai-powered-test-runs) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-runs) | 只计目标正文，不重复声称新功能。 |
| T198 | [Auto-close test runs](https://www.browserstack.com/docs/test-management/test-runs/auto-close-test-runs) | 创建时固定自动关闭日期；旧run7天宽限，自动关闭不触发webhook/质量重算。 | 关闭原因显式，定时对账补漏，未执行不标通过。 |
| T199 | [Automated test runs](https://www.browserstack.com/docs/test-management/test-runs/automated-test-runs) | 自动化报告/SDK接入生成运行，可来自本地/任意云；TRA向TM复制结果结构。 | TAP接外部报告不要求自身生成；不照搬跨产品复制，保持事实接口。 |
| T200 | [Clone test runs](https://www.browserstack.com/docs/test-management/test-runs/clone-test-runs) | 按全部/最新结果克隆，选择复制人员/标签/问题，克隆总active。 | 新周期结果重置，保来源周期关系。 |
| T201 | [Configurations](https://www.browserstack.com/docs/test-management/test-runs/configurations-in-a-test-run) | 配置组可组合，删除组影响关联run；自定义配置不能启动Live。 | 配置冻结，停用不改历史，执行适配器验证设备能力。 |
| T202 | [Create a manual test run](https://www.browserstack.com/docs/test-management/test-runs/create-manual-test-runs) | 手工周期容纳所选用例、配置、分派、标签和状态。 | TAP AI持业务执行实例，开始时冻结版本与数据。 |
| T203 | [Dynamic test case selection](https://www.browserstack.com/docs/test-management/test-runs/dynamic-test-case-selection) | 动态过滤持续添加匹配例，既有不匹配例仍保留。 | 开始前物化清单，执行中新增显式确认。 |
| T204 | [Link CI/CD results with manual testing](https://www.browserstack.com/docs/test-management/test-runs/link-ci-cd-results-with-manual-testing) | CLI报告创建独立自动运行，tag才稳定映射；本页说每build一XML且不支持外置logs。 | 接收层用稳定ID对账，未映射进待确认，不能每次静默造新例。 |
| T205 | [Manage test run groups](https://www.browserstack.com/docs/test-management/test-runs/manage-test-run-groups) | group批量分派/改关联计划/关闭；手工close保报告，自动化archive排除指标。 | 关闭/归档/删除三种语义分离，历史指标修正可追踪。 |
| T206 | [Manage test run state](https://www.browserstack.com/docs/test-management/test-runs/manage-test-run-state) | Active可改结果，Closed只读；支持批量关停。 | 周期结项不可再就地写，修正追加事件。 |
| T207 | [Redirecting…](https://www.browserstack.com/docs/test-management/test-runs/manual-test-runs) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/overview/manual-test-runs) | 只计目标正文，不重复声称新功能。 |
| T208 | [Search and filter](https://www.browserstack.com/docs/test-management/test-runs/search-and-filter) | 人员过滤区分Assignee与Last Executed By，无结果可筛Unassigned。 | 任务分派者/真实执行者分别记录。 |
| T209 | [Share your test run publicly](https://www.browserstack.com/docs/test-management/test-runs/share-test-runs) | 运行可启用匿名公共链接。 | 默认授权访问，公共分享单独控制和撤销。 |
| T210 | [Test Case ID Tagging](https://www.browserstack.com/docs/test-management/test-runs/test-case-tagging) | BDD标签/XML property/标题嵌ID；SDK/TRA可一自动测试映多用例，CLI只一个。 | 连接器显式声明映射基数，不按标题或文件夹猜身份。 |
| T211 | [Test run groups](https://www.browserstack.com/docs/test-management/test-runs/test-run-groups) | 运行分组支持创建、关联和折叠展示。 | 业务周期分组轻量实现，勿另造调度层。 |
| T212 | [Test runs list view](https://www.browserstack.com/docs/test-management/test-runs/test-run-list-view) | 手工与自动运行同列表，状态/所有者/筛选/固定或保存视图。 | 显示业务周期与技术运行来源，跳TAP看技术证据。 |
| T213 | [View test cases in a test run detail page](https://www.browserstack.com/docs/test-management/test-runs/view-test-cases-in-a-test-run) | 运行内目录/平铺，搜索切平铺，可选择列。 | 同一固定实例清单的不同展示，不改变统计范围。 |
| T214 | [Redirecting…](https://www.browserstack.com/docs/test-management/troubleshooting/audit-logs-and-history) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/test-cases/test-case-versioning) | 只计目标正文，不重复声称新功能。 |
| T215 | [Troubleshoot errors during CSV Import](https://www.browserstack.com/docs/test-management/troubleshooting/import-csv) | CSV映射Test Case ID重复则失败，官方workaround忽略该列。 | 冲突预检并保来源别名，不能静默丢来源ID或覆盖旧例。 |
| T216 | [Troubleshoot](https://www.browserstack.com/docs/test-management/troubleshooting/overview) | 审计历史/CSV/quick-import故障入口，非额外审计功能承诺。 | 失败信息按凭据、网络、映射、对象冲突分类。 |
| T217 | [Troubleshoot common quick import failures](https://www.browserstack.com/docs/test-management/troubleshooting/quick-import) | 迁移中修改来源目录可失败；私网需供应商IP白名单向support取得。 | 导入预检网络与源版本锁/水位，报告中途来源变化。 |
| T218 | [Troubleshooting TestRail quick import](https://www.browserstack.com/docs/test-management/troubleshooting/quick-import/testrail-issues) | 主教程Cloud-only与本页On-Prem≥7.5/cookie附件密码冲突；近1年可申请扩展受保留期；源作者缺失回退导入者。 | 支持矩阵标待验证；保原作者与实际导入者，失败项目选择性重试。 |
| T219 | [Troubleshoot connection failures for Zephyr Scale](https://www.browserstack.com/docs/test-management/troubleshooting/quick-import/zephyr-scale-issues) | 分别诊断Jira host/email/token、Scale token及网络；重新生成token需配置更新。 | 错误显示具体连接阶段且脱敏，不用同一通用连接失败。 |
| T220 | [Troubleshoot connection failures for Zephyr Essential (Squad)](https://www.browserstack.com/docs/test-management/troubleshooting/quick-import/zephyr-squad-issues) | 分别校验Jira host/账号/API token与Squad token，凭据各自生成。 | 复合凭据逐项预检、权限最小化、轮换后更新连接。 |
| T221 | [JUnit XML or BDD-JSON based report upload](https://www.browserstack.com/docs/test-management/upload-reports-cli) | JUnit/BDD导入按ID或标题匹配否则新建；默认Pass、failure/error/system-err→Fail；配置生实例，系统未知枚举自动增，自定义错类型留空。 | 稳定ID优先，未知用例入待关联队列，禁止技术结果静默改批准资产；导入字段失败逐项可见。 |
| T222 | [JUnit-XML based Appium report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/appium) | Appium Java/TestNG示例产JUnit后用旧import上传；API token示例-u，与CI header不同。 | Appium执行与报告归一化分层，按实际runner选择适配器并验证认证。 |
| T223 | [BDD-JSON based Cucumber report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/cucumber) | Cucumber JSON输出feature/scenario/steps/result再上传json/bdd；返回run URL。 | BDD层级/step身份保留，runner结果不覆盖手工用例版本。 |
| T224 | [JUnit-XML based Cypress report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/cypress) | Cypress junit reporter配置name/classname转换，生成XML再上传。 | 归一化前固化标题映射规则，避免改reporter导致历史断裂。 |
| T225 | [JUnit-XML based Espresso report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/espresso) | Espresso connectedAndroidTest产JUnit，设备属性与失败堆栈入报告。 | 设备配置从事实解析并绑定实例，技术失败证据归Insights。 |
| T226 | [JUnit-XML based Mocha report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/mocha) | Mocha junit reporter产XML，name/classname及输出需配置；旧import返回run URL。 | 报告适配器保原始名称/框架，统一稳定关联与错误反馈。 |
| T227 | [JUnit-XML based Nightwatch JS report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/nightwatch) | Nightwatch output_folder配置JUnit，上传后回run URL；示例支持失败文本。 | 结果上传与执行分开状态，保错误原文但脱敏索引。 |
| T228 | [JUnit-XML based Playwright report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/playwright) | Playwright显式--reporter=junit/输出文件，示例name与含浏览器路径classname不同。 | 绑定稳定ID与配置，不将文件行号当永久用例身份。 |
| T229 | [JUnit-XML based PyTest report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/pytest) | pytest --junitxml，record_property(id,TC-...)映射用例，旧import返回run URL。 | 为框架生成器注入稳定caseversion/instance标签并校验报告保留。 |
| T230 | [JUnit-XML based TestNG report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/testng) | TestNG Reporter.log PROPERTY id进入system-out标签，JUnit上传关联。 | 框架适配器识别属性与输出两种元数据，关联验证不只按标题。 |
| T231 | [JUnit-XML based WebdriverIO report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/webdriverio) | WebdriverIO需junit reporter；capabilities/file/suiteName等属性及命令日志写XML。 | 结构化配置和原始日志分开存，保版本/敏感字段清洗。 |
| T232 | [JUnit-XML based XCUITest report](https://www.browserstack.com/docs/test-management/upload-reports-cli/frameworks/xcuitest) | XCUITest经xcpretty把xcodebuild输出转JUnit再上传；示例命令含明显排版错误。 | iOS报告转换作为runner适配，不复制文档命令为生产契约。 |
| T233 | [Role-Based Access Control](https://www.browserstack.com/docs/test-management/user-access-control) | 此页明确是旧产品RBAC，现行可用跨产品集中模型；UI/API权限一致。 | TAP定义自己的项目角色/动作矩阵，勿照搬旧角色名。 |
| T234 | [Geo Region Restriction (GRR) for Test Management](https://www.browserstack.com/docs/test-management/user-access-control/geo-region-restriction) | Enterprise地区存储能力有覆盖/排除数据集合，不代表全数据驻留。 | 仅在明确部署需求下逐类存储/模型供应商核对。 |
| T235 | [Project Visibility](https://www.browserstack.com/docs/test-management/user-access-control/project-visibility) | 来源规则隐藏整个项目，影响默认列表/数字；直链仍能访问，并非授权。 | 导航隐藏与实际项目授权分开，前者不作安全边界。 |
| T236 | [Manage data access for Test Management](https://www.browserstack.com/docs/test-management/user-access-control/udac) | 可切团队数据隔离，IAM组织/团队角色及迁移影响访问。 | 项目成员权限与组织角色组合检查并即时失效缓存。 |
| T237 | [Webhooks](https://www.browserstack.com/docs/test-management/webhooks) | 9类case/plan/result事件；account统一配置、共享投递机制，变量按产品；此页未列唯一eventID/重试完整契约。 | 事件outbox+幂等消费+水位对账；订阅按项目权限，不假设exactly-once。 |
| T238 | [Test case variables](https://www.browserstack.com/docs/test-management/webhooks/test-case-variables) | 用例create/update/delete/archive事件含字段、需求、步骤、附件。 | 事件需版本/去重编号；附件签名URL不能作永久事实。 |
| T239 | [Test plan variables](https://www.browserstack.com/docs/test-management/webhooks/test-plan-variables) | 计划create/update/completed/delete事件含日期及结果汇总。 | 汇总事件作提示，权威指标由事实重算。 |
| T240 | [Test result variables](https://www.browserstack.com/docs/test-management/webhooks/test-result-variables) | 结果added事件列测试平台/失败类别/时长等变量，但本页未给完整事件唯一性/重试契约。 | 落接收记录去重，核对来源结果并定时补漏。 |
| T241 | [audit-logs-and-history](https://www.browserstack.com/docs/test-management/audit-logs-and-history) | HTTP Error 404: Not Found | 正文未核实，不据URL推断能力。 |
| T242 | [Redirecting…](https://www.browserstack.com/docs/test-management/integrations/ci-cd) | 官方跳转至 [官方页面](https://www.browserstack.com/docs/test-management/integrations/jenkins) | 只计目标正文，不重复声称新功能。 |
| T243 | [folders](https://www.browserstack.com/docs/test-management/test-cases/folders) | HTTP Error 404: Not Found | 正文未核实，不据URL推断能力。 |
| T244 | [overview](https://www.browserstack.com/docs/test-management/test-plans/overview) | HTTP Error 404: Not Found | 正文未核实，不据URL推断能力。 |

## 失败与跳转单独核对

所有初次429均已低速复核；最终4条404，0条429，0条待研读正文。两页demo只有嵌入视频及简介，已读其有限正文，未转录视频；不据视频扩充功能。对295条去重原URL的范围内链接完成闭环检查，没有剩余未入清单的站内产品链接。

| ID | 失败原URL | 状态 |
| --- | --- | --- |
| T052 | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/review-and-resolve-duplicates) | HTTP 404；不计正文已读 |
| T241 | [官方页面](https://www.browserstack.com/docs/test-management/audit-logs-and-history) | HTTP 404；不计正文已读 |
| T243 | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/folders) | HTTP 404；不计正文已读 |
| T244 | [官方页面](https://www.browserstack.com/docs/test-management/test-plans/overview) | HTTP 404；不计正文已读 |

| ID | 跳转原URL | 官方目标 |
| --- | --- | --- |
| J009 | [官方页面](https://www.browserstack.com/docs/test-management-jira-app/overview/reports-and-dashboards) | [官方页面](https://www.browserstack.com/docs/test-management-jira-app/dashboards) |
| T036 | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-jira-confluence-links) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs) |
| T037 | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-requirement-document-image-prompt) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases/from-different-inputs) |
| T051 | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-automation) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) |
| T067 | [官方页面](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/type-of-test-cases) | [官方页面](https://www.browserstack.com/docs/test-management/dashboard/test-management-widgets/test-cases-breakdown) |
| T073 | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai) |
| T074 | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-cases) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-cases) |
| T075 | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-runs) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/ai-generated-test-runs) |
| T076 | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/lcnc) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) |
| T077 | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/low-code-automation) | [官方页面](https://www.browserstack.com/docs/test-management/browserstack-ai/low-code-authoring) |
| T127 | [官方页面](https://www.browserstack.com/docs/test-management/overview/manage-test-runs) | [官方页面](https://www.browserstack.com/docs/test-management/overview/manual-test-runs) |
| T148 | [官方页面](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-jira) | [官方页面](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-defects) |
| T149 | [官方页面](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-test-cases) | [官方页面](https://www.browserstack.com/docs/test-management/dashboard/project-insights) |
| T150 | [官方页面](https://www.browserstack.com/docs/test-management/reports-and-analytics/dashboard-test-runs) | [官方页面](https://www.browserstack.com/docs/test-management/test-runs/test-run-list-view) |
| T161 | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/ai-powered-test-cases) | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-cases) |
| T168 | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/create-projects-and-test-cases) | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/create-test-cases) |
| T178 | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/shared-steps) | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/shared-fields) |
| T179 | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/test-case-history) | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/test-case-versioning) |
| T197 | [官方页面](https://www.browserstack.com/docs/test-management/test-runs/ai-powered-test-runs) | [官方页面](https://www.browserstack.com/docs/test-management/generative-ai/ai-powered-test-runs) |
| T207 | [官方页面](https://www.browserstack.com/docs/test-management/test-runs/manual-test-runs) | [官方页面](https://www.browserstack.com/docs/test-management/overview/manual-test-runs) |
| T214 | [官方页面](https://www.browserstack.com/docs/test-management/troubleshooting/audit-logs-and-history) | [官方页面](https://www.browserstack.com/docs/test-management/test-cases/test-case-versioning) |
| T242 | [官方页面](https://www.browserstack.com/docs/test-management/integrations/ci-cd) | [官方页面](https://www.browserstack.com/docs/test-management/integrations/jenkins) |


编号、URL 唯一性、正文缓存指纹与产品范围内链接闭包已核验。栏目索引和有限的演示说明计入正文，不能据此声称已观看视频或验证账户功能。
