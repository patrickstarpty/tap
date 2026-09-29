# BrowserStack Load Testing 逐页分析与 TAP 设计映射

研究日期：2026-09-17。本文为[官方公开文档](https://www.browserstack.com/docs/load-testing)研究，不是产品账号实测或已完成功能清单。目标交互、技术架构与交付边界见 [RFC-011 负载测试专题](../reference/2026-09-22-rfc-011-testing-execution-design.md#load-testing)。

## 阅读范围与证据

已解析产品 HTML 内 `window.sidebarData` 的126个产品导航URL；官方 `https://www.browserstack.com/sitemap.xml`（gzip压缩）给出139个 `/docs/load-testing` URL。正文链接递归再发现3个原目录未收录路径，共142个去重产品路径，稳定编号L001–L142。**128篇HTTP200非空正文已逐篇阅读全文并记录能力边界与TAP映射；11个HTTP200旧入口只有meta redirect；3个正文坏链HTTP404。** 不能将142个路径写成142篇已读文章。公开产品目录与已取正文内链接的发现闭包已完成；不代表登录后或未被官方导航/站点地图/正文链接公开的页面。

逐页清单记录原始 URL、HTTP 状态、正文能力/限制及 TAP 对应设计。最初遇到 HTTP 429，暂停后改为单路低速补抓，原清单 139 个路径最终均取得 HTTP 200；其中 11 个是跳转入口，不能计作功能正文。新增的 3 个坏链仍为 404。

11 个旧入口的跳转目标包含重复 `/docs/docs/`；去掉片段后共 6 个实际目标，均返回 404，其正确目录下正文已作为其他编号独立阅读。正文核读涵盖段落、列表、表格、代码及图片说明；图片的视觉内容和需登录功能未作实测。正文缓存指纹、编号唯一性及正文内产品链接闭包均已校验。

[逐页矩阵](#逐页能力与-tap-映射)保留全部路径；栏目索引属于正文，不能当作独立功能。本文的供应商限制仅代表本次公开文档，TAP 自有设计以主 RFC 为准。

## 先给设计结论

**TAP 新增独立 Load Testing 领域，三种模式为 API、Browser、Hybrid：协议压测唯一选 k6；Playwright 默认作为少量、隔离的浏览器体验探针，也支持经校准、专属池承载的真实浏览器并发。** 不把 Web/App 功能回归的并行数当作服务端负载，不新增 Appium 大规模并发，不承诺 BrowserStack 引擎能导出、自托管或其云扩展就是开源 k6 原生能力。JMeter/JMX、Gatling、Locust均仅列为本方案未纳入的对标差异，不列入实施路线。TAP 可消费 BrowserStack 外部报告，但不是实现自身负载能力的前提。

依据：k6 [open/closed模型](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)、[arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/)、[thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)、[分布式运行](https://grafana.com/docs/k6/latest/testing-guides/running-distributed-tests/)，JMeter[远程测试](https://jmeter.apache.org/usermanual/remote-test.html)，Locust[分布式](https://docs.locust.io/en/stable/running-distributed.html)。这些是一手能力证据；选型是结合TAP现有TypeScript脚本与Jenkins约束的设计判断。

| 引擎 | 已核官方能力与限制 | 对TAP决定 |
| --- | --- | --- |
| k6 | JS/TS受控脚本；场景与open arrival-rate、closed VU模型、checks与thresholds、CLI退出码；原生协议负载与浏览器模块是不同能力；分布式官方路径为k6 Operator/Kubernetes，不能把多进程当自动全局统计 | 首期唯一协议引擎；固定镜像版本和脚本包；当前部署先独立Linux runner容器，超过单机经校准能力再扩分布式，不为一期强加Kubernetes |
| JMeter | 成熟JMX/GUI创作和协议生态，正式负载应非GUI；远程节点须准备一致环境；远程运行默认各节点执行整份计划，需要显式分负载和数据，不能简单重复同VUs | 仅作为未纳入的对标差异；本方案不建设JMX导入/执行，不维护第二套引擎 |
| Locust | Python任务、用户等待/权重及master/workers；master不产负载、worker产负载，CPU扩展多进程；用户模型/任务速率不自动等于严格RPS | 与FastAPI语言相同不足以压过k6的开放到达率/发布脚本契合度；本方案不接入 |

## 一条完整使用流程

1. **性能需求**：在 TAP AI 测试管理从已发布需求/SLO、架构/接口文档、生产流量摘要生成性能测试计划草稿；逐项引用来源版本。缺少峰值流量、业务比例、p95/p99、错误预算、容量/持续时间、环境权限、账号/数据要求时标为待确认，不能从UI用例数推断并发。
2. **负载场景**：TAP Load Testing选择API/业务事务、环境、允许目标、数据及凭证引用。首期输入为人工API请求/已审核OpenAPI、已有k6包、脱敏HAR捕获；[HAR转换](https://grafana.com/docs/k6/latest/using-k6/test-authoring/create-tests-from-recordings/using-the-har-converter/)只是候选请求链，删除第三方/遥测/静态噪音、提取登录token/CSRF/动态ID、处理关联与断言后才能运行。URL只能起单请求草稿，不能生成有真实性保证的业务流。
3. **AI辅助生成**：TAP AI把证据与性能需求转成版本化 负载场景定义，TAP校验允许协议、目标、资源预算和脚本依赖，固定编译模板产k6脚本。AI可建议业务比例、参数化、关联/断言、think time、边界和预估资源；不能替用户批准目标、负载、预算，不能自动降低SLO。
4. **隔离调试**：独立调试服务跑1 VU/少量迭代或低到达率，检查请求顺序、变量提取、数据消耗、业务成功、清理、脱敏及统计标签。调试报告明确不代表容量，避免边debug边大量采样日志污染正式数值。
5. **发布版本**：冻结脚本内容哈希、引擎/依赖镜像digest、数据版本、环境、负载模型、阶段、场景权重、地域、门槛、停止规则、采样/保存策略、测量窗和批准记录。恢复旧配置创建新版本；一次run只绑定一份不可变manifest。
6. **正式执行**：TAP API建立MySQL run与幂等键，经Redis任务提示调用Jenkins，Jenkins在专属压测agent池取固定包执行。先目标/DNS/证书/账号/配额/时钟/生成机能力预检，预热、测量、降载、清理顺序固定。正式测量过程不调用模型改脚本或自动改负载。
7. **实时控制**：前端展示请求/迭代目标与实际、VUs、吞吐、错误、延迟和生成机CPU/内存/网络/丢迭代；暂停不作为标准压测恢复手段。人工停止、硬预算、服务红线、生成机失能和心跳过期均可停止；先停止新迭代并限时drain，再强制终止，写明停止原因/未完成样本。控制平面断联由agent租约TTL和本地wall clock硬限时自行停。
8. **报告与改进**：ClickHouse给权威指标/分位数/趋势，MySQL管run/SLO/baseline/审批，对象存储保存原始结果和脱敏样本，TAP AI只在数据完成度允许下解释、给假设与证据链接。修订场景/门槛生成新版本；复测不覆盖失败记录。

## 负载模型与精确语义

- `model=closed`：constant/ramping VUs，用户完成一次再开始下一次，think time算入迭代；服务变慢会减少到达率，不用于证明固定RPS下满足SLO。
- `model=open`：constant/ramping arrival-rate独立调度**迭代开始数/秒**。一个事务若含5请求不能把100迭代/s写成100RPS；报告同时记录目标/实际iteration starts、HTTP requests/s与完成事务/s。只在单请求迭代、无重试/跳转等明确条件下把二者等同。
- 场景含warmup/steady/spike/recovery/soak阶段；预算按全局总量分配到regions/shards，禁止每个节点复制整份global VUs/rate。固定shard manifest、起跑屏障、UTC+monotonic时间、每shard数据分片、唯一用户/迭代标识；多地域实际起跑偏移必须显示。
- `preAllocatedVUs/maxVUs`、CPU/内存/FD/出口带宽和网络端口限制必须实测校准，不能接受BrowserStack“每pod 1000VU”作为TAP容量承诺。到达率未达到或[dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/)超限，标为负载未达成/结果不充分，不能因为请求较少而判通过。
- 自动重试通常关闭；确需模拟业务重试时重试策略固定，所有尝试计流量、单独计业务事务成功，不丢弃失败。

## 服务与数据归属

React TAP前端新增Load Testing页面（Tests/配置与脚本/Run实时视图/Reports与Baseline）；TAP AI前端/后端保留知识、需求、测试计划/用例与AI生成。FastAPI TAP后端持有目标登记、压测场景版本、调试run、正式run、gate和权限；AI通过版本化接口提供草稿和解释，不直接操控agent。

MySQL：performance_test、test_version、scenario、dataset_ref、environment_policy、run、shard_attempt、threshold_policy、baseline、approval、run_event与摄入幂等记录。Redis仅排队提示/短期租约；恢复从MySQL重建。对象存储：原始HAR（限权/脱敏）、脚本包、manifest、完整原始指标批次、日志、采样响应、浏览器证据、报告导出。ClickHouse必选：request_samples或可合并histograms、scenario/step窗口计数、运行阶段、generator_health、SUT关联指标快照、门槛结果与历史趋势。前端不直连ClickHouse/对象存储/agent。k6 [JSON输出](https://grafana.com/docs/k6/latest/results-output/real-time/json/)区分Metric元数据和Point测量点；可作为初期批量摄入的原始证据，但summary-only不够重算任意筛选窗分位数。

协议压测池和真实浏览器负载池分别校准容量并独立隔离；少量浏览器探针与大量浏览器负载也需记录不同实验配置。独立Linux压测池与Web/App功能池、录制调试池、TAP业务服务及AI服务隔离CPU/内存/网络/队列；压测机不能与被测SUT同机争抢资源。私网使用部署在目标网络内的专属agent出站回连控制面；不把BrowserStack Local当作TAP分布式引擎，不承诺隧道能承载容量；云region只是流量源所在地，跟目标环境分别选择。

## 统计、门槛与终态

- 计数可相加，平均延迟用总sum/count，错误率用sum(failed)/sum(requests)，吞吐用**同一个实际测量窗**的sum(count)/seconds，不能把不同窗RPS直接加总。
- p50/p90/p95/p99从原始样本或统一精度、可合并histogram聚合后计算；不能平均节点p95、平均分钟p95或sum百分位。ClickHouse可用同一算法的[AggregateFunction states/merge](https://clickhouse.com/docs/reference/functions/aggregate-functions/combinators)或明确桶边界的直方图；标明近似误差、单位、样本数、过滤条件和窗。BrowserStack engine-count正文“响应时/错误率/吞吐全部sum”的表述不可照抄。
- HTTP延迟、业务事务端到端时间、TTFB、DNS/connect/TLS、浏览器LCP/INP/CLS分别计量；地域、场景、endpoint模板、状态/错误类别可筛选，不把高基数URL/token/userID作为指标标签。
- warmup/cooldown及setup/teardown默认不进steady-state门槛；需在报告显示。HTTP成功不等于业务成功：状态/schema/字段/库存/订单业务断言分开，checks失败必须绑定gate规则，不能只调用check就以为CLI会失败。
- 终态至少区分run lifecycle、SLO verdict、data completeness三轴。`completed`≠`passed`；无门槛、采样不足、未达负载、部分shard丢失、强制停机分别`not_evaluated/inconclusive/aborted`，不可自动绿灯。基线必须同场景/环境/版本/数据/地域/引擎/测量窗可比，配置变更显示差异；基线晋升需人工操作。
- 实时图暂定、晚到数据带watermark、批次seq/idempotent dedupe；按预期分片/批次完成标记和固定截止时间封账，超时生成不完整、无法判定的报告并结束CI等待，补传形成新版本；每次纠错重算有报告revision。凭单个shard exit=0不能替代全局gate，全局percentile阈值在集中完整结果上评估，安全停止规则在agent本地也评估。

## 安全范围与停止能力

目标必须项目登记且获授权（域名/IP/端口/协议/环境/时间窗/最大VUs或到达率/总请求/时长/费用），运行时DNS重解析和每次重定向重新校验，阻止metadata/未批准内网/第三方、不能只检查初始URL。私网明确批准的CIDR绑定专属agent和项目；凭证用secret引用短时下发，HAR/cookie/headers/query/body/logger全部脱敏，不把UI遮眼等同日志脱敏。网络egress allowlist、只读容器文件系统、禁任意依赖下载/任意shell、进程与容器隔离、资源配额和审计事件为执行边界。

运行控制权限拆分创建/编辑、审批/发布、运行、停止、查报告/原始payload、集成凭证管理；停止不能仅依赖创建者在线，项目值班者有授权停机入口。Jenkins取消、TAP取消和agent信号双向同步，不把Jenkins job结束当压测已停；agent结束确认或TTL硬限时后才释放运行租约。

## 文档中已发现必须保留的边界/疑点

- BrowserStack [Throughput](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/throughput)只支持JMeter/Gatling API测试；不代表k6没有open模型，是供应商入口差异。
- [engine count](https://www.browserstack.com/docs/load-testing/advanced-settings/engine-count)仅API，1–10、每pod 1000VU、UI all frameworks而CLI仅JMeter；跟constant VUs页CLI上限100有口径差异，不能合并成统一不限量承诺。
- [authentication](https://www.browserstack.com/docs/load-testing/api-reference/authentication)v1是组级全能力key，无只读/读写分权；TAP要更细项目权限。Runs API触发已保存测试，公开正文未证明创建/编辑脚本API。
- [Runs](https://www.browserstack.com/docs/load-testing/api-reference/runs)状态与SLA verdict分离是可借鉴点；其示例while仅判断completed，failed/aborted会无限等待，TAP必须覆盖全部终态、报告202/503与超时。示例summary p95=2145但阈值actual=1850，不作为统计一致性实现证据。
- [多场景](https://www.browserstack.com/docs/load-testing/configure-load-parameters/configure-multiple-scenarios)浏览器Playwright/Selenium TestNG/WebdriverIO、≤10；Hybrid单共享API（JMeter/k6/Gatling）随浏览器场景启动停止。不能推定Locust也支持Hybrid，不能用该编排替代TAP固定测量窗。
- [masking](https://www.browserstack.com/docs/load-testing/configure-load-parameters/environment-variables)正文有显示范围不一致，但明确脚本stdout不会自动脱敏；TAP从输入到日志做系统性redaction。
- [response capture](https://www.browserstack.com/docs/load-testing/configure-load-parameters/capture-response-details)最多20body/run、保留14天、浏览器只XHR、2xx仅有限样本；成功排除endpoint最多10且不排除error，不能承诺全流量证据或完整敏感信息保护。
- [APM pull](https://www.browserstack.com/docs/load-testing/apm-integration/view-server-metrics)仅Datadog/New Relic/Dynatrace之一、一个host至多10metrics，30–90秒延迟、打开报告实时拉取不保存；TAP需保存带来源和采样窗的快照才有长期可复核报告。
- [AI Insights](https://www.browserstack.com/docs/load-testing/ai-insights)Beta、账户/类型可变，仅完结报告自动建议，follow-up conversation尚为未来；不推定它能自主生成所有压测脚本或在运行期改变负载。
- [baseline](https://www.browserstack.com/docs/load-testing/compare-reports)同test两个completed run可比、每test一个active baseline、公链无账户可看；TAP默认项目认证，公开分享不是默认行为。
- [Hybrid summary](https://www.browserstack.com/docs/load-testing/test-results/hybrid/summary)把错误写成non-200，其他页写4xx/5xx；TAP必须固定预期HTTP状态、传输失败与业务断言口径，合法201/204不能被一概判错。
- [VUH](https://www.browserstack.com/docs/load-testing/references/vu-hours)描述API按每1000预留VU取整、每region至少1engine、browser 1VU/pod且10倍计费、最少5分钟并计启停；同页对共享/分别quota和算例存在冲突。保留供应商计费概念，不能作为TAP容量或费用保证。
- APM CloudWatch仅永久IAM key非temporary/STS、AppDynamics仅SaaS非on-prem、Prometheus/InfluxDB可接可达自管端；均是第三方集成限制，不影响TAP ClickHouse作为必选权威存储。

## Browser / Hybrid 的独立边界

Browser 模式是真实浏览器业务循环，k6 仍是唯一协议引擎、Playwright 承载浏览器侧。浏览器 VU 不能按协议 VU 等额换算，也不默认固定“80% API + 20% browser”。Hybrid 分别定义协议到达率/用户数和浏览器用户数，关联同一目标、场景、时间窗与run ID，资源与统计仍独立；默认少量体验探针，若用户明确选择浏览器容量实验才提高并发。页面导航结束、业务完成、HTTP请求完成是不同计时终点。

已有 LCA 固定版本可提供浏览器流程来源；TAP 自建 Playwright 用户生命周期、业务循环、并发限制和步骤/体验指标采集适配；浏览器首批只承诺并发用户模型，到达率默认用于 k6 协议场景。TAP 的 Playwright 适配去掉运行期模型调用，检查等待条件、业务循环、账号数据、think time、断言与采集开销。不能把功能流程直接复制并发后称为可靠压测，也不假定 BrowserStack LCA 脚本或执行引擎可导出。

BrowserStack [Browser report](https://www.browserstack.com/docs/load-testing/test-results/browser/browser-tab)有Network与Tests，但[Hybrid browser](https://www.browserstack.com/docs/load-testing/test-results/hybrid/browser)明确没有Network；[test.step](https://www.browserstack.com/docs/load-testing/test-results/browser/test-steps)仅Playwright Browser、非Hybrid、默认关闭、10步骤/2层，超量静默丢弃。TAP 自有采集需要显式标记被省略数据，不能机械承诺供应商三模式全部同功能。其[引擎健康页](https://www.browserstack.com/docs/load-testing/test-results/browser/engine-health)用80% CPU/内存作界限；低于此值不足以排除网络/连接/时钟/摄入故障或证明被测系统是根因。

## 录制、迁移及外部来源冻结

[Requestly 网络录制](https://www.browserstack.com/docs/load-testing/requestly-capture-api-calls)有逐请求过滤、Cookie/Header/JSON body提取与复用、低置信关联默认关闭，以及录制think time随机±20%。TAP 借鉴“原始链→清洗→关联→低负载验证→发布”而非直接放大捕获流量。必须删除第三方、记录过滤规则、将动态值来源和目标绑定成显式依赖，提取失败终止事务而非拿录制值继续。

BrowserStack Requestly 每次启动取集合和环境最新值；TAP 正式run应冻结集合内容及非密环境配置、绑定受控密钥版本，保持可复现。[Requestly CLI](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly/cli)称此版本不支持集合与脚本Hybrid，而[环境页](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly/requestly-environment-variables)又列Hybrid CLI配置，故记录入口/版本冲突而不合并成承诺。

[LoadRunner迁移](https://www.browserstack.com/docs/load-testing/migrate/loadrunner)是Web HTTP→k6、TruClient→Playwright；[NeoLoad](https://www.browserstack.com/docs/load-testing/migrate/neoload)协议主要→JMeter，真实浏览器→Playwright，特殊协议/控制器调度/监控连接等有丢失或近似；[TruClient](https://www.browserstack.com/docs/load-testing/migrate/truclient)只针对Web。这些页面明确允许下载**特定转换产出的脚本**，不能外推成BrowserStack管理引擎、普通LCA或所有资产都可导出。迁移成功需差异审查和低负载等价验证；TAP三阶段不建设JMX/Locust/Gatling执行。

## 分期和验收

一期：k6 HTTP(S)协议测试、请求/事务脚本与数据关联、closed/open模型、低负载调试、固定版本发布、Jenkins专属单机agent、运行/停止/预算、ClickHouse统计、阈值/报告/同配置baseline；少量Playwright探针可后随，不阻塞主链。二期：完成Browser独立负载与Hybrid，浏览器并发先实测每worker浏览器数和CPU/内存/网络预算；有容量或地域需求时做分布式固定分片、私网agent pool、服务器指标快照和浏览器体验关联。三期：完善k6场景治理、观测关联、跨版本趋势和自助报告，保持k6为唯一协议引擎；JMX、Locust和Gatling仍是未纳入能力。

验收必须含：已知请求序列和业务断言、token关联和多用户独立数据；open模型目标迭代率与实际RPS差异；服务变慢与dropped iterations；多shard已知数据全局p95/错误率正确；未达负载/缺失数据不能pass；运行过程中无AI改写；一键停止/Jenkins取消/控制面断联/agent失联能在明示上限内停；HAR/日志/附件没有secret；幂等提交、晚到数据重算和基线可比性可追溯。

## 逐页能力与 TAP 映射

| 编号/原始URL | HTTP / 正文 | 具体能力与限制 | TAP映射 |
| --- | --- | --- | --- |
| L001 [/](https://www.browserstack.com/docs/load-testing) | 200 / 正文已读 | 产品区分browser/API/hybrid；托管云、复用功能脚本、API入口宣传JMeter/k6。 | TAP分离协议负载与体验探针，入口不代表完整支持矩阵。 |
| L002 [/advanced-settings](https://www.browserstack.com/docs/load-testing/advanced-settings) | 200 / 正文已读 | 高级配置目录包含engine count、DNS、日志/响应捕获等。 | 独立资源与证据策略配置面板。 |
| L003 [/advanced-settings/engine-count](https://www.browserstack.com/docs/load-testing/advanced-settings/engine-count) | 200 / 正文已读 | API专用engine count 1–10，每pod1000VU；UI全框架，CLI仅JMeter；跨节点统计描述有错误。 | 按实测校准容量并做正确全局统计，不能照抄1000VU容量。 |
| L004 [/advanced-settings/override-dns](https://www.browserstack.com/docs/load-testing/advanced-settings/override-dns) | 200 / 正文已读 | 单次运行hostname→IP，保留Host/SNI/cookie；≤20精确hostname；默认仅public IPv4/IPv6，private需支持。 | 允许目标和运行DNS策略入manifest，校验私网授权。 |
| L005 [/ai-insights](https://www.browserstack.com/docs/load-testing/ai-insights) | 200 / 正文已读 | 完成报告自动AI建议，Beta、分账户，浏览器/API/hybrid信号；没有追问功能。 | TAP AI只解释已算指标与证据，不决策gate。 |
| L006 [/api-reference/authentication](https://www.browserstack.com/docs/load-testing/api-reference/authentication) | 200 / 正文已读 | HTTP Basic账号/key；同group任意有效key可读/停/触发全部run，无read-only。 | TAP按项目和操作分权，服务账号最小scope。 |
| L007 [/api-reference/introduction](https://www.browserstack.com/docs/load-testing/api-reference/introduction) | 200 / 正文已读 | REST JSON，默认100req/min/user与10并发，依plan。 | 查询限流、退避及SSE节流。 |
| L008 [/api-reference/runs](https://www.browserstack.com/docs/load-testing/api-reference/runs) | 200 / 正文已读 | 已保存test/version异步POST202；status/report/stop；report202未就绪；stop幂等且异步；SLA与lifecycle分离。 | 幂等运行、全部终态收敛、全局结果和完成度三轴。 |
| L009 [/apm-integration](https://www.browserstack.com/docs/load-testing/apm-integration) | 200 / 正文已读 | APM目录7种工具，有push及部分pull。 | TAP保存权威统计，APM只是关联来源。 |
| L010 [/apm-integration/appdynamics](https://www.browserstack.com/docs/load-testing/apm-integration/appdynamics) | 200 / 正文已读 | AppDynamics SaaS-only，API client+Analytics key，10秒桶、dashboard/events；不支持W3C trace headers。 | 可选插件式集成，不复制其权限过宽示例。 |
| L011 [/apm-integration/aws-cloudwatch](https://www.browserstack.com/docs/load-testing/apm-integration/aws-cloudwatch) | 200 / 正文已读 | CloudWatch10秒指标/events/dashboard；只支持永久IAM AKIA key、单region/同账户，非STS。 | 按现有企业观测能力选择，不让AWS凭证成为核心依赖。 |
| L012 [/apm-integration/datadog](https://www.browserstack.com/docs/load-testing/apm-integration/datadog) | 200 / 正文已读 | Datadog API/app key、10秒聚合push、事件、自定义trace header、dashboard。 | 时间相关性不等于因果；保留run/phase标识。 |
| L013 [/apm-integration/dynatrace](https://www.browserstack.com/docs/load-testing/apm-integration/dynatrace) | 200 / 正文已读 | Dynatrace SaaS/Managed的v2 ingestion，平台token、维度/事件，custom metrics有quota。 | 外部指标配额与连接健康独立状态。 |
| L014 [/apm-integration/influxdb](https://www.browserstack.com/docs/load-testing/apm-integration/influxdb) | 200 / 正文已读 | InfluxDB+Grafana7项凭证，21指标10秒桶、10事件、可达自管/onprem可接，Grafana≥9。 | 不新增必选时序库，ClickHouse分析不被替代。 |
| L015 [/apm-integration/new-relic](https://www.browserstack.com/docs/load-testing/apm-integration/new-relic) | 200 / 正文已读 | New Relic account/user API/license key及US/EU，指标/events/custom tracing/dashboard。 | 凭证引用、来源单位标准化、可选关联。 |
| L016 [/apm-integration/prometheus](https://www.browserstack.com/docs/load-testing/apm-integration/prometheus) | 200 / 正文已读 | Prometheus remote write+Grafana，21指标10秒桶、6项凭证，Cortex/Mimir/自管可接，underscore标签。 | 不把percentile gauge二次聚合成全局p95。 |
| L017 [/apm-integration/view-server-metrics](https://www.browserstack.com/docs/load-testing/apm-integration/view-server-metrics) | 200 / 正文已读 | pull只Datadog/New Relic/Dynatrace之一、1host≤10metrics；30–90秒延迟；每次打开实时查询不保存。 | 保存带来源和窗口的SUT快照，报告缺指标明确标注。 |
| L018 [/cicd-integration](https://www.browserstack.com/docs/load-testing/cicd-integration) | 200 / 正文已读 | CI目录有9种适配指南，依托供应商CLI。 | TAP正式入口只Jenkins先闭环。 |
| L019 [/cicd-integration/aws-codebuild](https://www.browserstack.com/docs/load-testing/cicd-integration/aws-codebuild) | 200 / 正文已读 | CodeBuild从Secrets Manager拉凭证、下载CLI跑示例、JUnit工件。 | 如外部报告接入保留CI run关联，非首期新增调度。 |
| L020 [/cicd-integration/azure](https://www.browserstack.com/docs/load-testing/cicd-integration/azure) | 200 / 正文已读 | Azure pipeline跨OS下载CLI，JUnit在成功失败均发布。 | Jenkins失败后也归档证据。 |
| L021 [/cicd-integration/bamboo](https://www.browserstack.com/docs/load-testing/cicd-integration/bamboo) | 200 / 正文已读 | Bamboo Server/DC，变量_password遮盖、Specs和工件。 | 借鉴secret注入，不引入第二CI。 |
| L022 [/cicd-integration/bitbucket-pipelines](https://www.browserstack.com/docs/load-testing/cicd-integration/bitbucket-pipelines) | 200 / 正文已读 | Bitbucket secured变量及Pipelines artifacts，配置固定路径。 | 外部来源通过通用报告协议接入。 |
| L023 [/cicd-integration/circleci](https://www.browserstack.com/docs/load-testing/cicd-integration/circleci) | 200 / 正文已读 | CircleCI变量/contexts、下载CLI、工件及test results。 | 只复用CI状态与报告模型。 |
| L024 [/cicd-integration/github-actions](https://www.browserstack.com/docs/load-testing/cicd-integration/github-actions) | 200 / 正文已读 | GitHub Actions secrets、CLI、always上传JUnit。 | 结果接入与Jenkins运行职责区分。 |
| L025 [/cicd-integration/gitlab-ci](https://www.browserstack.com/docs/load-testing/cicd-integration/gitlab-ci) | 200 / 正文已读 | GitLab masked变量、JUnit/artifacts always，Linux默认。 | 接收CI结果不等于扩建CI执行器。 |
| L026 [/cicd-integration/jenkins](https://www.browserstack.com/docs/load-testing/cicd-integration/jenkins) | 200 / 正文已读 | Jenkins凭证注入、OS/arch下载CLI、执行、JUnit、失败propagate。 | 专属压测agent、固定镜像/版本，取消必须停真实负载。 |
| L027 [/cicd-integration/teamcity](https://www.browserstack.com/docs/load-testing/cicd-integration/teamcity) | 200 / 正文已读 | TeamCity password env参数、Kotlin DSL、Linux agent/artifacts。 | 保留外部source ID，不复制三方基础设施。 |
| L028 [/collaboration-tools](https://www.browserstack.com/docs/load-testing/collaboration-tools) | 200 / 正文已读 | 协作目录Slack/Teams/email。 | 按用户订阅和权限发送，不默认群发。 |
| L029 [/collaboration-tools/email-notifications](https://www.browserstack.com/docs/load-testing/collaboration-tools/email-notifications) | 200 / 正文已读 | 按Initializing/Completed/Aborted/Error/Passed/Failed和收件人配置email。 | 区分生命周期和质量结论通知。 |
| L030 [/collaboration-tools/ms-teams](https://www.browserstack.com/docs/load-testing/collaboration-tools/ms-teams) | 200 / 正文已读 | Teams OAuth需组织admin和workspace授权，通知/日汇总。 | 连接scope与通知策略可审计。 |
| L031 [/collaboration-tools/slack](https://www.browserstack.com/docs/load-testing/collaboration-tools/slack) | 200 / 正文已读 | Slack失败/每日build汇总、用户级订阅，复用已有连接。 | TAP按project/run授权链接，避免敏感payload通知。 |
| L032 [/comments](https://www.browserstack.com/docs/load-testing/comments) | 200 / 正文已读 | 报告/tab/section评论、@≤10、3000字符、60天留存、10秒refresh；公链可读不可写。 | 评论与证据定位、状态分权、企业留存策略。 |
| L033 [/compare-reports](https://www.browserstack.com/docs/load-testing/compare-reports) | 200 / 正文已读 | 同test两completed runs比较，一个active baseline，差异提示与百分比delta，公链无需账号。 | 可比性检查、人工基线晋升、默认认证分享。 |
| L034 [/configuration-history](https://www.browserstack.com/docs/load-testing/configuration-history) | 200 / 正文已读 | 每次保存版本，restore创建新版本、两版本diff、legacy未知作者。 | 不可变manifest、restore不覆盖历史。 |
| L035 [/configure-load-parameters](https://www.browserstack.com/docs/load-testing/configure-load-parameters) | 200 / 正文已读 | 负载配置目录。 | TAP保持清晰配置分类但由具体leaf约束支持。 |
| L036 [/configure-load-parameters/add-tags](https://www.browserstack.com/docs/load-testing/configure-load-parameters/add-tags) | 200 / 正文已读 | test标签继承后续run、run标签仅该run，和k6请求筛选标签不同。 | 组织标签与metric标签分开，防高基数。 |
| L037 [/configure-load-parameters/capture-response-details](https://www.browserstack.com/docs/load-testing/configure-load-parameters/capture-response-details) | 200 / 正文已读 | 响应样本默认关闭、≤20/run保留14天、浏览器仅XHR，最多排除10个成功endpoint不含error。 | 脱敏、采样与保留策略固定，不能承诺全请求body。 |
| L038 [/configure-load-parameters/configure-multiple-scenarios](https://www.browserstack.com/docs/load-testing/configure-load-parameters/configure-multiple-scenarios) | 200 / 正文已读 | browser≤10场景，Playwright/TestNG/WDIO；individual或sum100%权重；Hybrid仅单共享API JMeter/k6/Gatling。 | 协议场景主测量窗独立，browser探针不截断压力窗。 |
| L039 [/configure-load-parameters/environment-variables](https://www.browserstack.com/docs/load-testing/configure-load-parameters/environment-variables) | 200 / 正文已读 | env多文件支持，CLI≤20键；isMasked不遮stdout；JMeter映射__P。 | 凭证只引用，输入和输出统一脱敏。 |
| L040 [/configure-load-parameters/filter-k6-tests-by-tag](https://www.browserstack.com/docs/load-testing/configure-load-parameters/filter-k6-tests-by-tag) | 200 / 正文已读 | 供应商k6请求tag/group include/exclude，精确值/前缀组OR，≤50/层64字符；跳过HTTP但其余JS仍跑。 | 不当作k6原生能力；排除请求必须验证关联和业务语义。 |
| L041 [/configure-load-parameters/headful-headless-mode](https://www.browserstack.com/docs/load-testing/configure-load-parameters/headful-headless-mode) | 200 / 正文已读 | browser全局headless默认、UI覆盖script、不能每scenario不同；API无该配置。 | 探针模式固定入基线；headless仍真实渲染，不能把营销表述当无渲染。 |
| L042 [/configure-load-parameters/load-profiles](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles) | 200 / 正文已读 | 五profile目录constant/ramping/perVU/spike/throughput。 | TAP显式closed/open模型。 |
| L043 [/configure-load-parameters/load-profiles/constant-vus](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/constant-vus) | 200 / 正文已读 | constant VUs各VU循环到duration；CLI页面上限100与新engine页口径不同。 | 不照抄统一并发上限；先低负载调试。 |
| L044 [/configure-load-parameters/load-profiles/per-vu-iterations](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/per-vu-iterations) | 200 / 正文已读 | 每VU迭代1–100000，duration与iteration任一到达终止，Hybrid全局/局部覆盖。 | 总迭代/每VU迭代/时限区分，未完成计数。 |
| L045 [/configure-load-parameters/load-profiles/ramping-vus](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/ramping-vus) | 200 / 正文已读 | ramp/hold/down可多stage、峰值0秒跳变，stage不超过global VUs。 | 阶段定义、变更预算、稳态窗口版本化。 |
| L046 [/configure-load-parameters/load-profiles/spike-vus](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/spike-vus) | 200 / 正文已读 | API-only spike，baseline上下三角无hold，≤10spikes、recovery gap。 | 模型图精确表达实际曲线，峰值hold另用阶段。 |
| L047 [/configure-load-parameters/load-profiles/throughput](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-profiles/throughput) | 200 / 正文已读 | Throughput仅JMeter/Gatling API，target RPS+max VUs+duration≥10s，顶到VUs后RPS可不足。 | TAP k6 open是迭代到达率，实际请求率另测。 |
| L048 [/configure-load-parameters/load-zones](https://www.browserstack.com/docs/load-testing/configure-load-parameters/load-zones) | 200 / 正文已读 | Virginia/N California/Mumbai/London/Frankfurt五区域，比例sum100%。 | region源与目标分开，按固定分片分全局总负载。 |
| L049 [/configure-load-parameters/network-throttling](https://www.browserstack.com/docs/load-testing/configure-load-parameters/network-throttling) | 200 / 正文已读 | Browser/API可限latency/upload/download/loss、预设3G/slow4G/fast4G；正文排除Hybrid。 | 网络条件入基线，不能承诺其他页所有模式兼容。 |
| L050 [/configure-load-parameters/real-time-load-injection](https://www.browserstack.com/docs/load-testing/configure-load-parameters/real-time-load-injection) | 200 / 正文已读 | 实时加VUs/延时各一次，API k6/JMeter及browser PW/Selenium；仅constant或ramp hold，非Hybrid，生效1–3分钟/分钟级。 | 首期固定正式测量，探索变更单独审计不得自动当baseline。 |
| L051 [/configure-load-parameters/requestly-environment-variables](https://www.browserstack.com/docs/load-testing/configure-load-parameters/requestly-environment-variables) | 200 / meta_redirect | Requestly environment旧入口，仅meta redirect；正确正文单列L103；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 别名去重，不重复计算能力。 |
| L052 [/configure-load-parameters/run-command](https://www.browserstack.com/docs/load-testing/configure-load-parameters/run-command) | 200 / 正文已读 | runCommand safe-list按framework，≤4096字符，不支持Nightwatch/k6；拒绝worker/reporter/shard等越界参数，job-level覆盖selector。 | 参数结构化白名单，禁shell自由拼接和绕过采集。 |
| L053 [/configure-load-parameters/set-thresholds](https://www.browserstack.com/docs/load-testing/configure-load-parameters/set-thresholds) | 200 / 正文已读 | 全局/请求阈值；scenario仅browser，单位含ms/s/%，基本metric列表p90而子页出现p95。 | 门槛有版本化类型/单位/适用范围，能力探测。 |
| L054 [/configure-load-parameters/set-thresholds/fail-on-threshold-breach](https://www.browserstack.com/docs/load-testing/configure-load-parameters/set-thresholds/fail-on-threshold-breach) | 200 / 正文已读 | 30秒grace与30秒累计评估，≤30thresholds；missing跳过；scenario breach只停该scenario父不自动fail；提及jobs API未在public Runs完整定义。 | 安全红线独立于稳态gate，缺样本不可绿灯，API能力不越界推断。 |
| L055 [/configure-load-parameters/set-thresholds/request-thresholds](https://www.browserstack.com/docs/load-testing/configure-load-parameters/set-thresholds/request-thresholds) | 200 / 正文已读 | URL/k6 tag/JMeter label单一/pattern/group；Hybrid同URL跨两组件混合；unmatched跳过，partial仅现有。 | 明确source/metric scope，缺必需endpoint判不充分，不默认混合探针协议分位数。 |
| L056 [/configure-load-parameters/store-execution-logs](https://www.browserstack.com/docs/load-testing/configure-load-parameters/store-execution-logs) | 200 / 正文已读 | 默认仅失败VU日志，storeAllLogs仅BLU及Hybrid browser，API-only不支持。 | 采样日志与指标采集分离，失败证据成本受控。 |
| L057 [/configure-load-parameters/store-execution-logs/logs-and-artifacts](https://www.browserstack.com/docs/load-testing/configure-load-parameters/store-execution-logs/logs-and-artifacts) | 200 / 正文已读 | protocol-only artifacts开关默认off，k6 summary/JMeter jtl/Gatling/Locust文件及指定env目录，自定义body写入受控路径。 | 原始批次与聚合摘要都保存，summary不能替代全局样本。 |
| L058 [/configure-load-parameters/test-data](https://www.browserstack.com/docs/load-testing/configure-load-parameters/test-data) | 200 / 正文已读 | CSV/JSON各≤10MB/100列、5文件/test、库50文件，sequential循环或random重复；固定data版本；browser自行读CSV；Requestly仅1文件。 | 明确分片/唯一性/耗尽策略，不把顺序循环当唯一账号保证。 |
| L059 [/configure-load-parameters/test-private-websites-and-apis](https://www.browserstack.com/docs/load-testing/configure-load-parameters/test-private-websites-and-apis) | 200 / 正文已读 | 固定源IP allowlist按所选五regions；未描述自托管agent/Local隧道。 | TAP私网agent出站回连是自主方案，需路由和目标授权。 |
| L060 [/dashboards](https://www.browserstack.com/docs/load-testing/dashboards) | 200 / 正文已读 | 项目dashboard模板/拖拽/双轴/filter/公链/PDF；所有成员可改删、last-write-wins；API无web-vitals。 | 统一报表组件和类型兼容，编辑权限/并发版本校验更严格。 |
| L061 [/debug](https://www.browserstack.com/docs/load-testing/debug) | 200 / 正文已读 | debug目录分browser/API/hybrid。 | 按运行域分诊。 |
| L062 [/debug/debugging-api-load-tests](https://www.browserstack.com/docs/load-testing/debug/debugging-api-load-tests) | 200 / 正文已读 | API按error/latency/throughput/generator诊断，4xx/5xx/网络分类、基线比较。 | 不能照抄(VUs×iterations)/duration当多请求RPS；不要自动放宽断言。 |
| L063 [/debug/debugging-browser-load-tests](https://www.browserstack.com/docs/load-testing/debug/debugging-browser-load-tests) | 200 / 正文已读 | browser按network、vitals、视频/错误、资源诊断。 | 探针证据与协议SLO各自评估。 |
| L064 [/debug/debugging-hybrid-load-tests](https://www.browserstack.com/docs/load-testing/debug/debugging-hybrid-load-tests) | 200 / 正文已读 | Hybrid分组件对照standalone及启动时差/数据争用，未证明相关即因果。 | 固定阶段、统一run ID、独立资源池并提示测量偏差。 |
| L065 [/exclude-domains](https://www.browserstack.com/docs/load-testing/exclude-domains) | 200 / 正文已读 | browser报告域排除capture阶段丢指标，≤10 hostname规则contains/equals；不阻止网络发送，API不适用。 | 安全egress必须另外实现，报告过滤不能替代允许目标。 |
| L066 [/export-csv](https://www.browserstack.com/docs/load-testing/export-csv) | 200 / 正文已读 | completed且owner才CSV，ZIP多个类型表、尊重当前filter、最小5秒桶、单CSV1048576行分卷。 | 保留导出过滤/聚合粒度，不能拿供应商CSV当原始逐request全量。 |
| L067 [/get-started](https://www.browserstack.com/docs/load-testing/get-started) | 200 / 正文已读 | get-started目录分browser/API/hybrid。 | 用户先选测量目的与模型。 |
| L068 [/get-started/browser-load-testing](https://www.browserstack.com/docs/load-testing/get-started/browser-load-testing) | 200 / 正文已读 | browser引导列Selenium Java/Python/Jest、Playwright、WDIO、Nightwatch。 | 既定Playwright足够，框架广度不等于首期需求。 |
| L069 [/get-started/browser-load-testing/run-test](https://www.browserstack.com/docs/load-testing/get-started/browser-load-testing/run-test) | 200 / 正文已读 | browser run-test路由页指向各框架，无额外引擎能力。 | 路由页单独登记，不充作深度能力证据。 |
| L070 [/get-started/hybrid-load-testing](https://www.browserstack.com/docs/load-testing/get-started/hybrid-load-testing) | 200 / 正文已读 | Hybrid引导复用browser+JMeter/k6，并列六browser入口。 | 功能脚本与协议脚本是两个输入，不自动互转。 |
| L071 [/get-started/hybrid-load-testing/run-test](https://www.browserstack.com/docs/load-testing/get-started/hybrid-load-testing/run-test) | 200 / 正文已读 | Hybrid run-test导航页。 | 只作流程入口。 |
| L072 [/get-started/protocol-load-testing](https://www.browserstack.com/docs/load-testing/get-started/protocol-load-testing) | 200 / 正文已读 | API目录实际JMeter/k6/Gatling/Locust四引擎。 | 供应商多引擎不迫使TAP多引擎。 |
| L073 [/get-started/protocol-load-testing/run-test](https://www.browserstack.com/docs/load-testing/get-started/protocol-load-testing/run-test) | 200 / 正文已读 | API run-test导航页。 | 具体格式/版本看引擎正文。 |
| L074 [/getting-started/browser/nightwatch](https://www.browserstack.com/docs/load-testing/getting-started/browser/nightwatch) | 200 / 正文已读 | Nightwatch3.16/Node24，ZIP≤250MB、root package/config、plugin、CLI init/run。 | 不引入Nightwatch，借鉴版本/依赖检测。 |
| L075 [/getting-started/browser/playwright](https://www.browserstack.com/docs/load-testing/getting-started/browser/playwright) | 200 / 正文已读 | Playwright NodeJS及Robot标注，Node24，版本1.58–1.62，ZIP≤250MB、config/root/testDir、SDK。 | 既有PW探针固定镜像；正文CLI仍写仅NodeJS，Robot范围需核。 |
| L076 [/getting-started/browser/selenium](https://www.browserstack.com/docs/load-testing/getting-started/browser/selenium) | 200 / 正文已读 | Selenium Java/JUnit/TestNG/Cucumber/Serenity、JDK21/17；ZIP≤250MB；Maven-only正文且每run首个config。 | 不新增Selenium，记录其版本/框架限制与新runCommand Gradle差异。 |
| L077 [/getting-started/browser/selenium-jest](https://www.browserstack.com/docs/load-testing/getting-started/browser/selenium-jest) | 200 / 正文已读 | Selenium Jest Node21，Jest自动发现、root package、ZIP≤250MB不含node_modules，本地hub RemoteWebDriver。 | 运行包依赖可再现，不复用任意本地webdriver路径。 |
| L078 [/getting-started/browser/selenium-python](https://www.browserstack.com/docs/load-testing/getting-started/browser/selenium-python) | 200 / 正文已读 | Selenium Python/pytest，requirements与runTarget，runTarget仅vanilla非pytest。 | 框架差异需adapter显式声明。 |
| L079 [/getting-started/browser/webdriverio](https://www.browserstack.com/docs/load-testing/getting-started/browser/webdriverio) | 200 / 正文已读 | WDIO9.29/Node24，config/package root、ZIP≤250MB、headless可配。 | 沿用App功能WDIO不等于直接支持协议负载。 |
| L080 [/getting-started/hybrid/nightwatch](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/nightwatch) | 200 / 正文已读 | Nightwatch+JMeter5.6.3/k6 2.0.0，browser ZIP250MB、协议50MB、browser/API VU比例。 | 两类VU不可按成本/吞吐等价合算。 |
| L081 [/getting-started/hybrid/playwright](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/playwright) | 200 / 正文已读 | Playwright+JMeter/k6两个脚本，browser/API VU份额，分别上传；Hybrid版本表k6 2.0而API页2.2。 | 同run编排两种测量，不能自动转换PW为协议请求。 |
| L082 [/getting-started/hybrid/selenium](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/selenium) | 200 / 正文已读 | Selenium Java+JMeter/k6，Java21、testng/cucumber配置、JMX/JS输入。 | 对标能力未纳入TAP额外框架。 |
| L083 [/getting-started/hybrid/selenium-jest](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/selenium-jest) | 200 / 正文已读 | Selenium Jest+JMeter/k6，package依赖与hub、ZIP项目加协议脚本。 | 独立资源预算/模式，不照抄比例成本假设。 |
| L084 [/getting-started/hybrid/selenium-python](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/selenium-python) | 200 / 正文已读 | Selenium Python/pytest+JMeter/k6，requirements和pytest入口；runTarget说明与单browser页口径不同。 | 未纳入额外框架，脚本adapter须实测。 |
| L085 [/getting-started/hybrid/webdriverio](https://www.browserstack.com/docs/load-testing/getting-started/hybrid/webdriverio) | 200 / 正文已读 | WDIO+JMeter/k6，browser config及protocol文件、区域与VU比例。 | 移动功能WDIO不等于手机API压力生成器。 |
| L086 [/getting-started/protocol/api-collections](https://www.browserstack.com/docs/load-testing/getting-started/protocol/api-collections) | 200 / 正文已读 | Requestly collection可导入Postman/HAR，live读取collection/env、pre/post scripts；multipart文件丢弃；该页称全profiles。 | TAP固定请求快照、清理/关联/断言后k6生成；throughput跨页冲突需核。 |
| L087 [/getting-started/protocol/gatling](https://www.browserstack.com/docs/load-testing/getting-started/protocol/gatling) | 200 / 正文已读 | Gatling3.10.5，Java/Scala/Kotlin、Maven/Gradle/SBT或standalone，ZIP100MB，FQCN选择，CLI1000上限。 | 只列未纳入能力，不新增引擎。 |
| L088 [/getting-started/protocol/jmeter](https://www.browserstack.com/docs/load-testing/getting-started/protocol/jmeter) | 200 / 正文已读 | JMeter5.6.3、JMX/ZIP100MB；single JMX可script-mode，Standard/Ultimate/Stepping、Open best-effort，Concurrency/Arrivals不支持；setup/teardown每engine执行。 | 只列未纳入；指出engine支持范围与L003冲突、插件不是全支持。 |
| L089 [/getting-started/protocol/k6](https://www.browserstack.com/docs/load-testing/getting-started/protocol/k6) | 200 / 正文已读 | k6 2.2.0原生JS/TS，单文件或ZIP≤100MB、多文件保留目录/entry必填，CLI页1000VU上限。 | TAP自托管原生k6固定版本，BrowserStack筛tag/托管配额不属原生契约。 |
| L090 [/getting-started/protocol/locust](https://www.browserstack.com/docs/load-testing/getting-started/protocol/locust) | 200 / 正文已读 | Locust2.32.4支持单py或多文件ZIP≤100MB、user subclasses、root requirements安装限120s。 | 未纳入TAP，Python同栈不构成换引擎理由。 |
| L091 [/integrations](https://www.browserstack.com/docs/load-testing/integrations) | 200 / 正文已读 | 集成目录覆盖CI/APM/LCA/协作。 | 职责分层，不把integration当独立引擎。 |
| L092 [/integrations/requestly](https://www.browserstack.com/docs/load-testing/integrations/requestly) | 200 / 正文已读 | Requestly从CLI collection运行或UI预填跳Load Testing。 | 协议创作与负载配置连接，TAP自行定义快照接口。 |
| L093 [/lca-integration](https://www.browserstack.com/docs/load-testing/lca-integration) | 200 / 正文已读 | LCA桌面录browser journey，Web仅查看；URL/viewport、验证/提取变量/CSV，保存挂脚本、编辑下次用最新。 | 沿用Web录制给少量探针；不把UI操作自动当协议压力脚本。 |
| L094 [/migrate](https://www.browserstack.com/docs/load-testing/migrate) | 200 / 正文已读 | AI迁移目录：LoadRunner HTTP→k6、TruClient→PW、NeoLoad→JMeter/PW。 | 迁移也是草稿需验证；TAP不列多引擎迁移为实施目标。 |
| L095 [/migrate/loadrunner](https://www.browserstack.com/docs/load-testing/migrate/loadrunner) | 200 / 正文已读 | AI迁移VuGen HTTP→k6可下载，TruClient→PW，非HTTP不支持；Controller/VU/network不搬，init/end不能一对一；上传250/500MB正文矛盾。 | 证明仅转换脚本可下载，不证明云引擎导出；迁移不等同验证成功。 |
| L096 [/migrate/neoload](https://www.browserstack.com/docs/load-testing/migrate/neoload) | 200 / 正文已读 | NeoLoad8.x–2025.x/≤500MB、NLAC可；scenario×population×path各建test，协议JMeter/RealBrowser PW；WS/MQTT等不搬，rendezvous/SharedQueue/SQL部分或无。 | 测试语义损失清单、转换逐实体状态可借鉴；TAP不纳入该迁移引擎链。 |
| L097 [/migrate/truclient](https://www.browserstack.com/docs/load-testing/migrate/truclient) | 200 / 正文已读 | TruClientWeb→PW可下载test/config/utils TS；Native Mobile/跨迭代事务/运行设置不搬，EvaluateC best-effort；batch≤50，250/500MB口径冲突。 | 迁移草稿先低VU真实验证，下载代码不等于供应商平台可移植。 |
| L098 [/overview/what-is-load-testing](https://www.browserstack.com/docs/load-testing/overview/what-is-load-testing) | 200 / 正文已读 | overview区分real browser、direct API与Hybrid，云自管负担由供应商承担，举例比leaf支持矩阵窄。 | 采用测量领域分离，不拿营销overview替代逐页边界。 |
| L099 [/recording-and-capture](https://www.browserstack.com/docs/load-testing/recording-and-capture) | 200 / 正文已读 | 录制与捕获入口，串联 Requestly 网络录制、请求过滤、关联与集合运行；不是单 URL 自动完整业务建模保证 | TAP 将网络捕获独立为草稿入口，审核后转受控 k6 |
| L100 [/recording-and-capture/requestly](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly) | 200 / 正文已读 | Requestly 文档入口分录制、环境、CLI、集合执行 | 统一列输入来源与前置条件，版本发布与运行分开 |
| L101 [/recording-and-capture/requestly/cli](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly/cli) | 200 / 正文已读 | CLI 必填 Requestly project/collection UUID，不与 files 共存；此页明确本版本不支持 Requestly 与脚本混合 Hybrid | 记录与 L103 Hybrid 配置冲突；不推定所有入口支持一致 |
| L102 [/recording-and-capture/requestly/collection-execution](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly/collection-execution) | 200 / 正文已读 | 从 Requestly 跳入仅预填向导并未自动保存；默认项目/映射，集合改名删除不丢历史报告 | 稳定 source ID 与历史快照，创建草稿不自动开压 |
| L103 [/recording-and-capture/requestly/requestly-environment-variables](https://www.browserstack.com/docs/load-testing/recording-and-capture/requestly/requestly-environment-variables) | 200 / 正文已读 | 运行时取环境值，仅保存环境 ID，Global 默认；UI/CLI 且列 Hybrid tests 配置，未解析变量可能原样请求 | 冻结非密环境快照与 secret 版本引用，未解析变量预检失败；与 L101 对照 |
| L104 [/references](https://www.browserstack.com/docs/load-testing/references) | 200 / 正文已读 | 参考文档索引，关联 FAQ 和 VUH 计费 | TAP 提供容量/资源计量解释，索引不当独立能力 |
| L105 [/references/faqs](https://www.browserstack.com/docs/load-testing/references/faqs) | 200 / 正文已读 | FAQ 默认 10 次/月、20 分钟、100 VUs，需联系扩额；版本与各框架页可能不同；随机 think time 模拟行为 | 容量数字不作为 TAP 承诺；open arrival-rate 末尾不机械加 pacing sleep |
| L106 [/references/vu-hours](https://www.browserstack.com/docs/load-testing/references/vu-hours) | 200 / 正文已读 | VUH 引擎按每 1000 API VU 向上取整，区域至少一引擎，浏览器 1 VU/pod×10、最少计5分钟及启动清理；同页共享/分别配额与示例自相矛盾 | 区分配置 VUs、预留资源和实耗，TAP 自己校准单浏览器成本，不抄供应商费用公式 |
| L107 [/rename-test](https://www.browserstack.com/docs/load-testing/rename-test) | 200 / 正文已读 | 名称≤255项目内唯一，改名留历史/链接；运行中不能改；CLI按旧名可能误报入新同名test | 稳定 ID 绑定 CI，名称仅显示字段，改名审计 |
| L108 [/reports](https://www.browserstack.com/docs/load-testing/reports) | 200 / 正文已读 | 报告入口联合 AI、对比、趋势、评论、CSV、仪表板/PDF | 统一报告导航；每种输出限权与同一指标版本 |
| L109 [/requestly-capture-api-calls](https://www.browserstack.com/docs/load-testing/requestly-capture-api-calls) | 200 / 正文已读 | Requestly 录制 API/Hybrid，XHR/fetch、缓存/service worker、iframe、±20% think time；method/host/type与优先 glob/regex过滤；cookie/header/body 动态关联+低置信默认不启用；集合每次拉最新 | HAR/录制链生成 k6 草稿，来源/变量依赖显式，低VU验证、版本冻结和第三方排除 |
| L110 [/requestly-cli](https://www.browserstack.com/docs/load-testing/requestly-cli) | 200 / meta_redirect | 旧 Requestly CLI 入口为 HTML meta redirect，详见 canonical/redirect 审计；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 按别名记录，正文读 L101，不重复计文章 |
| L111 [/test-results/browser](https://www.browserstack.com/docs/load-testing/test-results/browser) | 200 / 正文已读 | 浏览器报告元数据、复跑/基线/PDF、公私分享、全局聚合与region/time筛选；默认 p75 可项目设置 | 独立 browser 模式与正式运行溯源；筛选与指标口径透明 |
| L112 [/test-results/browser/browser-tab](https://www.browserstack.com/docs/load-testing/test-results/browser/browser-tab) | 200 / 正文已读 | Browser含WebVitals/Network/Tests，页面及请求钻取，Tests详情需 entitlement；明确 Network 仅Browser不Hybrid | 真实浏览器并发独立容量池；协议/页面/网络三类数据分开，不假定Hybrid天然有同样观测 |
| L113 [/test-results/browser/engine-health](https://www.browserstack.com/docs/load-testing/test-results/browser/engine-health) | 200 / 正文已读 | 引擎CPU或内存任一点>80%标不健康；文档声称低于阈值即可排除发压端故障，因果过强 | TAP联查网络/FD/CPU throttling/丢迭代/时钟/摄入延迟，不能以CPU健康证明SUT根因 |
| L114 [/test-results/browser/execution-logs](https://www.browserstack.com/docs/load-testing/test-results/browser/execution-logs) | 200 / 正文已读 | 每VU session日志、成功失败切换、region/time/scenario筛选、下载；默认仅失败日志 | 证据采样开销固定并脱敏，保留失败且不过度采全量 |
| L115 [/test-results/browser/network](https://www.browserstack.com/docs/load-testing/test-results/browser/network) | 200 / meta_redirect | 旧 browser network HTML meta redirect，正文内容并入 Browser 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保存别名与目标，避免重复计数 |
| L116 [/test-results/browser/page-load-time](https://www.browserstack.com/docs/load-testing/test-results/browser/page-load-time) | 200 / 正文已读 | Page load time至window.load，提供redirect/DNS/TCP TLS/TTFB/download/DOM/render阶段；browser/hybrid可设门槛 | 页面load不等于SPA业务完成，TAP显式业务ready断言及事务计时 |
| L117 [/test-results/browser/summary](https://www.browserstack.com/docs/load-testing/test-results/browser/summary) | 200 / 正文已读 | 浏览器summary含测试状态、实际/预期VU、网络RPS/错误/百分位、page load与vitals，scenario统一筛选；AI建议需核对 | 浏览器HTTP采样不能冒充协议引擎吞吐；功能通过与SLO分别展示 |
| L118 [/test-results/browser/test-steps](https://www.browserstack.com/docs/load-testing/test-results/browser/test-steps) | 200 / 正文已读 | Playwright browser独有 test.step opt-in，最多10步骤/2嵌套，超出静默丢弃；Hybrid/API/其他框架不支持 | TAP自有适配和显式采集上限提示，步骤时间保留父子关系，不相加重复计时 |
| L119 [/test-results/browser/tests](https://www.browserstack.com/docs/load-testing/test-results/browser/tests) | 200 / meta_redirect | 旧 browser tests 入口 HTML meta redirect，内容并入 Browser 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L120 [/test-results/browser/web-vitals](https://www.browserstack.com/docs/load-testing/test-results/browser/web-vitals) | 200 / meta_redirect | 旧 browser web-vitals 入口 HTML meta redirect，内容并入 Browser 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L121 [/test-results/hybrid](https://www.browserstack.com/docs/load-testing/test-results/hybrid) | 200 / 正文已读 | Hybrid报告明确分API和Browser VUs，公共复跑/比较/分享/聚合控制；APIs/Browser/Logs/Engine四域 | Hybrid run统一身份，但负载、容量、统计按组件分开 |
| L122 [/test-results/hybrid/api](https://www.browserstack.com/docs/load-testing/test-results/hybrid/api) | 200 / meta_redirect | 旧 hybrid api 入口 HTML meta redirect，内容并入 APIs 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L123 [/test-results/hybrid/apis](https://www.browserstack.com/docs/load-testing/test-results/hybrid/apis) | 200 / 正文已读 | Hybrid API含Requests/Errors/Assertions；endpoint延迟百分位、状态码、错误起点、自动发现脚本断言；断言可绑定门槛 | 断言计数和HTTP失败分开；Body证据取决于采样开启，不是全量 |
| L124 [/test-results/hybrid/browser](https://www.browserstack.com/docs/load-testing/test-results/hybrid/browser) | 200 / 正文已读 | Hybrid Browser只WebVitals/Tests，明确无Network；页面分阶段、用例失败与去重错误/堆栈 | TAP浏览器采集自实现，不能把Browser完整网络能力默认为Hybrid原生 |
| L125 [/test-results/hybrid/engine-health](https://www.browserstack.com/docs/load-testing/test-results/hybrid/engine-health) | 200 / 正文已读 | Hybrid引擎健康分API/Browser切换，CPU/内存80%和region过滤；健康推根因的因果断言过强 | 分别采资源和负载达成率，跨层关联仅列证据与假设 |
| L126 [/test-results/hybrid/errors](https://www.browserstack.com/docs/load-testing/test-results/hybrid/errors) | 200 / meta_redirect | 旧 hybrid errors 入口 HTML meta redirect，内容并入 APIs 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L127 [/test-results/hybrid/execution-logs](https://www.browserstack.com/docs/load-testing/test-results/hybrid/execution-logs) | 200 / 正文已读 | Hybrid日志分API generator与Browser VU session，成功/失败、region/time筛选、原始下载，默认失败采集 | 保留组件与节点身份，采样策略固定并脱敏 |
| L128 [/test-results/hybrid/summary](https://www.browserstack.com/docs/load-testing/test-results/hybrid/summary) | 200 / 正文已读 | Hybrid summary同时请求吞吐/延迟/错误与浏览器vitals；此页error%定义non-200，与其他页4xx/5xx不一致 | TAP冻结预期状态/传输错误/业务断言定义，禁止将合法201/204硬判失败 |
| L129 [/test-results/hybrid/tests](https://www.browserstack.com/docs/load-testing/test-results/hybrid/tests) | 200 / meta_redirect | 旧 hybrid tests 入口 HTML meta redirect，内容并入 Browser 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L130 [/test-results/protocol](https://www.browserstack.com/docs/load-testing/test-results/protocol) | 200 / 正文已读 | Protocol报告统一run控制、Summary/APIs Requests Errors Assertions/Logs/Engine Health | 协议报告与Browser/Hybrid保持同导航，组件数据独立 |
| L131 [/test-results/protocol/apis](https://www.browserstack.com/docs/load-testing/test-results/protocol/apis) | 200 / 正文已读 | Protocol APIs含Requests/Errors/Assertions，label趋势/百分位/状态码/错误首现及断言统计、response条件采样；label可能请求或流标签 | 请求与事务指标族不能混算，成功/失败样本口径固定，断言失败显式接入gate |
| L132 [/test-results/protocol/engine-health](https://www.browserstack.com/docs/load-testing/test-results/protocol/engine-health) | 200 / 正文已读 | Protocol引擎CPU/内存时间线、80%不健康、region筛选；健康不能充分证明服务端根因 | TAP发压能力及数据完成度为判定前置，跨层证据辅助定位 |
| L133 [/test-results/protocol/errors](https://www.browserstack.com/docs/load-testing/test-results/protocol/errors) | 200 / meta_redirect | 旧 protocol errors 入口 HTML meta redirect，内容并入 APIs 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L134 [/test-results/protocol/execution-logs](https://www.browserstack.com/docs/load-testing/test-results/protocol/execution-logs) | 200 / 正文已读 | Protocol日志题头为generator输出，但正文复用browser的VU session/locator失败描述；StoreAllLogs另页仅browser/hybrid | 保留正文冲突，不承诺协议也有相同session日志结构；TAP自定按shard输出 |
| L135 [/test-results/protocol/requests](https://www.browserstack.com/docs/load-testing/test-results/protocol/requests) | 200 / meta_redirect | 旧 protocol requests 入口 HTML meta redirect，内容并入 APIs 页；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L136 [/test-results/protocol/summary](https://www.browserstack.com/docs/load-testing/test-results/protocol/summary) | 200 / 正文已读 | Protocol summary展示尝试请求数、实际最大VU、RPS、错误/均值/百分位，图缩放重算所选窗，AI建议非结论 | TAP把目标/实际/完成业务/丢迭代并列，不用maxVU单项证明负载达成 |
| L137 [/test-results/protocol/tests](https://www.browserstack.com/docs/load-testing/test-results/protocol/tests) | 200 / meta_redirect | 旧 protocol tests 入口 HTML meta redirect，内容并入 APIs页Assertions；原页面 meta redirect 目标带重复 /docs/docs/，目标 HTTP 404 | 保留别名不重复计文章 |
| L138 [/trends](https://www.browserstack.com/docs/load-testing/trends) | 200 / 正文已读 | 同test跨run性能/引擎健康/活动趋势，一点一个run，本地时区；明确配置变化和partial run会误导，有无认证公链 | 配置可比分组、异常/未完整run显著标记、baseline审计，默认受权分享 |
| L139 [/troubleshooting](https://www.browserstack.com/docs/load-testing/troubleshooting) | 200 / 正文已读 | 框架路径/依赖/版本/env/证书等排错；k6先1VU5s；公网PyPI/Maven依赖限制、Gatling注入profile被覆盖；不可达私网建议与专页IP白名单不同 | 预检细分打包/变量/网络/依赖/引擎/数据错误；TAP私网agent专门设计，不盲目skipTLS |
| L140 [/configure-load-parameters/configure-multiple-scenarios/hybrid-cli](https://www.browserstack.com/docs/load-testing/configure-load-parameters/configure-multiple-scenarios/hybrid-cli) | 404 / failed | 正文多场景页链接到 hybrid-cli；HTTP404，无可读正文，不据此认定有独立CLI文档 | 仅保留已读多场景正文能力，CLI参数未证实部分标待核验 |
| L141 [/integrations/requestly/cli](https://www.browserstack.com/docs/load-testing/integrations/requestly/cli) | 404 / failed | 正文链接到旧 integrations/requestly/cli；HTTP404；有效正文位于 L101 recording-and-capture 路径 | 保留坏链，不把修正后URL当原链接成功 |
| L142 [/integrations/requestly/collection-execution](https://www.browserstack.com/docs/load-testing/integrations/requestly/collection-execution) | 404 / failed | 正文链接到旧 integrations/requestly/collection-execution；HTTP404；有效正文位于 L102 recording-and-capture 路径 | 保留坏链与独立有效路径关系 |
