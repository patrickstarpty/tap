# Tapper 本地知识 Demo 验收评审

> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。

| 字段         | 结论                                                                                                |
| ------------ | --------------------------------------------------------------------------------------------------- |
| 评审对象     | RFC-005 / Tapper 来源优先本地 `doc` 知识问答纵向切片                                                |
| 评审日期     | 2026-08-28                                                                                          |
| 代码证据基线 | `7c70658 + Task 10 acceptance patch`                                                                |
| 验收范围     | Python/FastAPI、React/Vite、MySQL/Redis/Azurite/Milvus/LiteLLM、Playwright、持久化与文档生命周期    |
| 当前结论     | **approved / GREEN**：mandatory deterministic/local-middleware gate 与实际手工视觉/键盘验收全部通过 |
| 真实模型     | `not-run: credentials not provided`；runnable LiteLLM route configured, provider unverified         |
| 生命周期     | RFC-005 为 `implemented`；Tapper 计划为 `completed`                                                 |

## 执行摘要

Tasks 1–9 的 runnable vertical slice 已交付并通过独立正式评审。Task 10 增加默认关闭的生产 LiteLLM 路径 smoke、完成开发/架构/契约文档同步，并在隔离的 `tap-tapper-e2e` 项目上取得新鲜自动化、跨应用/Compose 重启的文档与 ingestion/index 状态恢复、精确清理和手工视觉/键盘证据。Contract P1 预审指出的公共路径参数 casing 与稳定 problem type 漏项也已按 generated OpenAPI 和代码 truth 修正；再生成契约无 diff。

本 checkout 没有 `.env`。因此真实 provider 网络路径没有执行，准确状态为 `not-run: credentials not provided`。固定 `tapper-chat` / `tapper-embedding` LiteLLM 路由和 opt-in smoke 已配置，但真实 provider smoke 仍未验证；deterministic fake E2E 与默认 intentional skip 都不能替代该结论。

## 验收范围与已知限制

验收只覆盖精确 loopback 上的本地 Tapper：文本可提取 PDF/DOCX/MD/TXT、25 MiB 单文件、50 份未删除文档、一次回答最多 20 份 ready 来源、单次非流式 grounded answer 和 citation preview。它不覆盖身份验证、OCR/PPTX/Web sync、Conversation/history/SSE/stop/queue/Trace/Feedback、共享/生产部署、Entra/企业 ACL、Azure AI Search 四 family 或 `code/bdd/failure` 投影。

数据边界为 MySQL 权威事实、Redis 可重建分发、Azurite 原件/派生 artifact、本地 Milvus `doc` 投影和 LiteLLM 固定 alias。页面刷新、应用重启与普通 Compose `down/up` 恢复的是文档目录与来源可用状态、ingestion/index 状态和 citation resolver 所需的持久事实；当前渲染回答只在 Web 页面内存中，刷新会清空，本版不提供历史回答恢复，但可基于持久的 `ready` 来源重新提问。只有 exact project `tap-tapper-demo` 且显式设置 `TAP_ALLOW_TAPPER_VOLUME_RESET=1` 的 `demo-reset` 可以不可逆删除具名卷。

## 可执行 isolated full-test harness

下列脚本是 `test-sanitized.log` 的 `COMMAND_BEGIN` / `COMMAND_END` envelope 原文。把它保存为 `.superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh` 后，从仓库根目录执行 `bash .superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh`。它在启动前拒绝已有 exact-project 资源和占用端口，只用 literal `docker compose -f compose.yaml -p tap-tapper-e2e` 启动 MySQL/Redis，使用 `13306/16379`，迁移到 head，显式打开两项 integration gate，并在任何退出路径执行 exact-project volume cleanup。数据库 URL 的 scheme、authority、path 与 query 在 shell 内分段组装；普通 `make test` 只通过 `env -u TAP_TAPPER_COMPOSE_PROJECT` 调用，不继承 E2E project 环境。

