# Tapper 浏览器自动化本地运行

本指南使用现有的本地受信项目 scope。浏览器能力默认关闭；启用前需为 Browser API、workflow 和 LiteLLM 分别配置服务，并使用项目相同的 `TAP_BROWSER_PROJECT_ID` 与访问 token。浏览器子进程只收到 `scripts/run-tap-browser.sh` 选定的 Browser API 变量，不收到模型密钥。

## 准备服务和镜像

1. 按现有 Tapper 本地流程启动 MySQL、Redis、MinIO、LiteLLM 等依赖。为 TAP AI workflow 和 TAP Browser API 分别配置数据库与迁移：AI 使用 `TAP_DATABASE_URL=mysql+asyncmy://…` 执行 `make tap-ai-migrate`；Browser API 使用单独数据库的 `TAP_BROWSER_DATABASE_URL=mysql+pymysql://…` 执行 `TAP_DATABASE_URL="$TAP_BROWSER_DATABASE_URL" make tap-backend-migrate`。不要把两个 URL 指向同一库。
2. 在 arm64 主机构建浏览器镜像：`python3 scripts/build-browser-runtime.py`。验收镜像 ID 为 `sha256:516c8d85a71714bb7ee24510cb0b57f05ab7c7d61323597f1c0ebe98ae0ed9f8`；若本机重建 ID 不同，先核对锁定输入和实际构建报告，不能悄悄替换运行时 pin。
3. 提供经校验的 OpenCode 1.19.5 Linux arm64 可执行文件，再运行 `python3 scripts/build_coding_image.py --opencode-binary /path/to/opencode`。构建器核对该文件 SHA-256 `dab85b46f4ea4813da5367997f99a60aab64a2118d58ebf552980e792747436c` 和浏览器基础镜像 ID；当前编码镜像 ID 为 `sha256:1e3b18e1f38b56fe9b67564593ad31ab2807ad61c07de6fc55bded7128aa4a0b`。被忽略的本机开发目录不是产品启动依赖。

## 配置和启动

在私有本地环境中按 `.env.example` 配置 `TAP_WORKFLOW_ENABLED=true`、`TAP_BROWSER_CODING_ENABLED=true`、正数有限的 `TAP_WORKFLOW_MAX_COST_MICROS`、`TAP_BROWSER_API_ORIGIN=http://127.0.0.1:8002`、`TAP_BROWSER_PROJECT_ID`、`TAP_BROWSER_ACCESS_TOKEN`、到期时间、独立浏览器数据库 URL 与私有对象根目录。配置 `TAP_BROWSER_IMAGE`、`TAP_BROWSER_CODING_IMAGE` 和上面的精确 `TAP_BROWSER_CODING_IMAGE_ID`，以及从批准的 LiteLLM `/model/info` 取得的 `TAP_BROWSER_CODING_MODEL_ALIAS`、输入/输出百万 token 价格和模型接入参数。原生 workflow 的历史零费用上限不适用于浏览器任务；浏览器缺任何必需配置时不展示可用能力。项目 ID 必须匹配当前 Tapper 项目。

默认 `TAP_BROWSER_SERVICE_MODE=managed` 时运行 `make tap-ai-dev`：`run-tapper-dev.sh` 启动独立 Browser API、Browser worker、TAP AI API/worker 与 Web，并在退出时只清理自己启动的子进程。若 Browser API/worker 已由外部监督器管理，设 `TAP_BROWSER_SERVICE_MODE=external` 后再运行同一命令；脚本检查配置的 Browser API 健康端点，但不会启动、停止或竞争外部 worker。正式 worker 保持公共网络访问策略，不接受 HTTP 请求指定私网测试目标。

在 Chat 勾选 **Browser run**，输入自然语言目标和起始 URL；若目标中已有明确 URL，表单会自动带入并允许修改。预期结果默认取目标，输入资料可用普通文字填写。缺 URL 会在发送前提示且保留目标。提交后在会话侧栏或 **Agents → Runs** 查看持久任务、步骤、结果和截图/trace；刷新后仍从 API 读取。运行中可取消，终态可 **Replay**，新任务在 Runs 显示来源任务，沿用冻结 bundle 与输入且不再调用模型。执行中若返回 `input_request`，应提供补充资料创建新的冻结请求；当前接口不支持原执行的 resume/steer。

隔离自有站验收可由测试监督器在确认自己的容器 ID/labels 并从 inspect 取得地址后，构造 `BrowserRuntime(test_target=固定owned地址)`；这只属于测试 composition，不改变上述正式启动或开放 HTTP 私网目标。完整真实模型产品流程和跨站泛化验收另行记录。
