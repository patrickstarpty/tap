# 前端开发上手指引：从业务流程到页面交付

更新日期：2026-09-27。适用于首次参与 TAP 的 React/TypeScript 开发者。所有命令从仓库根目录执行；先完成步骤 1–3，再按负责的业务链路进入步骤 4–7。

## 1. 先理解用户要完成的工作

主线是：**资料上传 → 核对并发布可信知识 → 带引用的问答 → 生成、评审和发布测试计划 → 导入已有测试报告 → 查看指标、钻取证据并请求解释**。

| 用户任务                           | 运行页面所属应用           | 主要代码入口                                                             |
| ---------------------------------- | -------------------------- | ------------------------------------------------------------------------ |
| 资料、问答、Graph、测试设计        | `apps/tap-ai-frontend`     | `src/features/knowledge/`、`conversations/`、`graph/`、`testManagement/` |
| pytest/Allure 上传、指标和运行详情 | `apps/web`                 | `src/features/insights/`                                                 |
| 查看完整产品设计和跨模块交互       | `apps/web` 的 `/prototype` | `src/widgets/tap/TapProductPrototype.tsx`                                |

先读[产品原型基准](2026-09-22-product-prototype-baseline.md)。完整原型是设计基准；运行页面通过 API 获取实际业务状态。修改前确认需求属于哪个应用，不要因为两套应用都有同名组件就跨目录复制实现。

当前隔离模拟业务 UAT 已通过；真实业务 M1–M4 仍待验收。能力与证据以[交付门禁](../archive/reviews/2026-09-26-trusted-knowledge-insights-delivery-gate.md)为准。

## 2. 准备环境并打开完整设计

安装 Git、Node **22.22.0**、Corepack；仓库固定使用 pnpm **10.15.1**。API 联调另需 Python 3.13、uv 和 Docker Compose。

```sh
node --version
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm --dir apps/web dev --port 15176
```

打开 `http://127.0.0.1:15176/prototype`。依次进入 Tapper、Library、Test Management、Test Insights、Low Code Automation，确认菜单与跨模块链接可达。这里看到的示例数据不证明后台已完成操作。

**完成标志：**能指出资料审核、测试计划、报告分析分别在哪个模块，并能说明当前页面使用的是示例还是 API 数据。

## 3. 打开真实 API 页面

### 3.1 TAP AI 页面

按[后端上手指引](2026-09-27-backend-developer-onboarding.md)步骤 2–3 配好本地依赖，然后执行：

```sh
make tap-ai-dev
```

默认页面 `http://127.0.0.1:5173/`，API `http://127.0.0.1:8000/`。此命令已启动前端，不要再启动一份占用相同端口的进程。若后端同事已单独启动 API 与 Worker，只执行 `make tap-ai-web`。

检查浏览器网络面板中的 `/api/v1/runtime-mode`，页面应使用其返回的 `projectId` 建立项目客户端。不要在 UI 中编造 Actor、Role 或授权范围。

入口调用关系：`src/app/App.tsx` → `src/pages/TapAiPage.tsx` → `src/widgets/tap/TapProductPrototype.tsx`。当前 TAP AI 入口使用 `conversationSource="api"`；`widgets/tapper/TapperWorkspace.tsx` 是旧兼容入口。

### 3.2 TAP Insights 页面

后端按对应指引启动 TAP API、报告 Worker 和 ClickHouse 后，在另一终端运行：

```sh
make tap-web-dev
```

打开 `http://127.0.0.1:5174/?module=test-insights`，输入后端提供的 **Project ID** 和短期 **Access token**，点击 **Open Insights**。Vite 默认代理 `/api` 到 `127.0.0.1:8001`；自定义地址使用 `TAP_WEB_API_TARGET`。

**完成标志：**合法项目可打开分析页面；无数据时显示空态；错误凭证显示错误而不是切换为演示数据。令牌不写入代码、URL、截图或提交。

## 4. 沿一条资料流程读代码

1. 在 Library 添加可提取文字的 MD/TXT/PDF/DOCX；观察上传、处理、失败与重试状态。
2. 打开文档详情进入审核，查看原件与提取结果、审核版本及发布状态。解析完成不等于已发布。
3. 发布后回到 Tapper，选择可用来源并提问，打开回答引用。
4. 刷新页面，区分服务端恢复的业务状态与页面临时状态。

阅读顺序：

- [知识 API client](../../apps/tap-ai-frontend/src/features/knowledge/api/client.ts)：请求和返回类型。
- [KnowledgeReview](../../apps/tap-ai-frontend/src/features/knowledge/components/KnowledgeReview.tsx)：审核与发布交互。
- [Conversation client](../../apps/tap-ai-frontend/src/features/conversations/api/client.ts)：会话请求。
- [审核端到端规格](../../apps/tap-ai-frontend/tests/e2e/knowledge-review.spec.ts)：完整流程及受控身份准备。

独立复核有服务端身份约束。本地单一身份不能自行充当另一位复核人；学习完整交接用隔离 E2E 的 fixture 准备过程，不在产品页面增加角色切换按钮。

## 5. 接着读测试设计和报告分析