```bash
#!/usr/bin/env bash
set -eu

test_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$test_repo_root"

test_existing_containers="$(docker ps -aq --filter label=com.docker.compose.project=tap-tapper-e2e)"
test_existing_volumes="$(docker volume ls -q --filter label=com.docker.compose.project=tap-tapper-e2e)"
test_existing_networks="$(docker network ls -q --filter label=com.docker.compose.project=tap-tapper-e2e)"
if [ -n "$test_existing_containers$test_existing_volumes$test_existing_networks" ]; then
  echo "Refusing to reuse an existing tap-tapper-e2e Compose project." >&2
  exit 2
fi

for test_port in 13306 16379; do
  if lsof -nP -iTCP:"$test_port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Refusing occupied isolated test port $test_port." >&2
    exit 2
  fi
done

test_mysql_root_password="$(openssl rand -hex 24)"
test_mysql_password="$(openssl rand -hex 24)"
test_async_scheme='mysql+asyncmy://'
test_alembic_scheme='mysql+pymysql://'
test_database_authority="tap:${test_mysql_password}@127.0.0.1:13306"
test_database_path='/tap'
test_query_separator='?'
test_query_value='charset=utf8mb4'
test_database_url="${test_async_scheme}${test_database_authority}${test_database_path}${test_query_separator}${test_query_value}"
test_alembic_url="${test_alembic_scheme}${test_database_authority}${test_database_path}${test_query_separator}${test_query_value}"
test_redis_url='redis://127.0.0.1:16379/0'

test_cleanup() {
  test_status=$?
  trap - EXIT HUP INT TERM
  set +e
  docker compose -f compose.yaml -p tap-tapper-e2e down --volumes --remove-orphans
  test_cleanup_status=$?
  set -e
  if [ "$test_status" -ne 0 ]; then
    exit "$test_status"
  fi
  exit "$test_cleanup_status"
}

trap test_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

MYSQL_PORT=13306 \
REDIS_PORT=16379 \
MYSQL_ROOT_PASSWORD="$test_mysql_root_password" \
MYSQL_DATABASE=tap \
MYSQL_USER=tap \
MYSQL_PASSWORD="$test_mysql_password" \
docker compose -f compose.yaml -p tap-tapper-e2e up -d --wait --wait-timeout 180 mysql redis

TAP_ALEMBIC_DATABASE_URL="$test_alembic_url" \
uv run --project apps/backend alembic -c apps/backend/alembic.ini upgrade head

TAP_RUN_MYSQL_INTEGRATION=1 \
TAP_RUN_REDIS_INTEGRATION=1 \
TAP_DATABASE_URL="$test_database_url" \
TAP_ALEMBIC_DATABASE_URL="$test_alembic_url" \
TAP_REDIS_URL="$test_redis_url" \
env -u TAP_TAPPER_COMPOSE_PROJECT make test
```

## 自动化证据表

所有 SHA-256 都对应 ignored、sanitized evidence artifact；日志包含 `COMMAND` / `EXIT` envelope，严格扫描未发现未脱敏 AccountKey、URI userinfo 或 query string。

