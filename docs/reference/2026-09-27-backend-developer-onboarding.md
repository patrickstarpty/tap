# 后端开发上手指引：从业务状态到可恢复服务

更新日期：2026-09-27。适用于 Python/FastAPI 开发者。所有命令从仓库根目录执行。目标是能启动服务、追踪三条业务链路，并完成一次符合契约和授权边界的修改。

## 1. 先划清两个后端的职责

| 后端                          | 负责的业务                                                                   | 入口与存储                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| TAP AI：`apps/tap-ai-backend` | 可信知识、对话/引用、Graph、测试设计、Insights 只读解释                      | `tap.entrypoints.tapper_api`；MySQL 权威记录、对象存储原件、Milvus 检索投影、Redis 唤醒 |
| TAP：`apps/backend`           | pytest XML / Allure Results ZIP 接收、报告账本、测试事实、Insights 查询/证据 | `tap_platform.app:app`；MySQL 账本、本地报告对象目录、ClickHouse 投影                   |

完整 `/prototype` 展示产品设计，不能据此推断 API 或执行器已经实现。先阅读[产品边界](../architecture/2026-09-15-tap-ai-product-boundary.md)和[当前交付门禁](../reviews/2026-09-26-trusted-knowledge-insights-delivery-gate.md)。目前隔离模拟 UAT 通过，真实业务 M1–M4 PENDING；历史全量 `make test` 失败记录仍保留，不能称全仓测试全绿。

## 2. 准备依赖和本地配置

准备 Python 3.13、uv、Node 22.22.0、Corepack/pnpm 10.15.1 和可用的 Docker Compose。

```sh
make bootstrap
```

首次从 `.env.example` 复制为 `.env`；已有配置不要覆盖。按示例填写模型网关地址、凭证和数据库配置。配置真实模型会产生外部调用，开发验证优先使用隔离 runner 的本地 stub；stub 结果不能作为真实模型质量证据。

本地镜像首次需要构建。以下为 Apple Silicon 示例，其他机器改为匹配的平台：

```sh
make object-store-build PLATFORM=linux/arm64
TAPPER_PARSER_PLATFORM=linux/arm64 make parser-build
make demo-up
make demo-check
make tap-ai-migrate
```

**完成标志：**中间件检查通过、迁移到当前 head，无需删除已有数据。`demo-up` 启动本地依赖，不等于 API/Worker 已启动。配置含凭证，只保存在本地，不粘贴完整环境或日志到评审中。

## 3. 启动 TAP AI 并走通资料链路

```sh
make tap-ai-dev
```

默认 API `127.0.0.1:8000`，页面 `127.0.0.1:5173`。受监管进程包括 API、Relay 和各类 Worker；只运行 `make tap-ai-api` 不足以处理异步任务。

1. 请求 `/api/v1/runtime-mode`，确认服务端提供的 Project 范围。
2. 在 Library 上传小型 MD 文件，追踪 Source、Document Revision 和处理状态。
3. 在文档详情开始业务审核，核对原件与提取内容；满足复核要求后发布。
4. 在 Tapper 选择已发布来源，提问并打开引用；检查回答与证据版本一致。
5. 普通刷新验证恢复。要验证独立复核、撤回和进程重启，使用步骤 6 的隔离 E2E。

沿以下入口逐层阅读：

| 阶段       | 路径（相对仓库根）                                                                                                                                 | 必须保持的语义                                         |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 装配与身份 | `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py`、`interfaces/http/dependencies.py`                                                     | 服务端确定 Project/身份；禁止相信浏览器自报角色        |
| 上传与解析 | `interfaces/http/routes/knowledge_sources.py`、`entrypoints/tapper_parser_worker.py`、`tapper_ingestion_worker.py`（均在 TAP AI 的 `src/tap/` 下） | 原件、Revision、投影各有状态；解析就绪不等于可检索发布 |
| 核对与发布 | `apps/tap-ai-backend/src/tap/modules/knowledge/application/review.py`、`interfaces/http/knowledge_review_service.py`                               | 当前版本、独立复核、发布/撤回记录                      |
| 问答与引用 | `apps/tap-ai-backend/src/tap/interfaces/http/routes/conversations.py`、`citations.py`                                                              | 冻结输入、持久会话/SSE、授权证据                       |
| 测试设计   | `apps/tap-ai-backend/src/tap/entrypoints/tapper_test_design_worker.py`、`modules/test_management/`                                                 | 草稿可编辑，评审绑定版本，已发布版本不可变             |

