# BrowserStack App / Web LCA 逐页分析与 TAP 设计映射

评审日期：2026-09-17。本文为公开文档研究，**不是产品账号实测，也不是已完成功能清单**。目标设计见 [RFC-011](../proposals/2026-09-17-rfc-011-rag-test-design-cross-platform-automation.md)：第 3.5 节是知识人工核对，第 5 节是 LCA 交互、选型与执行设计。

## 结论与阅读方式

TAP 自建低代码创作、步骤定义与脚本生成，沿用现有工作台；参考 BrowserStack 的完整流程，不假设其公开了创作引擎接口。Web 使用 Playwright，App 使用 Appium + WebdriverIO；静态步骤默认用于正式回归，动态 AI 块由用户明确启用。逐页记录包含**原始链接、正文能力与限制、TAP 对应设计**，不仅分析 overview。

| 核对范围 | 本次结果 |
| --- | --- |
| App LCA | 30 篇不同产品正文，全部已读；另读 3 篇直接相关支撑文档，记录 1 个正文死链 |
| Web LCA | 108 个产品 URL：104 篇不同正文全部已读，另 4 个跳转别名；另读 7 篇直接相关 FAQ |
| 发现方式 | 从首页导航逐页遍历正文链接，再与官方网站地图交叉核对；栏目索引和版本说明计入正文，但不冒充独立功能 |
| 视频 | [BrowserStack Low Code Automation — Sneak Peak](https://www.youtube.com/watch?v=oOuogmSO5yI)：网页只返回标题/页脚；浏览器触发 YouTube 访问验证，未取得可播放视频或字幕，**未完成内容分析，不作为已核实设计依据** |
| 范围边界 | 覆盖本次公开导航、正文链接和网站地图发现的产品文档集合；不声称覆盖未公开/未链接资料，也未展开其他 BrowserStack 产品的完整文档树 |

A01–A30 对应 App 文档；W001–W108 对应 Web URL；F001–F007 对应 Web 支撑 FAQ。P0/P1/P2 表示本评审的建议优先级，最终交付批次以 RFC-011 第 5.7 节为准；不代表所有 P0 已实现。所有产品限额、套餐和实验状态仅代表本次读取的公开页面，冲突原样保留并列出核验点。

| 设计落点 | 主要来源 |
| --- | --- |
| 工作台、连接、录制与调试（第 5.1、5.6 节） | A03/A05/A10/A17/A18；W024/W086/W092/W094/W095/W102/W103 |
| 数据、条件、循环、模块和失败策略（第 5.2 节） | A11–A16/A19–A21；W006/W011/W017–W020/W080–W085/W088/W093 |
| App 包、签名、设备、特殊能力（第 5.3 节） | A05–A08/A18/A25/A26/A30 |
| Web 专属与能力边界（第 5.4 节） | W002/W009/W023/W070/W071/W079/W090/W096–W100/W104–W108 |
| AI、定位修复与导出（第 5.5 节） | A09/A14；W003–W005/W072–W078 |
| 套件、CI、运行证据及 Insights（第 5.6、第 6 节） | A22–A29；W008/W012–W016/W021/W022/W025–W069 |

## App LCA：逐页分析

### 范围与覆盖证据

本次以官方 [App LCA 首页](https://www.browserstack.com/docs/app-lca)为起点，解析公开 HTML 中的侧栏导航数据及正文链接，递归遍历 `/docs/app-lca` 下所有去重页面；去掉片段及末尾斜线后归并，同时在清单保存原始地址。再下载 [robots.txt](https://www.browserstack.com/robots.txt) 指定的 [sitemap.xml](https://www.browserstack.com/sitemap.xml) 交叉清点，并用官方站内限定搜索补查。网站地图含 26 个 App LCA 页面，全部已在导航/正文集合中。侧栏还含 overview、manage-and-run-tests、app-reset、build-status-report 四页，说明只看网站地图也会遗漏。

- App LCA 产品页：30 篇（含首页和两篇栏目索引），均 HTTP 200，正文已逐篇读取；产品页请求没有重定向。
- 产品正文死链：1 个，`https://www.browserstack.com/docs/app-lca/create-tests/variables/index.md`，HTTP 404；不能当作已读文章，其所属 variables 正式页已读取。
- 直接相关的官方公共支撑页：3 篇，已逐篇读取；其中 `https://www.browserstack.com/local-testing/binary-params` 重定向到 `https://www.browserstack.com/docs/local-testing/binary-params`。这三篇仅补足 App LCA 正文明示的内网和服务账号设置，未扩大成其他产品全量评估。
- 合计 34 个去重原始请求、33 篇可读取正文、1 个失败、1 条重定向。没有遇到需要登录才能读取的产品文档正文；账户设置、产品控制台、联系销售和下载二进制为操作入口，未登录/执行。全站菜单中的 App Automate、Web LCA 等其他产品、第三方正则入门页、支持/社区/营销入口排除。

优先级：P0 为移动自动化首个可验收闭环；P1 为复杂业务与团队使用；P2 为特殊硬件/高级能力，均是 TAP 建议，不是已实现状态。

### 逐页证据与设计落点

| # | 官方标题与完整原始 URL | 本页具体正文证据（自行概括） | TAP 对应建议、差异与优先级 |
| --- | --- | --- | --- |
| A01 | [App Low Code Automation](https://www.browserstack.com/docs/app-lca) | 首页同时宣称自然语言、AI 定位、自愈与自动等待，并列出视频、网络及 Appium 日志；30,000+ 是整体云真机营销口径。 | P0：工作台串联创作、调试、运行和证据；设备数量不能直接作为 App LCA 实际可选矩阵，TAP 显示实时设备能力。 |
| A02 | [What is App Low Code Automation?](https://www.browserstack.com/docs/app-lca/overview/) | 正文标 Beta；云端真机写步骤，含视觉选择元素、变量、检查、项目/文件夹/标签/套件。 | P0：借鉴完整工作流；TAP 明示实际完成度，云端“零设置”不适用于自托管 Appium/macOS 机器。 |
| A03 | [Get started with App Low Code Automation](https://www.browserstack.com/docs/app-lca/get-started) | 选 iOS/Android 设备→上传 IPA/APK→命令实时作用设备→加变量和检查→执行到此/完整运行→保存→套件→报告。 | P0：按此补齐连续操作路径；TAP 另外加入需求/用例来源和发布检查，两个平台分别验证。 |
| A04 | [Create tests using App Low Code Automation](https://www.browserstack.com/docs/app-lca/create-tests) | 此页为创作索引，正文列出 App 配置、AI、命令、等待/失败逻辑、变量、模块、API、编辑、重置、循环、条件、数据集等 16 项入口。 | P0：创作区必须有统一插入步骤入口，不可只有录制/生成两个按钮。 |
| A05 | [Configure device type and device model](https://www.browserstack.com/docs/app-lca/create-tests/configure-device-and-model) | 同时列手机和平板，创作时一次只能用一台型号；要求 App 与设备系统兼容。 | P0：设备选择器含平台、型号、系统、占用/在线状态，编辑会话持有唯一设备；矩阵执行另设。 |
| A06 | [Upload apps in App Low Code Automation](https://www.browserstack.com/docs/app-lca/create-tests/upload-apps) | 原生 IPA/APK≤1GB，RN/Flutter/Ionic 为实验支持；包 30 天过期，引用过期包的计划运行暂停；只能删除本人上传包。 | P0：应用包管理独立于脚本，记录平台/版本/签名/指纹/到期及引用；TAP 自定保留政策，不照搬 30 天。 |
| A07 | [Image injection](https://www.browserstack.com/docs/app-lca/create-tests/image-injection) | JPG/JPEG/PNG≤10MB，媒体库最多10图；只向上传包的相机注图，不支持商店包、系统 App、WebView、HEIC或相册；多注入时最后一次生效。 | P2：媒体库及相机注图作为明确设备能力；不能把常规 Appium 点击等同图像注入，需专项适配和验证。 |
| A08 | [iOS app groups entitlement](https://www.browserstack.com/docs/app-lca/create-tests/ios-app-configurations) | 仅开发者证书签名包可保留 app groups；默认关闭，新上传构建不继承旧配置，已有包可修改开关。 | P0：包预检显示签名、配置及启动可用性；P1：原包与处理后包、签名配置逐版本保存，验证共享存储/扩展实际可用。 |
| A09 | [Generate test steps using AI](https://www.browserstack.com/docs/app-lca/create-tests/generate-ai-steps) | AI 为实验功能，回放每次重新解释提示；可停止、改提示及转静态，转静态不可逆；AI 提示不支持变量、提示级失败策略及 validation commands。 | P0：TAP AI 生成草稿，TAP 调试并发布固定步骤/WDIO脚本；动态 AI 仅保留为用户显式选择的独立步骤块，不能改变正式回归预期。 |
| A10 | [Write tests using natural language syntax](https://www.browserstack.com/docs/app-lca/create-tests/write-tests-using-natural-language-syntax) | 命令含点/双点/长按、输入/清除/按键、轮式选择、滑动/滚动、启动/终止、深链；精确标签匹配优先，也支持自然语言描述、accessibility ID/XPath；显式等待≤180秒。 | P0：统一动作库＋当前界面候选＋定位预览；平台专属参数不得藏在一句自然语言中；深链变量需编码。 |
| A11 | [Configure step behaviour](https://www.browserstack.com/docs/app-lca/create-tests/configure-step-behaviour/) | 自动等待结合录制时网络/画面变化与执行时可操作性；普通步骤超时≤240秒、AI≤300秒；失败可停、失败继续、警告继续，动作默认停而检查默认失败继续。 | P0：分清固定等待、等待条件与超时；保存失败策略，警告不得混成通过；编辑器展示默认策略。 |
| A12 | [Variables](https://www.browserstack.com/docs/app-lca/create-tests/variables) | 局部变量仅后续步骤可用，全局变量项目共享且只能在管理页创建；可从元素全文/正则、API、DB抽取，一步一个元素值；无正则匹配按步骤失败策略处理。 | P0：显示变量来源、类型、范围、运行值和缺失提示；P1：正则提取和全局引用影响检查，值与凭证分开。 |
| A13 | [Secrets](https://www.browserstack.com/docs/app-lca/create-tests/secrets) | 值加密存后端并遮罩；测试与模块引用同一 secret，修改影响全部引用，仍被引用则不可删；不能用于 AI 步骤。 | P0：凭证选择显示引用名，调试、导出、模型输入、视频和日志皆检查脱敏；凭证轮换与脚本版本解耦。 |
| A14 | [Modules](https://www.browserstack.com/docs/app-lca/create-tests/modules) | 选步骤组成模块、加号导入；修改传播所有引用，使用中不可删；外部局部变量不能入模块，模块产生变量可在后续用，全局可用；AI转静态影响所有调用者。 | P1：TAP 使用有输入输出和固定版本的公共步骤，显示受影响用例；升级由引用方确认，避免隐式全局变更。 |
| A15 | [Validations](https://www.browserstack.com/docs/app-lca/create-tests/validations) | 正文只明确页面文字/正则、元素文字/数字/正则、可见/启用/勾选等状态三类；导语提 visuals 不足以证明视觉差异比较。 | P0：单独编辑实际值/预期值/比较方式，缺失预期要求补充；不将该页当截图像素比对能力证据。 |
| A16 | [Call an API and extract values](https://www.browserstack.com/docs/app-lca/create-tests/configure-api) | API 步骤支持方法、URL、query、Basic/Bearer、headers、form/raw；执行后查状态/头/body，以 JSONPath 或 XML XPath 提取，每次运行更新变量。 | P1：执行器增加受项目环境限制的 API 步骤，用于数据准备与后端检查；这是“测试内调用API”，不是 BrowserStack 创作 API。 |
| A17 | [Edit a test](https://www.browserstack.com/docs/app-lca/create-tests/edit-a-test) | 编辑重开原创作设备会话；步骤和名称/描述/标签/文件夹可改，克隆复制步骤与配置，删除不可恢复。 | P0：分开草稿、发布版、克隆和元数据；TAP 沿用已有版本设计并补齐执行侧实现，不将直接编辑当版本历史。 |
| A18 | [App reset](https://www.browserstack.com/docs/app-lca/create-tests/app-reset) | 独立重置清数据并重启，编辑光标留原行；Run all 可选清空或沿用，重置失败停止完整运行并可重试。 | P0：把当前设备状态与步骤草稿区分，显式重置策略与失败状态；Android/iOS 清状态实现分别验证。 |
| A19 | [Repeat steps with loops](https://www.browserstack.com/docs/app-lca/create-tests/loops/) | Repeat 为固定1–100次，支持选中步骤包成循环及循环内模块；iteration_number 从1起；非整数/越上限运行失败。 | P1：先提供有界循环和迭代号，报告定位到第几轮，禁止生成不可控循环。 |
| A20 | [Record conditional flows with if-else](https://www.browserstack.com/docs/app-lca/create-tests/conditional-flows/) | If/Else if/Else支持页面、元素、变量、系统及phone/tablet条件；单条件块最多两项and/or；不允许条件嵌条件，与循环只可有限组合。 | P1：权限弹窗及Android/iOS差异用显式分支；嵌套结构实时校验，记录跳过原因；TAP可定义自己的清晰边界。 |
| A21 | [Data-driven testing](https://www.browserstack.com/docs/app-lca/create-tests/data-driven-testing/) | CSV≤100行×40列、单值≤1000字符、表头为变量；每测试只连一数据集；调试仅首行，云按选中行执行；数据库仅公开MySQL/PostgreSQL。 | P1：数据预览/行筛选/场景列与每行结果；TAP调试允许指定行并固定数据快照，私网取数从受控执行端实现。 |
| A22 | [Manage and run tests](https://www.browserstack.com/docs/app-lca/manage-and-run-tests) | 此页为管理索引，正文把套件组织执行和含日志/截图的结果检查作为两条路径。 | P0：TAP自动化内保留套件与运行入口，详报进入同一Test Insights运行详情，不造重复报告体系。 |
| A23 | [Test suites](https://www.browserstack.com/docs/app-lca/manage-and-run-tests/test-suites) | 套件选测试并排序或并行，分别选Android/iOS包与设备，按星期/时间调度；latest开关会被API显式app ID覆盖，另有完整更新日志。 | P0：Jenkins接固定套件、脚本/包/数据/环境/设备版本；P1：周期运行。提交时解析“最新”并保存具体版本，历史可重现。 |
| A24 | [Customize test execution with blocks](https://www.browserstack.com/docs/app-lca/manage-and-run-tests/block-wise-execution) | Pro起支持块顺序、块内串并行及失败后是否进入下块；至少2块每块1测试；一平台失败可跳过后续所有平台，正在执行的测试先完成。 | P1：把准备/主流程/清理依赖与跳过写清楚；TAP默认按设备隔离失败传播，跨平台阻断必须显式配置，不盲抄竞品。 |
| A25 | [Test on internal networks](https://www.browserstack.com/docs/app-lca/local-testing) | 套件开Local且运行Local binary，不支持Local桌面App；定时任务需机器持续在线，默认用group owner key；受限网络可force-local，IP白名单为Enterprise。 | P0：环境连通性预检与受控执行网络；P1：云设备隧道生命周期、健康状态和独立账号，不能把“本地测试”等同设备在本地。 |
| A26 | [Service accounts](https://www.browserstack.com/docs/app-lca/service-accounts) | Enterprise，仅Owner/Group Admin创建修改，App LCA仅用组织级账号；每项目一个，可用于所有Local构建或仅定时Local构建。 | P0：Jenkins/调试后台机器身份单独授权，按项目和动作审计；不依赖个人账号，App LCA细则优先于通用团队账号说明。 |
| A27 | [CI/CD integration for App Low Code Automation](https://www.browserstack.com/docs/app-lca/cicd-integrations/) | 仅列上传包、运行已有套件、查build状态三接口；Basic账号/access key；上传1次/分钟，其余各100次/分钟；免费试用不可调运行/状态API。 | P0：Jenkins提交与状态对账，限流退避、超时重查；TAP自建创作/调试接口，不能声称BrowserStack提供低代码编辑API。 |
| A28 | [View test results in App Low Code Automation](https://www.browserstack.com/docs/app-lca/test-debugging/builds/) | 按项目找build，可按状态/设备筛选和停止；展开设备看步骤通过/失败/跳过、错误、重试数及视频。 | P0：Insights按构建→设备→数据行→尝试→步骤定位，视频时间与日志截图关联；取消不等同立刻硬断所有设备。 |
| A29 | [Build status report](https://www.browserstack.com/docs/app-lca/test-debugging/build-status-report/) | 套件通知可选Passed/Failed/Skipped和收件人，包括外部邮箱；默认创建者；邮件带触发者、当地时间、总耗时、各结果计数和build链接。 | P1：订阅按状态配置、项目权限和通知审计；指标由ClickHouse/原始运行事实计算，通知链接回同一运行。 |
| A30 | [Test biometric authentication](https://www.browserstack.com/docs/app-lca/create-tests/biometric-authentication) | Pro功能；上传APK/IPA、Android6+/iOS13+，Android指纹/iOS Touch ID与Face ID；会话前开配置，认证时可模拟通过/失败/取消，Xiaomi/Vivo/Huawei不支持。 | P2：认证结果注入作为设备能力，禁止运行中临时打开；与真实物理生物特征安全性评估分开验收。 |

### 正文关联支撑页

| 官方标题与完整原始 URL | 已读正文细节 | TAP建议 |
| --- | --- | --- |
| [Inbound IP Whitelisting](https://www.browserstack.com/docs/local-testing/inbound-ip-whitelisting) | 需要网络资源在公网可解析，只按来源IP限制；不能借白名单访问localhost或非公开子网，后者须隧道。 | P1：把公网IP许可与私网隧道分成不同连接模式，连通性测试明确目标和路径。 |
| [Flags for Local binary](https://www.browserstack.com/local-testing/binary-params) → [当前地址](https://www.browserstack.com/docs/local-testing/binary-params) | include/exclude-hosts可限定隧道，exclude优先；force-local令only失效；local-identifier区分多连接；可设代理/证书/日志和守护运行。 | P1：后端配置白名单、隧道编号及脱敏诊断，取消/到期回收；不向用户暴露无关供应商参数。 |
| [Create and manage service accounts](https://www.browserstack.com/docs/iaam/settings-and-permissions/service-accounts) | 通用账号可组织或团队范围，不能登录控制台、不收邮件；Owner/Admin管理和轮换密钥；通用文档有recycle_key API。 | P0：机器身份与人身份分离；App LCA只允许组织级，不把通用文档团队范围泛化为App LCA功能。 |

### 按用户流程得到的完整功能面

1. **准备应用与设备。** 选择平台/型号/系统和手机/平板；上传包、选历史/最新版本；配置iOS签名能力、相机注图、生物认证；检查包到期和目标环境连通性。TAP需要包库存、设备库存和环境配置，不能只补一个移动端画面。[设备](https://www.browserstack.com/docs/app-lca/create-tests/configure-device-and-model)、[上传](https://www.browserstack.com/docs/app-lca/create-tests/upload-apps)、[iOS配置](https://www.browserstack.com/docs/app-lca/create-tests/ios-app-configurations)
2. **在真机上创作。** 已读文档的明确操作是命令输入、当前界面选择、实时执行和AI提示生成；文档用“record/recording”描述捕获动作及等待，但没有单独完整证明“任意真机触屏操作均自动录成步骤”的录制模式，更没有公开录制API。TAP仍可实现录制，但需作为自己的需求验证；点选元素必须展示候选、范围、定位方式及唯一性。[自然语言命令](https://www.browserstack.com/docs/app-lca/create-tests/write-tests-using-natural-language-syntax)
3. **AI与确定步骤协同。** 用户能看到生成状态、停止、修改提示并转换固定命令；停止后App可能处于未知状态。App LCA动态AI每次重跑重新决策，消耗用量；TAP按既定方向在创作时生成/修复，正式Jenkins默认使用固定版本脚本；如用户明确选择动态AI块，保留边界、预算、动作与证据，并且已确认的业务预期不得自动改写。首次生成要保存原提示、生成步骤、实际验证证据，不能依靠“AI已完成”发布。[AI生成](https://www.browserstack.com/docs/app-lca/create-tests/generate-ai-steps)
4. **动作、检查与等待。** 普通点击/输入以外，移动端还需手势范围/方向、长按时长、多列日期轮、生命周期和深链。页面文字、元素状态、文字/数字/正则检查独立存在；标签、accessibility ID、XPath和AI描述定位分开。自动等待与固定等待、超时、失败继续/警告继续/立刻停止有不同语义。视觉像素差异、OCR比对、地理位置/网络条件/推送/横竖屏控制在这套App LCA正文中未找到完整能力说明，不计作已确认竞品功能。[命令](https://www.browserstack.com/docs/app-lca/create-tests/write-tests-using-natural-language-syntax)、[检查](https://www.browserstack.com/docs/app-lca/create-tests/validations)、[步骤行为](https://www.browserstack.com/docs/app-lca/create-tests/configure-step-behaviour)
5. **数据和复用。** 局部/全局变量、密钥、元素文本提取、API/DB取值、正则筛选、CSV/数据库数据集、场景命名和按行结果；公共步骤模块与条件/循环组合，保存引用影响。公开文档没有可核验的App LCA脚本Git同步/导出、分支合并、版本回滚完整流程；克隆/编辑/套件更新日志/App包版本不是脚本版本管理。TAP需保留自有草稿→验证→发布版本，并固定公共模块版本。[变量](https://www.browserstack.com/docs/app-lca/create-tests/variables)、[模块](https://www.browserstack.com/docs/app-lca/create-tests/modules)、[数据集](https://www.browserstack.com/docs/app-lca/create-tests/data-driven-testing)、[编辑](https://www.browserstack.com/docs/app-lca/create-tests/edit-a-test)
6. **局部调试。** 运行至选中步骤、仅运行选中步骤和完整运行；部分运行前应由用户保证App状态。支持重置、失败重试。TAP需在运行前展示将使用的数据行、会话状态和依赖变量，显示“本次只验证这些步骤”，不能把局部通过当完整验收。[入门](https://www.browserstack.com/docs/app-lca/get-started)、[重置](https://www.browserstack.com/docs/app-lca/create-tests/app-reset)
7. **套件、依赖、调度和CI。** 测试排序/并行、Android/iOS包×设备、块依赖、失败传播、定时启动、latest策略、内网隧道、机器账号。TAP调试服务独立，正式运行交Jenkins；提交时保存应用、脚本、模块、数据、设备与环境快照，运行中不自动切最新。[套件](https://www.browserstack.com/docs/app-lca/manage-and-run-tests/test-suites)、[块执行](https://www.browserstack.com/docs/app-lca/manage-and-run-tests/block-wise-execution)、[CI](https://www.browserstack.com/docs/app-lca/cicd-integrations)
8. **排错、权限与反馈。** 构建/设备/步骤结果、视频、截图/网络/Appium日志、错误与重试；定向状态邮件；服务账号与secret引用权限。产品文档未完整定义项目角色对所有资产的读写权限，需TAP自己明确查看、创作、调试设备、发布、运行、凭证管理权限。ClickHouse保存事实和统计维度，对象存储保存视频/日志，TAP AI只在授权证据上解释原因。[报告](https://www.browserstack.com/docs/app-lca/test-debugging/builds)、[通知](https://www.browserstack.com/docs/app-lca/test-debugging/build-status-report)、[账号](https://www.browserstack.com/docs/app-lca/service-accounts)

### 平台限制、文档矛盾与需核验的接口

- **跨平台不能偷换概念。** 上传页说任一平台创作可跨两平台执行；套件页明确Android和iOS分别选对应包/设备。它不表示APK可安装iOS，也不能证明控件/权限/签名差异自动无损消除。TAP共享业务意图，但保留平台动作和定位差异及各自通过记录。
- **AI支持边界自相矛盾。** AI页前面描述可用prompt发起action/validation，末尾明确不支持validation commands；步骤行为页可配置AI步骤失败，但AI页说不支持“prompt level”软硬失败。应采用末尾明确限制，不夸大AI断言能力；与“静态步骤的失败策略”分开。[AI页](https://www.browserstack.com/docs/app-lca/create-tests/generate-ai-steps)
- **重置失败文案不同。** AI/语法页描述可取消重置后继续当前会话，专门App reset页说明失败停止Run all。应区分保留调试会话与继续完整运行，TAP按已配置起始状态未完成即不开始完整测试。[专门重置页](https://www.browserstack.com/docs/app-lca/create-tests/app-reset)
- **选择器/自愈证据有限。** 首页说AI self-healing，正文提供精确标签、AI描述及accessibility ID/XPath，但未列可公开调用的定位/自愈API，也未明确跨平台候选置信度及修复审核。TAP须实现自己的定位快照、候选验证与修改审批，不把营销语当可复用服务协议。
- **原生支持与设备限制。** 原生包为主要支持，混合框架实验；生物认证仅上传包、Android6+/iOS13+、不支持Xiaomi/Vivo/Huawei；注图也为Android6+/iOS13+且不支持WebView/相册；这都是BrowserStack配置/供应商能力，不是开源Appium自动带来的保证。[生物认证](https://www.browserstack.com/docs/app-lca/create-tests/biometric-authentication)、[注图](https://www.browserstack.com/docs/app-lca/create-tests/image-injection)
- **数据与控制流限制需在UI提前揭示。** 数据集、循环、条件嵌套和模块变量范围有明确限制；公共数据库限制不能以Local testing对App访问支持来推断已支持私网数据库连接。[数据集](https://www.browserstack.com/docs/app-lca/create-tests/data-driven-testing)、[条件](https://www.browserstack.com/docs/app-lca/create-tests/conditional-flows)、[模块](https://www.browserstack.com/docs/app-lca/create-tests/modules)

公开CI接口边界（仅文档核验，未使用真实凭证发起请求）：

| 接口 | 正文确认的用途 | 尚需契约/实测核验 |
| --- | --- | --- |
| `POST https://app-lcnc-api.browserstack.com/api/v1/mobile-apps/upload` | multipart单包≤1GB；返回appHashedId；iOS可传set_ios_app_groups_entitlement；1次/分钟。 | 去重/超时重复上传行为、包有效期如何查询、其他App配置参数、服务账号是否支持此入口。 |
| `POST https://app-lcnc-api.browserstack.com/api/v1/test-suite/{test_suite_id}/run` | 启动已有套件，android_app_id/ios_app_id覆盖配置，local_identifier区分隧道；返回build_id；100次/分钟。 | 未文档化幂等键、取消API、重试策略、数据覆盖、设备矩阵覆盖；提交超时不可直接盲目重发。 |
| `GET https://app-lcnc-api.browserstack.com/api/v1/builds/{build_id}/status` | 含build状态passed/failed/running/queued/stopped、汇总、测试执行ID、设备和耗时、报告URL；100次/分钟。 | 细粒度步骤/截图/日志/视频下载、原始失败/重试关系、Webhook、分页与保留期、Skipped计数；已有UI详报不等于公开证据API。 |

没有发现公开的App LCA测试创建/编辑/录制会话/定位/模块管理/脚本导出API。不能用“Call API步骤”证明这类接口存在，也不能拿App Automate公开接口自动套在App LCA上。App LCA作为参考产品无需成为TAP技术依赖。[官方CI文档](https://www.browserstack.com/docs/app-lca/cicd-integrations)

### A10/A11/A15 操作、检查及执行语义核对

下表补齐语法页内部小节，不另计文档页数；引用仍是 [A10 命令语法](https://www.browserstack.com/docs/app-lca/create-tests/write-tests-using-natural-language-syntax)、[A11 步骤行为](https://www.browserstack.com/docs/app-lca/create-tests/configure-step-behaviour)、[A15 检查](https://www.browserstack.com/docs/app-lca/create-tests/validations)。

| 正文小节 | 已核对细节 | TAP设计落点 |
| --- | --- | --- |
| 命令编辑与定位 | 输入时建议合法命令；Esc关闭建议；不匹配命令报错；精确标签、AI描述、accessibility ID/XPath有不同可靠性与速度 | 参数和定位结构化保存，命令显示只是编辑方式；不将“看起来像自然语言”当作任意语义均可执行 |
| 点按/双点/长按 | 点按须可见可操作目标；长按默认3秒且可指定时长 | 分开动作类型、目标和持续时间 |
| 键盘 | 向指定字段输入、对焦后输入、清空、按键分别存在 | 聚焦与输入、输入后键盘状态可见，密码使用引用 |
| Picker | 多列用分号按屏幕列顺序匹配；单列须独立无障碍标签；值数量、有效选项、设备语言均约束；只支持wheel/spinner，不含calendar/compact | 弹出列值编辑器，按设备实际控件选择动作；平台错误不改为无条件点击 |
| 滑动与滚动 | 滑动可作用整屏或元素；滚动支持区域、方向、目标，默认移动区域50% | 展示手势路径与目标区域，记录实际滚动后的界面 |
| 页面/元素检查 | 页面检查有/无文字或正则；元素分可见、存在、启用、选中、勾选、空等 | 存在不等于可见；元素查找失败与断言失败分开 |
| 文字与数字检查 | 含/不含、等/不等、开头、正则；显式number按数值，否则字符串比较 | 显示类型和实际值，数值解析失败不可当0 |
| 显式等待 | >0且≤180秒，至多一位小数 | 与元素等待超时分开，避免把二者视作同一设置 |
| 选中步骤执行 | 可跳过前几步，但要求App已处于正确状态 | 提示遗漏变量/依赖，局部通过单独记账 |
| 启动/终止/深链 | 深链必须有效URI；可加别名、插入变量并自动URL编码；终止后可重启 | 保存目标App和深链原模板/解析值，重启不等于清数据 |
| 超时与失败 | 命令最大240秒、AI300秒；警告继续默认最大15秒但显式超时保留；动作/AI默认失败停止，检查默认失败继续 | 策略默认值明确，业务失败与警告均保留事实；不能通过降为警告自动消除失败 |

补充A09/A14：模块内AI每次执行仍耗AI交互用量；在任一引用测试里修改提示并保存后，其他引用测试下次运行取新提示；转换静态既不可逆，又同步影响所有引用。TAP必须把“原产品即时共享修改”和“公共步骤固定版本、显式升级”的差异写明。[AI生成](https://www.browserstack.com/docs/app-lca/create-tests/generate-ai-steps)、[模块](https://www.browserstack.com/docs/app-lca/create-tests/modules)

## Web LCA：逐页分析

### 范围与完成度

本报告研究公开 Web Low Code Automation 文档及其直接引用的关键产品FAQ。以官方正文为准；TAP列为设计建议，不代表BrowserStack能力或TAP已有实现。

- 从首页公开HTML中的 `sidebar0` JSON完整取得96个导航URL（含分类目录页）。递归读取每页产品内正文链接，再用官方 [docs sitemap](https://www.browserstack.com/docs/sitemap.xml)交叉核对；sitemap有104个本产品URL，并补出导航未列的静态AI步骤生成页。
- 最终去重 **108个产品文档URL = 104个不同正文页（含产品landing、目录页、6篇版本说明）+4个meta-refresh别名**；另读7篇正文直接引用的LCA FAQ。不是“108篇独立功能文档”。这104个正文页均已逐页读取，不只是overview。
- 公开请求最终全部成功：0最终读取失败、0鉴权/公开不可达；代理页首次临时断连后重试成功。4个跳转是HTTP 200的meta-refresh，目标已读。未执行登录、商业后台、测试执行API或录制操作；正文功能没有经过账号实测。
- 未把全站产品导航、外链视频、产品营销页、与本产品无关的Automate/App Automate/Percy完整文档树计入；跨产品能力只根据本LCA页所述边界，不因此声称读遍BrowserStack全部产品或每篇Support FAQ。本文末引用Playwright官方资料用于技术边界，另不计入LCA页数。

### 逐页矩阵

P0为真实Web自动化闭环必需；P1为后续增强；P2为可选集成或专项能力。优先级是TAP建议，不是BrowserStack分级。

| ID | 标题与完整URL | 正文功能/限制 | TAP建议/差异 | 优先级 |
| --- | --- | --- | --- | --- |
| W001 | [Low Code Automation](https://www.browserstack.com/docs/low-code-automation) | 录制操作形成步骤，页面列出AI维护、云执行与视频/控制台/网络证据四组能力。 | 采用端到端创作—验证—执行—分析流程，不能把首页四组摘要代替详细能力设计。 | P0 |
| W002 | [Use browser flags in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/browser-flags) | 桌面可配置屏幕共享、相机麦克风、位置、剪贴板、无痕、拒绝权限、暗色、语言；移动可选项更少，改动下次运行生效。 | Web会话设置增加权限、语言和主题；显示浏览器适用性，受控参数白名单。 | P1 |
| W003 | [Export tests as code in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/export-tests) | Pro导出.side或Nightwatch；秘密只留名称，AI数据留提示词；自愈、视觉验证、API和Open Email不导出。 | 原生生成Playwright TypeScript，并生成导出能力清单；不可将BrowserStack导出直接当Playwright。 | P0 |
| W004 | [Bulk export tests as code](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/export-tests/bulk-export) | 批量脚本先调test-list和export，再用Selenium代码导出器转换六类语言；Windows需WSL/Git Bash。 | TAP批量导出固定版本脚本包、数据模板、依赖和运行说明；不额外承诺多语言。 | P1 |
| W005 | [Export tests](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/export-tests/get-tests-api) | 公开GET /api/v1/tests/{test_id}/export?format=side或nightwatch、GET /api/v1/test-list；列表含ID、名称、tags。 | 将读取/导出API与创作API分开；未见公开创建/更新步骤接口，不设计成直接写入LCA。 | P0 |
| W006 | [Global variables](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/global-variables) | 全局变量可查看使用它的测试/模块数量；编辑提示受影响数量，有引用时不能删除；按环境覆盖值。 | 变量修改展示影响列表；运行固定环境数据快照。 | P0 |
| W007 | [Advanced use cases](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/overview) | 高级能力目录列出数据库、隧道、位置、秘密、全局变量、下载、导出、跨测试数据与服务账号。 | 高级设置按数据、网络、扩展三组呈现，避免散在每个步骤。 | P1 |
| W008 | [Advanced reporting and analytics](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/reporting-analytics) | 与Test Reporting & Analytics的集成仍beta；未手动映射时首跑后在Test Management创建1:1用例，录制可对应多个用例。 | ClickHouse Insights接真实执行事实；映射必须可核对，不能自动创建重复业务用例。 | P0 |
| W009 | [Run database query, add validations, and extract values](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/run-database-query) | MySQL/PostgreSQL/MS SQL/Oracle查询、断言与提值；结果最多100行40列，不内置阻止SQL写入；内网云执行需无identifier隧道。 | 数据库动作走TAP受控执行器及只读账号默认策略；界面区分查询截断和完整结果。 | P1 |
| W010 | [Service accounts for Low Code Automation](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/service-accounts) | Enterprise服务账号仅Owner/Admin可配置，每项目一个，可限定全部build或仅scheduled build的本地隧道凭证。 | Jenkins使用项目服务身份，运行与凭证责任明确，不依赖个人账号。 | P0 |
| W011 | [Share test data](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/share-test-data) | 父测试动态变量供同套件子测试使用；本地用手工默认值，云端须父先子后；被引用父测试不能删。 | 显式展示依赖和调试替代值；缺少真实上游输出不得视为正式验证。 | P1 |
| W012 | [Test Case Management Integration](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/test-management-integration) | 测试可关联BrowserStack TCM、TestRail、Azure Test Plan，并提供报告API；总览称Pro。 | TAP AI用例与TAP脚本双向关联，外部用例映射独立适配。 | P0 |
| W013 | [Azure Test Plan](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/test-management-integration/azure) | Azure Pro可OAuth/PAT连接并自动同步；Essentials手填case IDs，下载XML经Azure流水线发布。 | 区分自动同步与手工导入；记录外部项目、case ID与同步结果。 | P2 |
| W014 | [Reports API](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/test-management-integration/reports-api) | 公开GET /api/v1/builds/{build-id}/report?type=browserstack/testrail/azure/standard返回XML，含环境、设备、场景、失败和跳过。 | Insights同时接JUnit及扩展运行清单，去重按运行/测试/数据行/尝试编号。 | P0 |
| W015 | [BrowserStack Test Management](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/test-management-integration/tcm) | 步骤或步骤组映射到用例，边界绑定步骤本身而非易漂移的序号；新增步骤后边界随之移动。 | 使用稳定步骤ID关联用例和断言，保留多对多关系。 | P0 |
| W016 | [Integrate TestRail with Low Code Automation](https://www.browserstack.com/docs/low-code-automation/advanced-use-cases/test-management-integration/test-rail) | TestRail Pro自动上传，Essentials通过XML/trcli；同测试只映射一个项目/套件，重跑和中断/不完整build不自动同步。 | 外部同步需要重跑更新与未完成状态，不把未上报当通过。 | P2 |
| W017 | [Data driven testing](https://www.browserstack.com/docs/low-code-automation/best-practices/data-driven-testing) | CSV或数据库数据集；CSV最多100×100、单值1000字；一个测试一个数据集，本地只跑所选首行，云逐行；AND/OR不能混用。 | 数据预览、按行筛选、场景名称、失败行重跑；TAP调试允许明确选行并保存快照。 | P0 |
| W018 | [Generate dynamic data for every test run](https://www.browserstack.com/docs/low-code-automation/best-practices/functions) | 函数生成定长字母、数字、指定域名邮箱、密码；也可提示词生成数据。 | 内置确定的数据函数优先，随机种子与实际输入记录便于复现。 | P0 |
| W019 | [Modules](https://www.browserstack.com/docs/low-code-automation/best-practices/modules) | 连续步骤成模块、实例覆盖静态模块变量、动态提值不能覆盖；模块不可嵌套，有独立版本/恢复且自动更新所有引用。 | 固定模块版本、参数输入输出及影响预览；升级显式确认，避免改一次静默变全套。 | P0 |
| W020 | [Variables](https://www.browserstack.com/docs/low-code-automation/best-practices/variables) | 变量来自静态输入、UI全文/正则提取、JS字符串或API结果，可在步骤输入与文本验证复用。 | 变量面板区分来源、范围、类型和值预览，运行时缺值标原因。 | P0 |
| W021 | [CI/CD integration using REST API](https://www.browserstack.com/docs/low-code-automation/cicd-integrations/rest-api) | CI通过已建套件ID、账号密钥触发与查状态；列举七种CI且支持其他CI使用同REST API。 | Jenkins负责正式执行，TAP AI不直接持执行状态；TAP统一运行入口。 | P0 |
| W022 | [Execute tests](https://www.browserstack.com/docs/low-code-automation/cicd-integrations/run-tests-api) | POST套件run返回build_id；可覆base_url、enableFullUrl、environment、local_identifier；GET build status返回执行列表及public/private URL。 | 设计TAP提交/查询/取消/回调幂等协议；BrowserStack本文不证明取消或创作API存在。 | P0 |
| W023 | [Geo Region Restriction (GRR) for Low Code Automation](https://www.browserstack.com/docs/low-code-automation/geo-region-restrictions) | GRR支持欧洲/澳洲/印度；设备与核心数据受区域限制，AI/第三方不全覆盖；各区视觉、AI、无障碍可用性不同，API域名不同。 | 项目记录数据与执行区域，AI外发边界单独配置，不把区域托管等同所有链路驻留。 | P1 |
| W024 | [Create and run your first test](https://www.browserstack.com/docs/low-code-automation/get-started/create-test) | 当前为Web管理端+Mac Intel/Apple Silicon、Windows桌面App；需要Chrome；录制和本地回放每次干净实例，本地结果独立于云端。 | 托管录制及Electron本地连接器采用统一会话协议；调试独立于Jenkins正式结果，不写成仅浏览器扩展。 | P0 |
| W025 | [Low Code Automation Integrations](https://www.browserstack.com/docs/low-code-automation/integrations) | 集成目录分缺陷、CI、通知、测试管理，入口指向各具体连接方式。 | 在TAP统一连接管理及权限/连通状态，按业务需接入。 | P1 |
| W026 | [Azure Pipelines](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/azure-pipelines) | Azure Pipelines示例POST触发并每30秒轮询状态，以失败退出码影响流水线。 | 非首批CI；若复制示例需修跨step变量传播与总超时，不当生产模板直接用。 | P2 |
| W027 | [Bitbucket Pipelines](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/bitbucket-pipelines) | Bitbucket示例用build_id.txt工件跨step传build ID，再30秒轮询。 | 可复用正式运行协议；当前仅保留Jenkins主路径。 | P2 |
| W028 | [CircleCI](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/circle-ci) | CircleCI示例分触发和查状态两个run步骤，用curl/jq获取结果。 | 非首批；接入时必须补跨step状态持久化、取消和超时。 | P2 |
| W029 | [Github Actions](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/github-actions) | GitHub Actions普通/Local两种模板，Local用运行ID构造identifier并传API；示例包含残缺BUILD_ID和旧set-output。 | 只能参考接口与隧道关联，不能声称模板复制即运行；Jenkins先落实。 | P2 |
| W030 | [Gitlab CI/CD](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/gitlab-ci-cd) | GitLab同job中触发后30秒轮询passed/failed，失败exit 1。 | 后续CI适配共享TAP执行协议，补错误/超时状态。 | P2 |
| W031 | [Jenkins](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/jenkins) | Jenkins需pipeline-utility-steps，Groovy POST套件、readJSON解析build_id、每30秒查终态。 | 作为正式执行集成样式；TAP实际执行Playwright包并上传证据，增加队列、超时、取消和幂等。 | P0 |
| W032 | [Travis CI](https://www.browserstack.com/docs/low-code-automation/integrations/ci-cd/travis-ci) | Travis示例在Test stage以curl/jq触发与轮询，只覆盖passed/failed。 | 不扩首批CI范围，保留未来适配点。 | P2 |
| W033 | [Communication tools](https://www.browserstack.com/docs/low-code-automation/integrations/communication-tools) | 通信集成目录当前指向Slack，接收build完成通知。 | 通知由TAP配置和发送，TAP AI只提供分析内容。 | P1 |
| W034 | [Setup Slack Notifications for Build Summary](https://www.browserstack.com/docs/low-code-automation/integrations/communication-tools/slack) | Slack可每次/失败/状态变化通知，多频道；私有频道需加BrowserStack app；断开会永久删各套件通知设置。 | 订阅失败或状态变化为默认建议，断开显示影响范围并保留可恢复配置。 | P1 |
| W035 | [Design apps](https://www.browserstack.com/docs/low-code-automation/integrations/design-apps) | 设计应用目录用设计稿作为视觉基准，目前指向Figma。 | 设计稿对比作为独立可选能力，不与功能通过混算。 | P2 |
| W036 | [Integrate with Figma](https://www.browserstack.com/docs/low-code-automation/integrations/design-apps/figma) | Ultimate Figma需先成功产生视觉报告；需Percy访问文件；每build最多50设计，映射snapshot后生成已批准基线需1–5分钟。 | 设计基准显式版本化，未就绪不得用旧基线伪装新设计验证。 | P2 |
| W037 | [Issue trackers](https://www.browserstack.com/docs/low-code-automation/issue-tracker) | 缺陷入口支持Jira/Azure DevOps/GitHub，可在LCA报告页直接报问题。 | Insights失败详情可创建或关联缺陷，提交前可审阅证据和描述。 | P1 |
| W038 | [Integrate Low Code Automation with Azure DevOps](https://www.browserstack.com/docs/low-code-automation/issue-tracker/azure-devops) | Azure DevOps OAuth连接、动态加载工作项字段，可创建或更新issue并自动附步骤/环境/重试/超时/错误元数据。 | 通过缺陷适配器附原始运行引用，支持字段映射。 | P2 |
| W039 | [Integrate Low Code Automation with GitHub](https://www.browserstack.com/docs/low-code-automation/issue-tracker/github) | GitHub OAuth连接后选组织/仓库/项目及负责人，问题关联测试run，支持更新已有issue。 | 优先复用既有缺陷系统，避免失败自动刷大量新issue。 | P2 |
| W040 | [Integrate Low Code Automation with Issue Trackers](https://www.browserstack.com/docs/low-code-automation/issue-tracker/jira) | Jira Cloud支持OAuth或token，自托管私网需integrations-repeater隧道；创建/更新含失败步骤与运行链接。 | 对接内网缺陷系统单独验证网络路径；创建缺陷不等于测试已修复。 | P1 |
| W041 | [Test on internal networks](https://www.browserstack.com/docs/low-code-automation/local-testing) | 私网网页云执行需BrowserStack Local binary而非Local Desktop；每套件一个隧道；定时需在线机器+owner或服务账号；支持force-local。 | TAP本地连接器与Jenkins执行节点分别验证可达性，并展示隧道归属/心跳，勿推导API步骤也能私网。 | P0 |
| W042 | [Customize test execution with blocks](https://www.browserstack.com/docs/low-code-automation/managing-tests/block-wise-execution) | 采用blocks后所有测试都须在block内；块内顺序/并行，失败可继续或跳过后续块。 | 套件支持依赖分组，明确blocked/skipped与failed，显示并行执行顺序。 | P1 |
| W043 | [Configure environment for test suites](https://www.browserstack.com/docs/low-code-automation/managing-tests/configure-environment) | 环境覆域名/端口并保留path/query/fragment，或完整URL替换；Pro环境覆变量/secret；被套件引用环境不能删。 | 提交前预览实际URL和环境变量来源，跨域请求不能盲目统一替换。 | P0 |
| W044 | [Copy entities](https://www.browserstack.com/docs/low-code-automation/managing-tests/copy-entities) | 测试最多50批量复制，带依赖去重；套件只能同项目复制且共享测试不克隆；历史/用例映射不复制。 | 复制向导明确新建、共享、覆盖与不复制项，跨项目秘密不可默默搬运。 | P1 |
| W045 | [Organize and manage your tests](https://www.browserstack.com/docs/low-code-automation/managing-tests/organize-tests) | 以标签分类与文件夹层级组织测试，两者独立。 | 沿用TAP项目导航增加搜索筛选，避免另建测试管理副本。 | P1 |
| W046 | [Manage and run tests](https://www.browserstack.com/docs/low-code-automation/managing-tests/overview) | 管理目录聚合项目、标签、套件、重跑、并行、跨浏览器/移动浏览器、版本。 | 脚本库与执行套件区分，正式报告统一进Insights。 | P0 |
| W047 | [Rerun failed tests in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/managing-tests/rerun-failed-tests) | 自动重跑一次主要用于scheduled；手动仅最新失败build且无后续成功，可选失败/跳过；原build改最终状态仍留旧结果；A11y不支持。 | TAP保留首次结果与每次attempt，统计首跑通过/重试后通过分开，数据变更禁止冒充同一次重跑。 | P0 |
| W048 | [Manage tests with Low Code Automation](https://www.browserstack.com/docs/low-code-automation/managing-tests/test-suite) | 浏览器可固定版本；套件可多日历/间隔schedule、隧道、环境、位置；有并行额度时默认并行，可关为顺序。 | 套件矩阵和定时委托Jenkins；一次运行固化脚本/模块/环境/数据/浏览器版本。 | P0 |
| W049 | [Add tags to the tests](https://www.browserstack.com/docs/low-code-automation/managing-tests/test-tags) | 录制设置或列表可批量加标签，标签唯一，可修改/删除用于筛选。 | 优先级、业务域、端、标签与用例复用，避免仅自由文本分类。 | P1 |
| W050 | [Test version history](https://www.browserstack.com/docs/low-code-automation/managing-tests/test-versioning) | 每次改名称/步骤/变量/高级选项生成版本；恢复另建版本；正文仍说module不随测试回滚、需联系支持，与modules页冲突。 | 测试版本必须锁模块依赖，恢复完整发布快照并验证；保留官方冲突待核验。 | P0 |
| W051 | [What is Low Code Automation?](https://www.browserstack.com/docs/low-code-automation/overview/introduction) | 面向手工QA、开发和业务测试者，录制、断言、JS、AI维护、数据/模块、跨浏览器执行构成产品闭环。 | 用普通中文步骤服务非代码用户，代码与证据可展开；AI仍需真实验证。 | P0 |
| W052 | [Best practices for recording tests on Low Code Automation](https://www.browserstack.com/docs/low-code-automation/recommended-guidelines) | 建议稳定data-*等属性、固定A/B状态、关键节点断言；500–600步大测试可能不稳，宜拆单一流程和模块。 | 录制后提示无断言/冗余步骤/超长流程，但不自动删业务检查；测试环境防护配置由环境owner掌握。 | P0 |
| W053 | [Low Code Automation desktop app release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes) | 桌面版本目录列六个版本，最新v3.51.0(2026-09-01)，而非全历史档案。 | 连接器、浏览器、执行包兼容矩阵纳入运行证据。 | P1 |
| W054 | [Desktop app v3.45.1 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-45-1) | 3.45.1(06-11)修严格CSP自定义JS、iframe匹配及Safari导航中的脚本执行。 | 验收覆盖CSP、iframe、脚本期间导航，防止只测简单DOM。 | P1 |
| W055 | [Desktop app v3.47.0 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-47-0) | 3.47.0(07-06)新增域级请求头、Figma，修Safari脚本返回/iframe截图及证书可达问题。 | 请求头设域范围、脚本返回和导航分状态，代理/证书错误独立诊断。 | P1 |
| W056 | [Desktop app v3.48.4 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-48-4) | 3.48.4(07-15)宣称AI-assisted步骤可用secret/变量、TOTP、Safari/Firefox自愈及tab crashed原因。 | 核验AI secret与W075冲突；TAP秘密不进入模型，浏览器崩溃单独标记。 | P0 |
| W057 | [Desktop app v3.49.1 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-49-1) | 3.49.1(08-05)新增canvas录制回放，改善慢页等待，修Salesforce Firefox登录和Lightning断言。 | Canvas作为Web扩展路线；验收实际复杂控件，不能由普通DOM通过推及全部应用。 | P1 |
| W058 | [Desktop app v3.50.0 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-50-0) | 3.50.0(08-19)失败原因区分WAF/访问墙/存在但不可见，修iframe重写、拦截粘贴、跨标签续录和嵌套滚动。 | 失败分类指向可操作原因，调试聚焦当前标签/iframe，加入代表性验收用例。 | P0 |
| W059 | [Desktop app v3.51.0 release notes](https://www.browserstack.com/docs/low-code-automation/releases-and-downloads/release-notes/v3-51-0) | 3.51.0(09-01)企业代理菜单、私网视觉测试，隧道未连快速失败，API Local失败提示改进。 | 连接检查覆盖代理/隧道/截图服务；该变更未足够证明W081私网API限制已解除。 | P0 |
| W060 | [Role-Based Access Control (RBAC) in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/role-based-access-control) | IAM Owner/Admin/User与产品RBAC Admin/User/Viewer分开；licensed Member与只读Guest；Enterprise自定义role并可项目override。 | TAP权限细分读、编辑、发布、执行、凭证、分享；TAP AI与TAP校验相同项目边界。 | P0 |
| W061 | [Build Status Report](https://www.browserstack.com/docs/low-code-automation/test-debugging/build-email) | 邮件可每次/失败/状态变化，支持组织外收件人；个人关闭优先；通知含触发者/时区/结果/时长，Slack等价。 | 运行订阅明确收件范围，链接经权限校验，通知失败不改变测试结果。 | P1 |
| W062 | [Debug tests using network and console logs](https://www.browserstack.com/docs/low-code-automation/test-debugging/debugging-logs) | 网络HAR含请求/响应/时延可下载；Console日志仅Chrome/Edge。 | Playwright trace+HAR+console关联步骤，按浏览器能力显示缺失而非空白成功。 | P0 |
| W063 | [Test reporting and debugging](https://www.browserstack.com/docs/low-code-automation/test-debugging/overview) | 报告目录聚合通知、视频、截图、日志、分享链接。 | Insights以单次运行证据包为入口，跨到趋势/失败分析。 | P0 |
| W064 | [Instant failure analysis with Screenshots](https://www.browserstack.com/docs/low-code-automation/test-debugging/screenshots) | 本地/云关键步自动截图；元素未找到可比首个成功build基线与失败全页图，beta限选定客户。 | 调试显示原/现元素、页面与截图差异，不能只给AI摘要。 | P0 |
| W065 | [Share builds using public or private URLs](https://www.browserstack.com/docs/low-code-automation/test-debugging/share-build-via-url) | public链接默认开启，Owner/Admin项目级控制；匿名只读者可下载视频/网络/JUnit；private只组织内。 | TAP默认项目授权链接，公开分享需明确开启及有效期，敏感证据独立控制。 | P0 |
| W066 | [Debug tests faster with Video Recording](https://www.browserstack.com/docs/low-code-automation/test-debugging/video-recording) | 每次云执行默认视频，动作元素高亮，可下载、变速；正文不承诺本地视频。 | 正式执行视频+步骤时间轴；调试视频可选且明确工件是否齐全。 | P0 |
| W067 | [Redirecting…](https://www.browserstack.com/docs/low-code-automation/test-execution/cloud-run) | 旧cloud-run是meta-refresh，指向get-started/create-test#running-on-cloud，无独立正文。 | 按W024分析，不重复计篇数。 | 映射 |
| W068 | [Redirecting…](https://www.browserstack.com/docs/low-code-automation/test-execution/local-run) | 旧local-run重定向get-started/create-test，无独立正文。 | 按W024独立调试能力分析。 | 映射 |
| W069 | [Parallel Testing](https://www.browserstack.com/docs/low-code-automation/test-execution/parallel-test) | Pro并行额度组织级，可分给团队；无额度顺序，有N槽则N并行。 | Jenkins容量/项目配额/排队状态可见，调试资源与正式执行隔离。 | P0 |
| W070 | [Use IP Geolocation, language & locale](https://www.browserstack.com/docs/low-code-automation/test-real-user-conditions/ip-geo-location) | Enterprise IP地理位置覆盖60+国家/30+州；录制浏览器locale在本地/云回放保持。 | 地区、语言、时区各自配置；浏览器geolocation不等同出口IP所在地。 | P1 |
| W071 | [Set advanced test configurations](https://www.browserstack.com/docs/low-code-automation/test-recording/advanced-test-configurations/advanced-options) | Basic Auth免费，headers/cookies/localStorage付费；请求头最多4个，默认所有请求、可含/排除域最多4；cookie Max-Age≤400天。 | 会话初始化面板采用域范围与secret引用，显示将发送凭证的目标域。 | P0 |
| W072 | [AI-powered testing](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai) | AI能力目录区分生成步骤、整用例、自愈、agentic，不是同一执行机制。 | UI清楚标记静态生成和每次运行的动态AI块。 | P0 |
| W073 | [Agentic testing in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/agentic-testing) | agentic v2 Alpha限时试用Pro/Ultimate；需求/URL探索/已有用例/TCM输入，先评审再并行自动化+回放修复，可手接管且保存会话。 | TAP AI负责生成/澄清，TAP提供真实页面会话与受限调试；关键断言不可放宽，session可恢复。 | P1 |
| W074 | [AI-powered automated test generation](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-automated-tests) | 旧整用例生成Beta Pro+，填URL、前置、步骤、预期，可私网；支持视觉/文本/存在、type/click/hover六类动作。 | 生成前分离前置与预期，能力不支持时明确让用户补录。 | P0 |
| W075 | [Automate dynamic workflows with AI steps](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-interactions) | 自然语言动态步骤每次执行重新用AI；可导入变量，本文禁secret/代码/网络控制台DOM等非UI验证；每测试20步、每人每月500执行。 | 默认确定脚本；显式AI块显示成本/预算/真实子步骤，预期由用户控制，secret冲突待核验。 | P1 |
| W076 | [Self healing in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-self-heal) | 先规则属性优先，再AI找近似元素并给理由；不修网络/浏览器/崩溃，正文承认可能掩盖真实问题。 | 定位修复建议并列前后证据，经确认新版本+复跑，不自动改关键断言。 | P0 |
| W077 | [AI-powered test data generation](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-test-data-generation) | AI数据示例含本地化手机号/地址、指定格式密码和范围数字，在输入步骤生成后保存。 | AI测试数据先校验格式和业务范围，记录实际值与来源。 | P1 |
| W078 | [AI-powered test step generation in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/browserstack-ai/ai-test-step-generation) | sitemap额外页：录制器自然语言实时生成具体步骤，TCM也可送URL/步骤至LCA；不等于W075每次动态执行。 | 静态AI创作与动态AI执行在步骤类型和发布说明上分开。 | P0 |
| W079 | [Canvas](https://www.browserstack.com/docs/low-code-automation/test-recording/canvas) | Canvas用区域截图/Percy比较、OCR文本断言和提值，DOM自定义定位器不可用。 | Canvas专用选区与OCR证据；不得承诺Playwright定位器直接覆盖画布内部对象。 | P1 |
| W080 | [Record Conditional flows with If-else](https://www.browserstack.com/docs/low-code-automation/test-recording/conditional-flow) | If/ElseIf/Else条件支持元素存在、文本和变量，不支持嵌套；条件不满足不影响总结果，条件求值错误则停。 | 可视化分支、未走路径标明未覆盖，求值错误与分支false分开。 | P1 |
| W081 | [Add validations or extract values from API responses](https://www.browserstack.com/docs/low-code-automation/test-recording/configure-api) | API步骤支持GET/POST/PUT/DELETE/PATCH、Basic/Bearer、JSON/XML、JSONPath/XPath；单步验证或提值二选一；正文禁云端localhost/内网API。 | TAP用执行节点API客户端处理私网与数据准备；标HTTP证据，避免把页面JS fetch当通用API执行。 | P1 |
| W082 | [Configure step behaviour](https://www.browserstack.com/docs/low-code-automation/test-recording/configure-step-behaviour) | CSS/XPath可覆定位，iframe用<<、shadow用双竖线且不支持shadow XPath；步骤≤240秒；支持失败继续/警告继续/立即停。 | Web定位上下文树、自动等待/超时优先级及失败策略，业务必需断言不得默认降为警告。 | P0 |
| W083 | [Organize and structure your tests using Folders](https://www.browserstack.com/docs/low-code-automation/test-recording/create-folders) | 文件夹可嵌套，测试/文件夹可批量移动，编辑名称和描述。 | 脚本库支持文件夹且用稳定ID引用，移动不破坏执行链接。 | P1 |
| W084 | [Configure custom actions using Javascript](https://www.browserstack.com/docs/low-code-automation/test-recording/custom-actions) | JS在网页上下文执行；动作、返回字符串变量、返回布尔断言三种；每步可选择传一个元素，并导入各范围变量。 | 区分页面JS与Node/Playwright扩展，审核可用能力并保留脚本/错误证据。 | P1 |
| W085 | [Custom JavaScript library scripts](https://www.browserstack.com/docs/low-code-automation/test-recording/custom-actions/js-library) | JS片段库可试执行后保存；导入后修改不传播其他测试，内置片段不能删除。 | 代码片段复制与可升级共享模块分开，不暗示二者同一种复用。 | P1 |
| W086 | [Handle similar elements in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/element-selection) | 同类元素按文本/位置/父容器/属性选择，匹配数和唯一性反馈；管理员设URL范围可靠/不可靠属性，步骤配置覆盖项目。 | 元素选择器显示候选高亮、数量与定位理由，优先role/label/testid，可查看父容器及动态属性风险。 | P0 |
| W087 | [Redirecting…](https://www.browserstack.com/docs/low-code-automation/test-recording/get-started) | 旧get-started为meta-refresh至W024，无独立产品正文。 | 按W024统计与引用。 | 映射 |
| W088 | [Repeat steps with loops](https://www.browserstack.com/docs/low-code-automation/test-recording/loops) | 计数/直到存在/直到文本三类循环，最多30次，不允许循环嵌套或间接嵌套于模块/分支。 | 明确最大次数/超时/停止原因，变量计数必须受限。 | P1 |
| W089 | [Create tests using Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/overview) | 创作目录包含录制、定位、断言、数据、模块、配置、API、控制流、JS、多标签、编辑、邮件。 | 工作台按记录动作、检查结果、数据、流程、扩展统一步骤库。 | P0 |
| W090 | [Configure device profile and resolution](https://www.browserstack.com/docs/low-code-automation/test-recording/profile-and-resolution) | 桌面/手机/平板与横竖屏；录制分辨率受当前显示器限制，可记录每步resize并回放。 | 托管浏览器可独立于用户屏幕固定viewport；区分设备仿真与真机。 | P0 |
| W091 | [Projects](https://www.browserstack.com/docs/low-code-automation/test-recording/projects) | 团队可多项目，默认项目对所有用户；测试/套件/build属项目，正文称变量/secret/module/data/tag项目隔离。 | 项目授权与资产范围明确，注意tags与复制页group级说法冲突。 | P0 |
| W092 | [Supported actions in the recorder](https://www.browserstack.com/docs/low-code-automation/test-recording/record-actions) | 点击/输入/hover/拖拽/alert/confirm/iframe/shadow/导航多窗口/富文本/canvas；上传≤25MB不支持单步多文件/拖上传；prompt不支持；shadow内iframe不捕获。 | Web操作上下文完整录制，工具栏/下载/弹窗分类型；以真实兼容矩阵约束录制与代码导出。 | P0 |
| W093 | [Manage Secrets](https://www.browserstack.com/docs/low-code-automation/test-recording/secret-management) | secret加密保存、值掩码、使用处追踪；被引用不能删，环境可覆值，API步骤可用。 | 秘密仅运行时注入，脚本/日志/模型输入脱敏，权限按项目与环境管控。 | P0 |
| W094 | [Smart multi-tab handling](https://www.browserstack.com/docs/low-code-automation/test-recording/smart-tab-handling) | 标签匹配URL/title/query/path，不依赖打开顺序；可将修改批量应用相似步骤，人工tab规则优先默认。 | 标签/弹窗映射树和预期匹配预览，创建事件与稳定page ID关联。 | P0 |
| W095 | [Editing a test](https://www.browserstack.com/docs/low-code-automation/test-recording/test-editing) | 不启动录制可改URL/超时/定位/失败/重排/模块；新增步骤需录制，Execute till here到指定现场；首Open URL不能移。 | 独立调试支持单步、到此处、断点、补录，变量依赖和上下文校验后再保存。 | P0 |
| W096 | [Test email workflows in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/test-recording/test-email-workflows) | 提供永久/每次临时邮箱，打开邮件新标签；按sender/receiver/subject筛选，OTP全文/regex提取与链接内容验证。 | 邮件适配器与隔离测试收件箱，按本次运行相关ID取邮件，避免旧OTP误匹配。 | P1 |
| W097 | [TOTP authenticator](https://www.browserstack.com/docs/low-code-automation/test-recording/totp-authenticator) | TOTP secret可Base32或QR导入，SHA-1/256/512，验证六位倒计时；每次录制回放生成新码。 | 测试账号TOTP凭证由TAP secret保管，调试显示过期/时钟偏差而不暴露密钥。 | P1 |
| W098 | [Redirecting…](https://www.browserstack.com/docs/low-code-automation/test-recording/upload-files) | upload-files跳转W092#file-upload-action，无独立正文。 | 按上传正文限制核验。 | 映射 |
| W099 | [Validate downloaded file and its content](https://www.browserstack.com/docs/low-code-automation/test-recording/validate-file-download) | 下载自动记录名称/扩展/大小/静态MD5；PDF/CSV可打开做内容验证；≤25MB，单动作多文件不支持，真机移动不支持，blob新tab和Safari预览有限。 | 下载工件与业务内容断言分离，PDF/CSV解析为可审阅值，不以hash替代动态内容验证。 | P1 |
| W100 | [Adding validations](https://www.browserstack.com/docs/low-code-automation/test-recording/validations) | 文本/regex/大小写、动态日期/区间、存在、视觉、下载；视觉默认10%，首Chrome云基线放宽≥40%，其他浏览器首跑跳视觉建基线。 | 新视觉基线是待批准状态；固定浏览器基线，不能把首次跳过/放宽校验算作视觉通过。 | P0 |
| W101 | [BrowserStack Low Code Automation Troubleshooting](https://www.browserstack.com/docs/low-code-automation/troubleshooting-docs/get-support) | 桌面Help上传诊断信息含测试名/build URL/是否间歇/附件，可查看App版本。 | 一键诊断包先预览脱敏，区分产品缺陷、脚本问题和环境错误。 | P1 |
| W102 | [Configure proxy settings](https://www.browserstack.com/docs/low-code-automation/troubleshooting-docs/proxy-configuration) | 桌面代理支持无/系统/HTTP/HTTPS/PAC及绕过列表，可测试连通；health/visual错误能直接跳代理配置。 | 本地连接器网络向导与逐服务诊断，配置只影响明确的连接器会话。 | P0 |
| W103 | [Run health check on Low Code Automation](https://www.browserstack.com/docs/low-code-automation/troubleshooting-docs/run-health-check) | 健康检查覆盖Low Code Server/Web/Percy/更新/存储服务，状态healthy/degraded/error。 | 连接前检测浏览器、通信、凭证、网络/代理、工件存储，明确故障点。 | P0 |
| W104 | [Types of testing](https://www.browserstack.com/docs/low-code-automation/type-of-testing) | 测试类别目录区分跨浏览器、移动浏览器、无障碍、视觉。 | 功能、视觉、A11y结果维度分开，按能力显示可用配置。 | P1 |
| W105 | [Test your website for Accessibility using Low Code Automation](https://www.browserstack.com/docs/low-code-automation/type-of-testing/accessibility-testing) | 需Accessibility许可；桌面仅Chrome、真机仅Android Chrome；可WCAG/高级/潜在/最佳实践扫描，部分页每run单组件。 | A11y作为单独检查与质量规则，潜在问题需复核，不能声称扫描全覆盖。 | P2 |
| W106 | [Cross Browser Testing](https://www.browserstack.com/docs/low-code-automation/type-of-testing/cross-browser-testing) | 套件Chrome/Firefox/Safari/Edge矩阵；Safari Pro；正文说视觉仅Chrome，与新validation/release页冲突。 | Playwright浏览器引擎矩阵与真实Safari能力明确区分，实际验证后承诺。 | P0 |
| W107 | [Mobile Browser Testing](https://www.browserstack.com/docs/low-code-automation/type-of-testing/mobile-browser-testing) | 移动录制为仿真，可云真机或仿真；不支持移动新tab录制/视觉验证，真实设备上传不支持；列具体设备系统组合。 | 响应式Web与原生App流程分开，仿真不能等同真机，设备清单以运行时服务为准。 | P1 |
| W108 | [Visual testing in Low Code Automation](https://www.browserstack.com/docs/low-code-automation/type-of-testing/visual-testing) | Ultimate beta Percy全页视觉仅云执行，功能重跑不产生新Percy报告，视觉不改功能测试状态，设备同浏览器家族合并报告。 | 独立视觉结果、基线批准与质量门槛，Insight明确功能通过但视觉待审。 | P1 |

### 关键支撑FAQ（不计作产品文档正文页）

| ID | 标题与完整URL | 正文细节 | TAP建议 |
| --- | --- | --- | --- |
| F001 | [How do I execute tests against localhost or private websites?](https://www.browserstack.com/support/faq/low-code-automation/basics-low-code-automation/how-do-i-execute-tests-against-localhost-or-private-websites) | 私网按需与计划运行需不同凭证；计划机器须始终在线。 | 连接器心跳、执行节点网络和服务凭证分开诊断。 |
| F002 | [How does the cloud execution work? Does it interfere with my Automate parallels?](https://www.browserstack.com/support/faq/low-code-automation/basics-low-code-automation/how-does-the-cloud-execution-work-does-it-interfere-with-my-automate-parallels) | LCA cloud运行不消耗Automate产品并行额度。 | 不同执行池/配额不混算。 |
| F003 | [What are the minimum requirements to start recording tests on Low Code Automation?](https://www.browserstack.com/support/faq/low-code-automation/basics-low-code-automation/what-are-the-minimum-requirements-to-start-recording-tests-on-low-code-automation) | 需Chrome及正确Mac架构应用，并列出BrowserStack/Percy/S3/TCG域名可达要求。 | 本地连接器安装向导应逐服务检查。 |
| F004 | [How do I fix the “Unable to establish connection on the required port 9222” issue?](https://www.browserstack.com/support/faq/low-code-automation/troubleshooting-low-code-automation/how-do-i-fix-the-unable-to-establish-connection-on-the-required-port-9222-issue) | 9222是录制器与浏览器远程调试协议通信端口。 | 连接器检测端口占用与策略限制，连接只在受控本机会话。 |
| F005 | [How to fix the “Connection failed due to certificate issue” error while recording tests?](https://www.browserstack.com/support/faq/low-code-automation/troubleshooting-low-code-automation/how-to-fix-the-connection-failed-due-to-certificate-issue-error-while-recording-tests) | 证书链/企业代理SSL检查可能阻断录制；提供证书路径或忽略SSL示例。 | 先提供项目受控CA配置，不把忽略证书变成全局默认。 |
| F006 | [I am unable to start recording as the browser does not launch. How can I fix this?](https://www.browserstack.com/support/faq/low-code-automation/troubleshooting-low-code-automation/i-am-unable-to-start-recording-as-the-browser-does-not-launch-how-can-i-fix-this) | 浏览器启动失败可因端口/VPN/架构或PATH中旧chromedriver；App自身管理driver。 | TAP连接器固定Playwright浏览器版本并显示冲突来源。 |
| F007 | [Why am I unable to perform Visual Validations?](https://www.browserstack.com/support/faq/low-code-automation/troubleshooting-low-code-automation/why-am-i-unable-to-perform-visual-validations) | 视觉失败可因Percy网络、旧Apple Silicon Rosetta需求或自签证书。 | 视觉服务健康与实际断言失败分开，旧FAQ兼容信息需按版本核验。 |

### 按Web用户流程汇总

| 用户阶段 | 从正文得到的完整能力 | TAP应落到的交互与归属 |
| --- | --- | --- |
| 连接应用 | Web管理与桌面创作分工；选择URL、设备/分辨率、权限/flags、Basic Auth/headers/cookies/localStorage、代理/Local隧道、IP地域与locale | TAP自动化工作台先选择托管浏览器或Electron本地连接器；先做浏览器/网络/凭证/存储健康检查，显示当前连接方式和可达环境。用户日常浏览器与调试干净会话分离。参考W024、W041、W070–071、W090、W102–103。 |
| 录制操作 | 点击/双击/右键、输入、下拉、hover、拖拽、键盘、alert/confirm、页面前后退/刷新、标签/窗口、iframe/shadow、部分富文本、canvas；文件上传有大小和交互限制 | 左侧步骤，中间真实应用，右侧当前元素/输入/证据；录制、暂停、手动操作、补录状态明确；窗口/标签/frame上下文不能只藏在生成代码里。参考W092、W094。 |
| 辨认元素 | 同名元素编号高亮，按文字/位置/父容器/可靠属性缩小，项目按域设置可靠/不可靠属性，允许步骤覆写与CSS/XPath | Web独有“选择的是哪个按钮”交互：显示候选数、所处容器、定位理由与当前唯一性；错误时提供重选和可审阅修复，不把AI置信度等同正确。参考W086、W082。 |
| 加入业务检查 | 文本/正则/大小写、日期/相对日期/区间、存在/不存在、视觉区域、Canvas OCR、下载元数据与PDF/CSV内容、API/数据库、自定义布尔JS | 操作和预期分别编辑；关键业务断言必需且固定；视觉/无障碍与功能结果分维度；新基线须明确批准。参考W079、W081、W084、W099–100、W105、W108。 |
| 组织数据 | 静态/动态/提取/API/JS变量，CSV/数据库数据集、行筛选/名称、全局变量、secret及环境覆值、随机函数/AI数据、父子测试共享、模块参数 | 一个面板查看来源/范围/类型/引用，预览本次运行实际值来源；秘密只运行时解析，数据版本和随机种子可追踪；调试替代值与正式数据明确不同。参考W006、W009、W011、W017–020、W043、W093。 |
| 组织流程 | If/ElseIf/Else、计数/条件循环、模块复用与参数覆值、JS片段库、API/数据库/邮件/TOTP动作 | 普通流程用结构化步骤；嵌套、循环上限、变量先用后定义在发布前校验；模块固定版本，展示引用影响；JS区分页面执行与Node执行器。参考W019、W080–085、W088、W096–097。 |
| AI辅助创作 | 旧静态提示词转步骤、整用例生成Beta、每次运行动态AI步骤、Alpha agentic按需求/URL探索/已有用例/TCM输入后评审、批量自动化和回放、自愈 | TAP AI负责生成/解释和修复建议，TAP提供真实会话、受限调试和证据；草稿先人工审预期；默认静态可重放步骤，动态AI块显式选择并显示预算和实际动作。参考W072–078。 |
| 调试与修改 | 本地独立干净回放，高亮正在操作的元素；无需录制可改配置/重排，新增操作需现场；执行到此处后补录；失败截图对比、JS/网络日志 | 调试服务独立于Jenkins正式回归，单步/断点/执行到此处/选数据行/选择标签；保留暂停原因和上下文，修改形成草稿，复跑后发布。参考W024、W062、W064、W095。 |
| 保存版本与复用 | 测试每保存出版本，恢复产生新版本；模块有独立版本但文档间一致性有疑点；项目/文件夹/tags；跨项目复制依赖与套件同项目复制 | 脚本、模块、基线、数据、环境配置形成发布清单；使用固定依赖；恢复不混最新模块；复制预览共享与克隆、敏感数据和用例映射差异。参考W019、W044、W049–050、W083、W091。 |
| 编排正式执行 | 套件、浏览器/版本矩阵、设备、顺序/并行、blocks依赖、环境、多个时间或间隔schedule、重跑失败/跳过/数据行 | TAP提交不可变运行清单；Jenkins排队/执行/取消/超时；TAP追踪状态并接回证据，调度只能有一个责任方，不复制独立执行平台。参考W022、W031、W042–043、W047–048、W069。 |
| 分析与缺陷闭环 | 每步截图、云视频、HAR/console、XML报告、原/重跑历史、邮件/Slack、公共/私有链接、用例映射、问题创建/更新；高级分析对接另一产品且beta | 对象存储放证据、MySQL保存事务性状态、ClickHouse统一查询运行事实和趋势；详情能回到步骤/原始错误/数据行/首次与重试；缺陷关联和修复复核独立状态。参考W008、W014–016、W037–040、W061–066。 |

#### Web能力边界不能合并成一句“Playwright都支持”

- **定位上下文**：DOM、iframe、Shadow DOM、canvas是不同处理路径；多标签还有归属与匹配问题。LCA对Shadow DOM内部iframe、动态广告、移动新标签的具体限制是产品限制，不能直接套为Playwright引擎限制，也不能因为Playwright能定位某元素就声称TAP录制器已经能捕获并导出它。W082、W092、W107。
- **网络**：网页Local隧道、API步骤执行、数据库连接、缺陷系统连接、浏览器代理、出口IP位置均不同；页面可访问不表示所有后端调用可访问。W009、W040–041、W070、W081、W102。
- **复用**：模块引用会传播更新，片段库导入是复制；跨测试共享值要求父子依赖，本地默认值不等价真实上游；TAP的已发布运行应固定这几种依赖。W011、W019、W085。
- **检查结果**：执行成功、功能断言通过、视觉基线初始化/批准、A11y扫描完成、数据同步成功是不同事实；浏览器故障和连接失败不能归成业务失败。W047、W059、W100、W105、W108。

### 重要限制、冲突与公开API核验

#### 已有明确正文依据的限制

1. 录制与本地运行当前依赖桌面应用和Chrome；支持Mac/Windows下载，文档没有给出Linux桌面录制应用。Local隧道二进制支持Linux不意味着Linux录制桌面端。W024、W041、F003。
2. LCA代码导出不是完整可移植执行器：`.side`/Nightwatch只提供部分步骤，AI自愈、API/视觉/邮件等不能据此迁移；TAP要自己生成并验证Playwright TypeScript。W003–005。
3. AI动态步骤本文上限20/测试、500执行/用户/月；网络/DOM/console/非功能检查不能用该AI交互替代，agentic v2为Alpha，不宜拿宣传速度作TAP验收。W073–075。
4. 循环最大30，嵌套if/loop/module均有限制；一步超时最大240秒；CSV100×100、单值1000字符，数据库结果100×40；上传/下载验证均25MB。TAP不必复制数值，但须有自己的明确限制与超限反馈。W009、W017、W080、W082、W088、W092、W099。
5. 私网API正文明确不支持云API步骤，虽然私网网页、数据库和近期私网视觉有各自方案；不能从“Local”统一开关推断。W009、W041、W059、W081。
6. 元素视觉默认10%差异，首Chrome云执行取较高40%阈值，Safari/Firefox/Edge首跑可不验证而建立基准。全页视觉云端独有、重跑不重做Percy、结果不影响功能状态。TAP须单独显示基线未建立/待批准。W100、W108。
7. 公共build链接正文称默认开启且可下载网络、视频、JUnit；这是对标差异，TAP不照搬公开默认。W065。
8. GRR不完整覆盖AI/第三方，且有地域功能差异；Console日志仅Chrome/Edge；移动是真机与仿真分别定义，移动Web不是原生App。W023、W062、W107。

#### 需按版本/账号实测或向官方确认的文档冲突

| 问题 | 正文之间的差异 | 设计与报告处理 |
| --- | --- | --- |
| 模块版本 | W019 Modules已给独立版本查看/恢复；W050仍说模块不版本化、测试恢复保留最新模块且需联系支持 | 不宣称测试回滚可完整回滚模块；TAP固定模块版本解决自己的可复现问题。 |
| AI与secret | W075 AI interactions明确不支持secret；W056 3.48.4说AI-assisted steps支持secret和变量 | 可能是版本或AI产品路径不同，不能自行断定已全部支持；TAP默认模型不接收秘密原值。 |
| 跨浏览器视觉 | W106说视觉验证仅Chrome；W100列四浏览器建基线，W055修Safari截图 | 新详细页与旧概览更新不一致；应按具体版本/浏览器试验，报告保留两者。 |
| 富文本 | W092输入段说不支持富文本格式；同页后文列CKEditor4/5、TinyMCE、Froala、Quill、Jodit、Slate并回放HTML | 后段详细能力可能较新，暂按“指定RTE最终内容支持、工具栏动作非逐击验证”设计，不写成任意RTE已支持。 |
| 标签范围 | W091说tags项目隔离；W044说tags属于group、跨项目存在 | 权限/复制时按实际接口核验；TAP采用清晰项目边界。 |
| 私网API | W081明确云API不支持私网；W059仅说Local API失败原因改进 | 发布说明不足以推翻功能限制，不承诺已解除。 |
| 套餐 | W012/W014总览Pro；W013/W016另有Essentials手动XML方式 | 区分自动同步与报告/导入方式，不能简单写全功能仅Pro。 |
| 数据设备表 | W107部分最新设备/系统组合值得再次核验 | 不把静态文档设备清单当实时可选设备能力；运行时获取兼容矩阵。 |

#### 公开API能够证明什么

已公开且读取过正文的接口（鉴权与实际调用均未测试）：

| 接口 | 文档证明的能力 | 不能顺带推断的能力 |
| --- | --- | --- |
| `POST /api/v1/test-suite/{test_suite_id}/run` | 触发现有套件，可传环境/URL/隧道，返回build_id | 创建套件、上传步骤、编辑模块、保存AI生成结果、取消任务、带幂等键提交均未在本文档树给出。W022。 |
| `GET /api/v1/builds/{build_id}/status` | 查状态、汇总与测试执行列表、build链接 | 完整事件流、稳定分页、所有错误枚举、webhook、取消语义、首次和全部retry详情未充分给出。W022。 |
| `GET /api/v1/builds/{build-id}/report?type=...` | 下载XML格式结果，含环境/用例映射与失败/跳过信息 | 不等于全部截图/视频/HAR/step证据下载API，亦不等于自动写入ClickHouse。W014。 |
| `GET /api/v1/test-list` | 获得账号group的测试ID/名称/tags | 不等于项目全资产CRUD接口或完整版本列表。W005。 |
| `GET /api/v1/tests/{test_id}/export?format=side\|nightwatch` | 部分代码导出 | 不等于Playwright导出、无损往返、原始自愈算法或可再次导入。W003–005。 |

CI教程多为短示例，通常只轮询passed/failed，未处理请求失败、超时、终止、幂等和凭证轮转；部分跨step变量和GitHub示例有明显残缺。它们适合确认集成方向，不应直接变成生产执行模板。Jenkins应保留TAP运行ID与队列/作业/构建ID的关联，触发响应丢失先查原任务；取消须等待执行节点确认为终态；报告上传失败独立重试，不能重跑业务测试替代上传。以上后半段是TAP设计建议，非对BrowserStack私有接口的断言。

#### Playwright方案的实际技术边界

这些结论来自Playwright官方文档，TAP仍需实现产品层：

- Playwright提供Chromium、修改过的Firefox和WebKit；Chrome/Edge可指定channel。WebKit不是品牌Safari，也不能任意控制未支持的Firefox/Safari安装版本；浏览器二进制与Playwright版本相关。因此不能把“Playwright三引擎通过”写成“真实Safari及所有历史版本已测”。[官方浏览器说明](https://playwright.dev/docs/browsers)。
- 手机/平板设备配置是对viewport、user agent、触摸等参数的仿真，并不自动带来真实iOS/Android设备、系统级弹窗或原生App能力。真机Web/原生App仍按各端执行方案验证。[官方仿真说明](https://playwright.dev/docs/emulation)。
- Codegen是录制与代码生成基础，不直接提供TAP多用户Web嵌入编辑器、远程画面流、版本/模块/权限、AI修复、Jenkins编排或ClickHouse归集；这些是TAP需建设的产品和服务。此为基于功能范围的工程判断。[官方生成器](https://playwright.dev/docs/codegen)。
- Playwright定位器会重新定位并支持自动等待，建议以用户可见语义和明确test ID为基础；封闭Shadow Root不支持、XPath不穿透Shadow DOM；canvas内部对象没有普通DOM定位。TAP必须保留上下文和扩展定位路径，不能靠长XPath包办。[官方定位器](https://playwright.dev/docs/locators)、[LCA Canvas](https://www.browserstack.com/docs/low-code-automation/test-recording/canvas)。
- Trace Viewer可查看动作、DOM快照、截图、网络、console等现场；它是失败调查证据基础，不自动给出正确的业务根因或跨平台质量趋势。TAP需保留原始证据、加入稳定步骤ID和用例/需求关联，再由Insights提供分析。[官方Trace Viewer](https://playwright.dev/docs/trace-viewer)。

#### 建议验收用例

首批P0用真实网站测试：重复按钮不同父容器；动态属性；嵌套iframe/开放Shadow DOM；新标签/弹窗顺序变化；登录cookie和请求头域限制；延迟加载/隐藏遮挡；自定义日期/金额断言失败；下载/上传；断点补录；参数缺值；模块版本升级/回滚；代理/隧道离线；Jenkins排队取消/回调重复；报告上传失败；首跑失败重试成功；导出包在无TAP UI的独立Jenkins节点运行。Canvas、RTE、邮箱/TOTP、动态AI、PDF/CSV内容与视觉/A11y随后按P1/P2补专项验收，未验证能力在矩阵中明确标为未支持。