| Gate                                                            | 精确命令                                                                                                                                                                             | Exit      | Passed / skipped                                                                                                                                                                   | Sanitized artifact SHA-256                                                                                                                               | 结果                                                                                                                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frozen bootstrap                                                | `make bootstrap`                                                                                                                                                                     | `0`       | 70 Python packages audited；skip 不适用                                                                                                                                            | `4ff0ed86576bdcc071d747c7d48c991e53b5f14ee47469391bc2f0e4137a33aa`                                                                                       | GREEN；workspace dependencies current                                                                                                                      |
| Contract generation + clean generated diff                      | `make contracts && git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/`                                                                                            | `0`       | generated diff `0`；skip 不适用                                                                                                                                                    | `8b958bd102bfbed2d282189de991fa54a874b14137952d86d2f6a1b37b465b4e`                                                                                       | GREEN                                                                                                                                                      |
| Backend unit/contract/integration + Web component               | `bash .superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh`（完整命令见上节）                                                               | `0`       | Backend `1883 passed / 9 skipped / 2 warnings`；Web `10 files / 127 passed`                                                                                                        | `3f503c7296aaaadfdcb54a18f709ed5ad49de6395572afb9faad4ba9de90a024`                                                                                       | GREEN；两条 warning 均为 Alembic `path_separator` deprecation；isolated containers/volumes/networks/listeners/locks 均为 `0`                               |
| Python/Web lint/type/architecture/contracts/Web build           | `env -u TAP_TAPPER_COMPOSE_PROJECT make check`                                                                                                                                       | `0`       | Ruff pass；format `153` files；mypy `87` source files；max JS `415.03 kB`；chunk warnings `0`                                                                                      | `cc003161d16ce301af22b387340d222107c660a10b94debd7f35764449a89d3c`                                                                                       | GREEN                                                                                                                                                      |
| 默认真实模型 smoke collection                                   | `env -u TAP_RUN_TAPPER_REAL_MODEL_SMOKE uv run --project apps/backend pytest apps/backend/tests/smoke/test_tapper_real_model.py -v -rs`                                              | `0`       | `0 passed / 1 skipped`；精确 reason：`real Tapper model smoke requires explicit opt-in`                                                                                            | `56dcac77365ca6044ae43df6a20febb1e4368f9d6c40b8b9c3bf4e5345589223`                                                                                       | GREEN（默认关闭契约）；provider 未调用                                                                                                                     |
| 四格式上传与来源问答 Playwright                                 | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；journey phase 覆盖 PDF/DOCX/MD/TXT、grounded claims 与 citation resolution                                                                          |
| 重复上传与并发幂等                                              | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`<br>`bash .superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh`                | 均为 `0`  | E2E：`3` 个 fail-closed Playwright phases，verifier `12 passed / 0 skipped / 2 warnings`<br>Full test：Backend `1883 passed / 9 skipped / 2 warnings`，Web `10 files / 127 passed` | E2E：`b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`<br>Full test：`3f503c7296aaaadfdcb54a18f709ed5ad49de6395572afb9faad4ba9de90a024` | GREEN；Playwright 直接证明重复上传复用 document identity/job；full test 的 document-ledger integration 证明 renamed duplicate 与 capacity-lock concurrency |
| parsing / embedding / publishing fail-once + retry              | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；三个失败阶段均从正确 checkpoint 恢复                                                                                                                |
| 取消来源三层零命中 + citation tamper 拒绝                       | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`<br>`bash .superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh`                | 均为 `0`  | E2E：`3` 个 fail-closed Playwright phases，verifier `12 passed / 0 skipped / 2 warnings`<br>Full test：Backend `1883 passed / 9 skipped / 2 warnings`，Web `10 files / 127 passed` | E2E：`b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`<br>Full test：`3f503c7296aaaadfdcb54a18f709ed5ad49de6395572afb9faad4ba9de90a024` | GREEN；Playwright 证明取消来源后的三层零命中；full test 的 `test_citation_snapshot_transaction.py` 证明 missing/anchor/hash manifest tamper 均 fail closed |
| 删除后的 Blob/manifest/Milvus 清理                              | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；删除后 projection 与 artifact 清理断言通过                                                                                                          |
| API/Web/Worker application restart persistence                  | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；application-restart phase 恢复文档、ingestion/index 与必要可重建状态；不声称恢复上一回答正文/history                                                |
| 普通 Compose down/up volume persistence                         | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；compose-restart phase 与 exact-state verifier 恢复文档、ingestion/index 与必要可重建状态；不含回答正文/history 恢复                                 |
| API/Web/中间件 loopback binding 与 exact project cleanup        | `env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e`                                                                                                                        | `0`       | `3` 个 fail-closed Playwright phases；persistence verifier `12 passed / 0 skipped / 2 warnings`                                                                                    | `b902208178d50f545e881ae1d2cf831968219bba44d3df50539e92b60ffb5024`                                                                                       | GREEN；exact-project cleanup exit `0`                                                                                                                      |
| Whitespace / patch hygiene                                      | `git diff --check`                                                                                                                                                                   | `0`       | output lines `0`；skip 不适用                                                                                                                                                      | `e148137371c9cac08ce16bda72fe790cac02b3c5a6f3ffd59acbc1b3a803aff1`                                                                                       | GREEN                                                                                                                                                      |
| Real-provider embedding + grounded answer + citation resolution | `set -a; . ./.env; set +a; TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1 uv run --project apps/backend pytest apps/backend/tests/smoke/test_tapper_real_model.py -v -rs`（授权后可运行，未执行） | `not-run` | `not-run`                                                                                                                                                                          | `not-run`                                                                                                                                                | `not-run: credentials not provided`；runnable LiteLLM route configured, provider unverified                                                                |

E2E 只使用 exact Compose project `tap-tapper-e2e` 与固定 loopback ports：MySQL `13306`、Redis `16379`、Azurite `11000`、LiteLLM `14000`、Milvus `29530/19091`、API `18000`、Web `15173`。最终清理核对为 containers `0`、volumes `0`、networks `0`、八个固定端口 listener 全部 `0`、lock directories `0`；默认/shared middleware 未触碰。