业务报告主线是 **pytest + Allure**。**Upload test report** 中选择 pytest JUnit XML、Allure Results ZIP 或旧 JUnit 格式；格式与文件扩展名、请求 Content-Type、manifest 的 `reportFormat` 一起提交。切换格式会清空已选文件。完整历史默认不确认，须由来源负责人核实。

普通 pytest XML 的身份及 attempt 规则、Allure 参数和重试映射见[后端报告接入步骤](2026-09-27-backend-developer-onboarding.md#41-报告来源pytest--allure)。运行详情通过 **View report evidence** 按 receipt + factKey 加载证据，展示失败信息、测试/fixture 步骤及附件。PNG/JPEG 预览只使用授权响应生成的临时 Blob URL，卸载时释放；不直接展示报告提供的 HTML/SVG。

| 流程                | 开发时重点                                    | 对照文件                                                                            |
| ------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------- |
| 对话 → 测试草稿     | 生成任务状态、引用、假设、未知项和覆盖缺口    | `apps/tap-ai-frontend/src/features/testManagement/api/`                             |
| 编辑 → 评审 → 发布  | 保存冲突、修改后采纳、发布后的版本只读        | `apps/tap-ai-frontend/tests/e2e/tapper-test-plan.spec.ts`                           |
| 上传报告 → 收据就绪 | 必填来源身份、轮询、重复、失败、重试和更正    | `apps/web/src/features/insights/components/ReportIntake.tsx`                        |
| 指标 → Run → 证据   | 查询范围、空态、无截图/无步骤提示、原报告下载 | `apps/web/src/features/insights/components/InsightsWorkspace.tsx`、`RunDetails.tsx` |
| 运行详情 → Tapper   | 传资源引用与 queryId，服务端重新授权读取事实  | `apps/web/tests/e2e/insights-report.spec.ts`                                        |

不要把上传成功当作指标可见；等待收据 **Ready for Insights**。不要从 URL 传入或信任指标数值，也不要给报告中不存在的步骤或截图补造内容。

## 6. 完成第一次小改动

建议选择已有页面的一项明确需求，例如优化报告失败提示；以下流程也适用于正式任务。

1. 在 `/prototype` 确认预期交互，并在对应运行页面复现问题。
2. 找到所属 feature 的组件、API client 和现有测试。保持 `app/pages → widgets → features → shared` 依赖方向。
3. 若改变行为，先在同目录测试中补一个用户可观察的失败用例；只改静态文案时做针对性检查即可。
4. 修改组件，覆盖加载、空态、错误、权限不足和成功状态；不把 fixture、模拟控制或实现说明加到产品 UI。
5. 若接口需变，先与后端确认 DTO、错误码、异步状态和兼容性，由后端修改契约后运行 `make contracts`；不要手改 `shared/api/generated/schema.ts`。
6. 运行相关测试，再运行对应应用的检查。

```sh
corepack pnpm --dir apps/web exec vitest run src/features/insights/components/ReportIntake.test.tsx
corepack pnpm --dir apps/web run check
```

TAP AI 改动替换为：

```sh
corepack pnpm --dir apps/tap-ai-frontend exec vitest run src/features/knowledge/components/KnowledgeReview.test.tsx
corepack pnpm --dir apps/tap-ai-frontend run check
```

示例测试仅适合对应组件；实际提交选择与改动相关的测试。涉及完整业务旅程时，由配好依赖的环境运行 `make demo-e2e` 或 `make tap-insights-e2e`，不能用单组件测试代替持久化验证。

## 7. 向后端交接并提交

提交前提供：页面入口、复现步骤、请求/响应示例（脱敏）、错误和异步状态、测试结果。UI 变更附相同视口的前后截图，并确认完整原型所有模块仍可达；不得用独立新壳替换 `/prototype`。

```sh
git diff --check
git diff -- README.md docs/ AGENTS.md
```

接口变化同时提交生成契约；新增环境配置同步 `.env.example`。不要提交本地凭证、客户数据和测试产物。

## 常见卡点

| 现象                   | 先检查                                                                   |
| ---------------------- | ------------------------------------------------------------------------ |
| 能看原型但没有后台请求 | 是否停留在 `/prototype`，是否打开了正确应用的运行入口                    |
| 端口冲突               | 是否同时执行了 `tap-ai-dev` 与 `tap-ai-web`；两套前端默认是 5173/5174    |
| 上传后一直处理         | 后端 Parser/Ingestion/Relay 是否运行；查看 Source 状态，不靠刷新伪造完成 |
| 发布按钮不可用         | 当前版本是否完成审核、是否满足独立复核和权限要求                         |
| Insights 无数据        | 项目、时间范围、收据状态和 Worker 投影；先区分空态与授权失败             |
| Tapper 解释不可用      | 后端跨产品授权和模型配置是否完整；保留明确不可用提示                     |

继续阅读：[后端指引](2026-09-27-backend-developer-onboarding.md)、[客户业务演示](2026-09-04-customer-prototype-demo-guide.md)、[原有领域开发说明](2026-09-13-tapper-developer-guide.md)。

## 知识审核工作台补充

当前长文档筛选、定位标记、授权原件阅读与 XLSX 解析见[知识审核工作台](2026-09-27-knowledge-review-workbench.md)。升级 XLSX 解析时须重建隔离解析镜像，原件读取继续受项目及审核资源权限约束。
