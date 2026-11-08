# Tapper 浏览器自动化本地运行

本指南使用现有的本地受信项目 scope。浏览器能力默认关闭；启用前需为 Browser API、workflow 和 LiteLLM 分别配置服务，并使用项目相同的 `TAP_BROWSER_PROJECT_ID` 与访问 token。浏览器子进程只收到 `scripts/run-tap-browser.sh` 选定的 Browser API 变量，不收到模型密钥。

## 准备服务和镜像

1. 按现有 Tapper 本地流程启动 MySQL、Redis、MinIO、LiteLLM 等依赖。为 TAP AI workflow 和 TAP Browser API 分别配置数据库与迁移：AI 使用 `TAP_DATABASE_URL=mysql+asyncmy://…` 执行 `make tap-ai-migrate`；Browser API 使用单独数据库的 `TAP_BROWSER_DATABASE_URL=mysql+pymysql://…` 执行 `TAP_DATABASE_URL="$TAP_BROWSER_DATABASE_URL" make tap-backend-migrate`。不要把两个 URL 指向同一库。
2. 在 arm64 主机构建浏览器镜像：`python3 scripts/build-browser-runtime.py`。当前构建镜像 ID 为 `sha256:60cbf6735f945b534fb0b8aca25e6ee6f257fcb6ee051231c5f2812e5b3a8dce`；若本机重建 ID 不同，先核对锁定输入和实际构建报告，不能悄悄替换运行时 pin。
3. 提供经校验的 OpenCode 1.19.5 Linux arm64 可执行文件，以及 [ripgrep 15.1.0 官方 aarch64 GNU 发行归档](https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-aarch64-unknown-linux-gnu.tar.gz)，运行 `python3 scripts/build_coding_image.py --opencode-binary /path/to/opencode --ripgrep-archive /path/to/ripgrep-15.1.0-aarch64-unknown-linux-gnu.tar.gz`。构建器核对 OpenCode SHA-256 `dab85b46f4ea4813da5367997f99a60aab64a2118d58ebf552980e792747436c`、ripgrep 官方归档 SHA-256 `2b661c6ef508e902f388e9098d9c4c5aca72c87b55922d94abdba830b4dc885e` 和浏览器基础镜像 ID；当前编码镜像 ID 为 `sha256:c56cac0aa6951e9dc2ab9ecbf62c3ee349d6b283c231659bb4e96e806b12c8c5`。被忽略的本机开发目录不是产品启动依赖。

## 配置和启动

在私有本地环境中按 `.env.example` 配置 `TAP_WORKFLOW_ENABLED=true`、`TAP_BROWSER_CODING_ENABLED=true`、正数有限的 `TAP_WORKFLOW_MAX_COST_MICROS`、`TAP_BROWSER_API_ORIGIN=http://127.0.0.1:8002`、`TAP_BROWSER_PROJECT_ID`、`TAP_BROWSER_ACCESS_TOKEN`、到期时间、独立浏览器数据库 URL 与私有对象根目录。配置 `TAP_BROWSER_IMAGE`、`TAP_BROWSER_CODING_IMAGE` 和上面的精确 `TAP_BROWSER_CODING_IMAGE_ID`，以及从批准的 LiteLLM `/model/info` 取得的 `TAP_BROWSER_CODING_MODEL_ALIAS`、输入/输出百万 token 价格和模型接入参数。原生 workflow 的历史零费用上限不适用于浏览器任务；浏览器缺任何必需配置时不展示可用能力。项目 ID 必须匹配当前 Tapper 项目。

默认 `TAP_BROWSER_SERVICE_MODE=managed` 时运行 `make tap-ai-dev`：`run-tapper-dev.sh` 启动独立 Browser API、Browser worker、TAP AI API/worker 与 Web，并在退出时只清理自己启动的子进程。若 Browser API/worker 已由外部监督器管理，设 `TAP_BROWSER_SERVICE_MODE=external` 后再运行同一命令；脚本检查配置的 Browser API 健康端点，但不会启动、停止或竞争外部 worker。正式 worker 保持公共网络访问策略，不接受 HTTP 请求指定私网测试目标。

Browser API 需迁移至 `0008_browser_network_mode`。新任务默认允许公开网站和页面依赖，无需列出域名；冻结包保存策略，旧包继续使用原来源限制。本机、私网和云元数据地址仍被隔离。

在 Chat 直接输入自然语言目标、网址和测试资料，Tapper 在已启用的能力中自动选择执行方式，不需要勾选 Browser run 或指定 Agent。也可展开 **Execution options** 手选 Browser。自动路由缺少明确 URL 时会先询问，不会猜测网站。浏览器动作前先保存 Given/When/Then 计划，并随实际操作和校验更新进度。提交后从 **View progress** 或 **Agents → Runs** 查看持久任务：默认显示 BDD 步骤及 Outputs、Subagents、Sources；展开结果、业务步骤或 Run details 查看截图、trace、定位器、耗时和脱敏错误；刷新后仍从 API 读取。运行中可取消，终态可 **Replay**，新任务在 Runs 显示来源任务，沿用冻结 bundle 与输入且不再调用模型。执行中若返回 `input_request`，应提供补充资料创建新的冻结请求；当前接口不支持原执行的 resume/steer。

隔离自有站验收可由测试监督器在确认自己的容器 ID/labels 并从 inspect 取得地址后，构造 `BrowserRuntime(test_target=固定owned地址)`；这只属于测试 composition，不改变上述正式启动或开放 HTTP 私网目标。自有站和独立公共站的有界真实模型核心闭环结果、历史失败及适用边界见[核心验收指南](2026-11-07-tapper-web-automation-core.md)；上述能力仍须按本指南配置并显式启用，当前共享应用没有自动开启。