## 手工视觉与键盘证据

应用内 Browser runtime 在按要求排查目标 URL 连接后仍不可用，因此本次透明使用 repository-pinned Chromium fallback。它实际启动并验收本地 full stack，命令为 `TAP_MANUAL_REPO_ROOT=<worktree> corepack pnpm --filter @tap/web exec node <ignored-evidence>/manual-browser-fallback.mjs`，exit `0`；不声称使用了 in-app Browser。

Manual log SHA-256 为 `9f66bcda6e5c637d2210a22b27e9abdd372bcfc2fd42d060ce1180307c224f31`；结构化 result JSON SHA-256 为 `33fdfd8edb17c1cda00b7611edff5072ac73d32cdcff2b65e0254dcdab72c3b5`。

| Viewport          | 检查项                                                                                                                                                                                             | 证据                                                                                                                                                                                                                           | 结果     |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| Desktop `1440 px` | 来源优先三栏、11 个 keyboard focus stops、visible focus、键盘 tab/上传、labelled input/checkbox、六阶段完成、grounded claims、inline citation keyboard open/close + focus restore、无横向 overflow | `manual-desktop-before-answer.png` SHA-256 `9cad718641ee347ef95eb7fa0512fb5237ea69961e644dc5bd77299aacdfc4cb`；`manual-desktop-citation-masked.png` SHA-256 `7c0765321fae942aefc4b618b7f43ae0bdb60cfd57a262f20cde49c2981baf1a` | **PASS** |
| Mobile `390 px`   | source → question → citation 顺序、长中英文 wrap、citation viewer、无横向 overflow                                                                                                                 | `manual-mobile-390-citation-masked.png` SHA-256 `0731af224df079306ab2033a6a1f5c38dee75e9347ad63ce57388e66f8c644d5`                                                                                                             | **PASS** |

Runtime audit 同时取得 console issues `0`、page issues `0`、unexpected request issues `0`、external browser requests `0`。回答、问题与 citation quote 已在 post-answer screenshots 中遮罩；证据未记录 credential、provider payload、endpoint、raw request ID、vector 或原始正文。

## 生命周期结果

Mandatory deterministic/local-middleware 行和两种 viewport 的实际手工验收均为 GREEN，因此按治理规则完成且只完成下列转换：

| 文档                          | 转换前               | 转换后        | 处理结果 |
| ----------------------------- | -------------------- | ------------- | -------- |
| RFC-005                       | `accepted`           | `implemented` | 已转换   |
| Tapper plan                   | `active`             | `completed`   | 已转换   |
| plans index / proposals index | 与源文档一致         | 同步目标状态  | 已同步   |
| Phase 1 plan                  | `active`             | `active`      | 保持不变 |
| RFC-003 / RFC-004             | `accepted` / `draft` | 不变          | 保持不变 |
| 全部 ADR                      | 原状态与语义         | 不变          | 未修改   |

可选真实模型 smoke 未运行不阻止本地 deterministic acceptance；本结论只批准 local/no-auth/no-OCR Tapper slice，不表示真实 provider、完整 Knowledge Chat、企业四索引 RAG 或 Phase 1 已验证完成。

## 最终复现清单

`.superpowers/` 证据目录按治理规则不会进入提交，新 clone 不能假设 `full-test-harness.sh` 已存在。执行下列清单前，必须先创建对应 ignored 目录，并将本评审“可执行 isolated full-test harness”一节的完整 `bash` 代码块原样保存为 `.superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh`；然后才执行清单中的 `bash` 命令。sanitized ledger 同时记录该调用的显式 `COMMAND=` 行以及逐字脚本 `COMMAND_BEGIN` / `COMMAND_END` envelope。

```sh
make bootstrap
make contracts
git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/
env -u TAP_TAPPER_COMPOSE_PROJECT make check
bash .superpowers/sdd/2026-08-27-tapper-local-knowledge-demo/task-10-evidence/full-test-harness.sh
env TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-e2e make demo-e2e
env -u TAP_RUN_TAPPER_REAL_MODEL_SMOKE uv run --project apps/backend pytest \
  apps/backend/tests/smoke/test_tapper_real_model.py -v -rs
git diff --check
```

生命周期回填后再次执行 stale-claim scan、相对链接验证、contract regeneration clean diff 与 `git diff --check`；结果记录在 ignored Task 10 implementer report。