同一进程中的重复请求与进程中断后的重试都要考虑。MySQL 是权威账本，Redis 是可重建唤醒机制；不能用 Redis 状态代替任务已提交或业务已发布的事实。

## 4. 启动 TAP Insights 独立运行链路

### 4.1 报告来源：pytest + Allure

当前业务以 **pytest 执行测试、Allure 展示测试报告**为主。TAP 支持 `pytest` XML 与 `allure` Results ZIP 两种来源配置，并保留 `junit` 旧格式。JUnit XML 是交换格式，不代表客户使用 Java/JUnit 测试框架。

在客户测试项目的既有 pytest 环境中（已安装 `allure-pytest`），同一次运行可输出两类产物。以下是准备样本的命令模板，请为每次运行选择新的输出目录，避免混入历史结果：

```sh
python -m pytest --junitxml=artifacts/run-001/junit.xml --alluredir=artifacts/run-001/allure-results
```

参数依据：[pytest 官方命令参考](https://docs.pytest.org/en/latest/reference/reference.html)、[Allure Pytest 文档](https://allurereport.org/docs/pytest/)。这是外部测试项目的执行示例；Allure 插件只用于生成来源报告，不是 TAP 后端运行依赖。将本次 `allure-results` 目录压缩为 ZIP 后可选择 Allure 格式上传；不接受生成后的 HTML 站点。

接入前按以下顺序核对：

1. 保留同一次运行的 pytest XML、Allure results/附件与运行标识。ZIP 可以包含 `allure-results/` 根目录，附件路径须保持原样。
2. 上传 manifest 的 `reportFormat` 使用 `pytest`、`allure` 或 `junit`；省略时沿用旧 `junit` 行为。容器与格式明显不符（`allure` 却不是 ZIP，或 XML 格式却上传 ZIP/声明 `application/zip`）时，上传直接返回 `415`，不生成回执；内容损坏的报告仍保留为可追溯的拒收回执。对应解析版本是 `pytest-junit-v1`、`allure-results-v1`、`junit-v1`，账本保存版本供恢复重放。
3. pytest 配置从 `classname::name` 生成稳定 ID；若来源提供 `tap.test_id`，优先保留。只有明确确认完整历史且用例唯一、没有重试摘要时，普通 XML 才视为第 1 次执行。重名节点、无效 attempt、rerun/flaky 摘要不伪造重试序列。
4. Allure 使用 `testCaseId`/`fullName` 标识测试、`historyId`/参数区分实例；manifest 的 `externalTestIdMapping` 优先生效，键写作 `module::test`（`fullName` 中的 `module#test` 按此规范化），因此同一映射可同时用于 pytest XML 与 Allure。在完整历史已确认且时间顺序明确时排列 attempts。时间重叠或并列起点会保留未知顺序。每次运行使用空的新结果目录，不能混入旧结果。
5. 勾选 **Complete attempt history** 前确认当前报告确实包含该运行全部 attempts；默认不勾选。未确认时首轮/重试等指标会显示不可用，不把已有数据伪装成完整历史。
6. Run 详情的 **View report evidence** 可查看失败信息、嵌套步骤和关联 fixture；附件通过受项目权限保护的接口读取，PNG/JPEG 可预览，其他格式下载。缺失附件有明确提示。
7. 上传原件仍遵守现有大小限制；ZIP 另有成员数、解压大小、压缩比、JSON/步骤深度及展开后证据大小限制。拒绝路径穿越、软链接、重复成员与加密包；不会将 ZIP 解压到磁盘。macOS 压缩产生的 `__MACOSX/`、`._*` 与 `.DS_Store` 会被忽略。超长失败信息、堆栈、步骤名、参数与 `fullName` 截断展示并标注 `[truncated]`，不会拒收整次运行；长 `fullName` 仍按完整值计算身份。
8. 先使用[官方插件生成的合成样本](../../apps/backend/tests/fixtures/insights/pytest-allure/README.md)验证，再核对客户来源与运行语义。格式兼容测试不替代真实业务签字。

### 4.2 启动与验证现有导入通道

先用 `make tap-insights-e2e` 观察仓库自动准备的一条完整报告旅程（依赖安装见步骤 6）。它会启动独立资源、执行浏览器及重启检查并清理，**不会留下持续运行的演示站点**。

需要持续开发时按下面顺序手动启动：

1. 使用专门供 TAP Insights 的 MySQL 数据库和对象目录。不要把两个后端的 Alembic 迁移写到同一个数据库；默认 `.env` 是 TAP AI 的配置起点。
2. 在这些终端中导出 TAP 专用配置。`TAP_DATABASE_URL` 使用同步 `mysql+pymysql://…` URL；TAP AI 使用 `mysql+asyncmy://…`。配置 `TAP_REPORT_OBJECT_ROOT` 为可写持久目录。
3. 配置 `.env.example` 中的 ClickHouse reader/writer 参数；配置 `TAP_REPORT_ACCESS_TOKEN`、`TAP_REPORT_PROJECT_ID`、`TAP_REPORT_TOKEN_EXPIRES_AT` 为同一组本地短期授权。手动 make 目标并非全部自动加载 `.env`，启动前在每个终端导出同一组变量。
4. 启动 ClickHouse、检查连通并迁移 TAP 专用数据库：

```sh
make tap-insights-up
make tap-insights-check
make tap-backend-migrate
```

5. 在共享上述 TAP 配置的两个终端分别执行：

```sh
make tap-backend-dev
```

```sh
make tap-insights-worker
```

6. 第三个终端运行 `make tap-web-dev`，打开 `http://127.0.0.1:5174/?module=test-insights`，输入对应 Project ID 和 token。
7. 展开 **Upload test report**，选择 **JUnit XML (explicit identities)**，上传[报告 fixture](../../apps/backend/tests/fixtures/insights/reports/retry.xml)。填写 Source、Run、Batch、Shard、Application/Script commit、Environment、Configuration、Started at；选好文件后确认完整 attempt 历史再提交。字段示例见[浏览器规格](../../apps/web/tests/e2e/insights-report.spec.ts)。保留 fixture 来源标识，不能冒充客户 CI。
8. 等待 **Ready for Insights**，查看指标，打开 Run，下载原报告。重复导入相同身份，核对去重；更正必须使用版本/更正语义，不能覆盖旧证据。

**完成标志：**上传返回的收据最终就绪、查询有数据、钻取能回到原报告。若需要完全可复制的自动环境配置，以[隔离启动脚本](../../scripts/run-tap-insights-e2e.sh)为准，不复制其中临时凭证到长期环境。

## 5. 追踪报告与解释流程

| 顺序               | 代码入口                                                                                                 | 调试重点                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| 1. HTTP 授权和接收 | `apps/backend/src/tap_platform/insights/http.py`                                                         | 项目权限、上传上限、请求身份                       |
| 2. 原件与账本      | `insights/application/intake.py`、`insights/adapters/mysql.py`（TAP `src/tap_platform/` 下）             | 原始文件、收据、重复与冲突                         |
| 3. 解析和投影      | `apps/backend/src/tap_platform/insights/worker.py`、`insights/adapters/report_parser.py`                 | 稳定测试映射、attempt、重试、更正和投影水位        |
| 4. 查询和钻取      | `apps/backend/src/tap_platform/insights/application/queries.py`                                          | 授权范围、指标口径、查询快照、限制与缺失证据       |
| 5. AI 解释         | `apps/tap-ai-backend/src/tap/modules/ai/adapters/tap_insights.py`、`application/insights_explanation.py` | 服务端重新取事实，区分事实/假设/缺失信息，只读权限 |

跨产品解释另需 `.env.example` 中完整的 `TAP_INSIGHTS_DELEGATED_*`、service token、授权版本、base URL 和模型费用上界配置。凭证留在服务端；前端只交接 queryId/resourceRef，不交接可信数值或扩大权限。

## 6. 完成第一次修改并验证

1. 选定一个领域，例如报告拒绝原因或文档发布校验；先读同领域现有测试。
2. 行为修改先补一个失败测试；在 application/domain 修正逻辑，在 adapter 处理基础设施差异。Domain 不依赖 HTTP DTO、FastAPI 或数据库实现。
3. 数据变化添加迁移，保留旧数据升级和重试语义；不要通过清库让测试通过。
4. API 变化同步后端 schema、错误契约及相应测试，再运行 `make contracts`，把生成文件一并交给前端。
5. 先运行修改所属的窄测试，再按范围执行检查。

```sh
uv run --project apps/backend pytest apps/backend/tests -q
make tap-backend-check
```

TAP AI 使用 `make tap-ai-check`、`make tap-ai-test`。跨产品或公共契约变更按需扩大到 `make check`、`make test`；如有失败，应报告实际结果与原因，不能用窄测试覆盖全量失败结论。

浏览器旅程先安装两套 Playwright 所需 Chromium：

```sh
corepack pnpm --dir apps/tap-ai-frontend exec playwright install chromium
corepack pnpm --dir apps/web exec playwright install chromium
make demo-e2e
make tap-insights-e2e
```

联合恢复验收为 `TAP_RUN_TASK14_ACCEPTANCE=1 make task14-acceptance`，耗时更长，用于跨域交付/恢复验证。所有这些 fixture 运行均不替代真实模型与具名业务审核。

### 本轮 pytest / Allure 验证记录（2026-09-27）

- `make tap-backend-check` 通过：134 项测试通过，35 项需独立集成环境的测试跳过；边界、Ruff、格式与 mypy 通过。
- Web 检查及契约漂移检查通过；前端报告组件覆盖格式选择、历史确认、证据加载与权限错误。
- `make tap-insights-e2e` 的四个阶段通过：上传与钻取、应用重启、MySQL/ClickHouse 重启、投影重建后查询。使用真实 MySQL/ClickHouse 和浏览器，但来源是合成测试数据。
- 重建旧 JUnit 与新 Allure 共 6 条执行事实，行数及校验和一致；桌面与 390 像素手机截图已检查，手机无横向溢出。完整 `/prototype` 截图与既有基准 SHA-256 一致。
- 这些结果证明格式接入与本地持久化，不替代真实客户 CI、模型质量或 M1–M4 业务签字。

## 7. 停止、定位问题和交接

终止应用进程后，普通停止中间件用 `make demo-down`、`make tap-insights-down`，保留数据卷。不要使用 `demo-reset` 排查日常问题。

| 现象                   | 定位顺序                                                           |
| ---------------------- | ------------------------------------------------------------------ |
| AI 上传迟迟未就绪      | Source 失败原因 → Parser/Ingestion Worker → 对象存储与投影         |
| 草稿未生成             | Job 状态 → Relay/Worker → 模型网关；不要绕过账本手工标成功         |
| TAP API 数据库驱动报错 | 是否误用 AI 的 asyncmy URL，是否导出了 TAP 专用配置                |
| 收据就绪但指标异常     | 查询范围 → 分片/attempt 语义 → 投影水位 → ClickHouse reader/writer |
| 401/403                | token 有效期、Project 与权限；不通过关闭授权修复                   |
| AI 解释失败            | 两端委托授权是否一致、queryId 是否可读、模型路由是否可用           |

交接前提供：契约差异、迁移/回退影响、业务状态与错误说明、测试命令及实际结果、脱敏的重现步骤。运行 `git diff --check`；确认没有凭证、客户资料和本地产物进入提交。

下一位开发者应能按本文启动对应应用，并完成资料发布或报告导入中的至少一条流程。前端协作见[前端指引](2026-09-27-frontend-developer-onboarding.md)，业务讲解见[客户演示](2026-09-04-customer-prototype-demo-guide.md)。

## 知识审核工作台补充

当前长文档筛选、定位标记、授权原件阅读与 XLSX 解析见[知识审核工作台](2026-09-27-knowledge-review-workbench.md)。升级 XLSX 解析时须重建隔离解析镜像，原件读取继续受项目及审核资源权限约束。
