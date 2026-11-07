# Tapper Web 自动化核心闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 控制器统筹，GPT-6 Sol 实施。逐项独立审查，复用已通过验证，复核只看修复范围。

**Goal:** 从 Tapper 自然语言生成并执行通用 Web 自动化，展示结果与证据并重跑。

**Architecture:** 现有 workflow 任务底座 + network-none OpenCode/邮箱模型网关 + TAP 非 AI 浏览器会话/执行权威；完整测试资产管理与复杂恢复后移。

**Tech Stack:** Python 3.13/FastAPI/MySQL、React/TypeScript、OpenCode 1.19.5、Node 22.22.0、Playwright 1.62.1 Chromium、Docker。

**Spec:** [核心设计](../specs/2026-11-07-tapper-web-automation-core-design.md)、[ADR-037](../../decisions/2026-11-07-adr-037-browser-automation-core-first.md)。

## Global Constraints

- 用户已批准核心优先并授权一般工程决定，无须重复确认。不得发布、提交真实业务交易或触碰无关数据。
- 沿用现有 managed worktree，保留未提交文档与真实样本。代码先 TDD，单个实现者；允许审查和只读调查并行。
- 首版不开放 pause/resume/steer/node retry，不把八项旧门禁改成通过；保留隔离、取消、执行端预算。
- 生产代码不能 import scripts 探针。需要复用的纯运行组件移入适当生产模块并保留探针兼容入口，禁止两份分叉实现。
- 模型 key 只在宿主监督端；浏览器无模型 key、Docker socket、编码邮箱或宿主个人目录。只管理带本任务所有权的资源。
- 真实调用使用原 `/private/tmp/tap-opencode-task03-ledger.jsonl` 与原工作区 `.env`，不得打印或复制 key。截至 Task 4 验收累计139calls/USD1.1749068（含一次未知用量的保守上限结算），剩余98calls/USD0.97473，无待结算授权；每次真实验证事先限定预算并结算，不盲目重试。
- 不读取/修改原 `.env.bak-20261015` 或 `apps/tap-ai-backend/.quality/`。默认测试不访问外部网站。用户已允许清理未使用 Docker 镜像缓存，禁止据此清理容器/卷。

## Task 1：固定 Chromium 执行环境与浏览器引擎

**Files:** Create `deploy/browser/{Dockerfile,build-inputs.json,package.json,package-lock.json,inspector.mjs,ctl.mjs,runner.mjs,reporter.mjs,site-proxy.mjs,network-policy.sh}`；Create `scripts/build-browser-runtime.py`；Create `apps/backend/src/tap_platform/browser_automation/{__init__.py,contracts.py,policy.py,docker.py}`；Create `apps/backend/tests/unit/test_browser_runtime.py`、`tests/integration/test_browser_runtime.py`。允许按职责拆小 helper，不改业务界面、workflow 或模型网关。

**Interfaces:** `BrowserRuntime.start_inspector(task_id, allowed_origins, deadline) -> handle`；`observe(handle, action) -> BrowserObservation`；`execute(bundle, inputs, limits) -> BrowserExecution`；`cancel(handle) -> stop_receipt`。这里以协议/注入式 Docker client 隔开真实命令；执行方法 async，命令 argv 固定、JSON 参数 stdin。合同字段按 spec，不在测试中导入 AI 应用。

- [x] RED：路径穿越、symlink、未授权 origin/私网、跨任务资源、缺证据、全 skipped、超时/取消未确认都拒绝。最小例：`assert execution_succeeded(exit_code=0, passed=0, skipped=1, evidence_complete=True) is False`。
- [x] 构建轻量固定 Node slim + Chromium + Playwright1.62.1；只在构建期安装依赖，记录 base/platform/npm/browser/final digest。复用已验证 Linux OpenCode包作为后续构建输入，当前任务无需模型。
- [x] inspector 固定结构化动作，持久 context、真实有界 snapshot/字段/截图；runner 在新 context 执行冻结 `flow.spec.ts`，受信 reporter 输出结果、每测试/步骤状态、截图与 trace。失败不由模型文字覆盖。
- [x] 实现隔离 Docker 生命周期、站点代理、NET_ADMIN-only短命规则设置及先验证后启动。CPU/memory/pids/输出/期限有界；取消确认停止。生成与执行不合并成同一未隔离进程。
- [x] unit 先 RED/GREEN；独立集成命令在自有合成站点验证 navigate/fill/select/result/screenshot/trace、取消和禁止出站/宿主网关/跨任务。读写检查及真实 Chromium sandbox 运行证据必需。不能用 mock 代替本项镜像/浏览器证明。
- [x] 相关 pytest、Node 测试、Ruff/mypy/diff 检查，提交 `feat: add isolated browser runtime`，报告实际镜像摘要与验证命令。无真实模型或用户网站动作。

## Task 2：浏览器资产、执行 API 与持久证据

**Files:** Create `apps/backend/src/tap_platform/browser_automation/{store.py,objects.py,service.py,http.py,worker.py}`；Modify `apps/backend/src/tap_platform/app.py`、访问控制和下一可用 Alembic migration；Create `apps/backend/tests/unit/test_browser_service.py`、`apps/backend/tests/contract/test_browser_api.py`、`apps/backend/tests/integration/test_browser_persistence.py`；Modify `.env.example` 和必要本机启动脚本。

**Interfaces:** 项目作用域 `/api/projects/{project_id}/browser/sessions`、`sessions/{id}/actions`、`bundles`、`executions`、`executions/{id}/cancel`、`evidence/{id}`；service 只接受经过授权主体、不可变摘要和 idempotency key。响应均符合 spec。后台任务使用持久 intent+所有权标签，重复请求先核对；不实现复杂恢复，未知不重发副作用。

- [x] RED：同 key 同内容幂等、异内容冲突、跨项目读写取消/证据拒绝、hash不符拒绝、旧证据不覆盖；重跑新执行ID保持 bundle/input 摘要。
- [x] MySQL 持久 browser_session/bundle/execution/evidence 与状态；内容寻址对象仅允许任务文件，限制体积/MIME，不泄露宿主路径。沿用现有文件对象 store 模式，加入缺对象/不完整证据不能成功测试。
- [x] API/worker 连接 Task1，执行结果原子落库后事件可读；取消请求到实际stopreceipt再终态。读回结果与真实证据，不仅返回内存内容。完成 migration/contract/窄集成测试并提交。

## Task 3：OpenCode 编码段与实际页面工具

**Files:** Create `apps/tap-ai-backend/src/tap/modules/workflows/{ports/coding.py,adapters/opencode_runtime.py,adapters/opencode_profiles.py,adapters/browser_client.py,adapters/coding_mailbox.py}`；Create `deploy/browser/{model-shim.mjs,browserctl.mjs,opencode-client.mjs}`；Move reusable gateway to `apps/tap-ai-backend/src/tap/platform/coding/gateway.py` with probe compatibility import；Modify workflow dispatch/native route/runtime composition/settings and relevant tests.

**Interfaces:** coding start/observe/collect/cancel consume frozen workflow task+browser API client; same OpenCode session/model loop gets browser observations and creates script. A single task BudgetGate spans all generation/repair model requests; `CodingResult` includes provider session, bundle ref, execution ref, usage and terminal evidence. No outer LLM re-planning.

- [x] RED：mailbox请求/响应ID并发隔离、重复/过期/symlink/路径逃逸拒绝；原始正文不进日志制品；network-none编码容器无法直连目标或宿主。`assert replay_model_calls == 0` 在注入式 replay 测试中成立。
- [x] 固定模型 HTTP shim（131072-byte request、1048576-byte response，与生产gateway上限一致）经私有邮箱走宿主 BudgetGate，保留状态/content-type/body，支持原始已验证 bufferedSSE。过期关闭，私有正文处理后删除。provider/小模型均固定LiteLLM，启动配置冻结并 `--pure`。无模型实测默认工具请求32313字节、必要工具裁减后24827字节，真实页面观察后请求13910字节；旧32768字节仅余455字节且无法稳定承载后续上下文，故统一提高到128KiB，费用/调用/期限上限不变。
- [x] `browserctl` 通过邮箱调用 Task2 真实 inspector；主Agent获当前获准专业Agent配置，SDE/SDET可写/执行，QAE只读。未知工具不变全权限。经固定 docker exec 客户端读取serve持久身份/终态，产物摘要校验后提交 Task2 并执行；允许有界修复新版本，保留失败。
- [x] 保留任务预算/lease/cancel；取消杀任务容器后确认停止，费用与调用落同一任务；未知用量保守结算。core能力明确列示，不能将旧完整八门禁标ready。无resume重启能力。
- [x] 相关 contracts/unit/隔离 integration、gateway回归、Ruff/mypy；通过无模型shim+浏览器合成旅程，再用一次限定真实模型调用验证生成与执行。提交并独立审查，不把独立CLI当产品验收。

## Task 4：Tapper 入口、证据与重跑

**Files:** Modify workflow contracts/store/service/http/chat bridge、`apps/tap-ai-frontend/src/features/workflows/{api,components}` 及 Chat 入口；Create artifact fetch adapter；regenerate contracts；frontend tests/e2e。

- [x] 自然语言任务进入同一 Chat/workflow上下文，补充URL/输入/目标不要求手写定位器。冻结browser任务kind及数据；缺必要信息等待输入。
- [x] snapshot投影真实步骤、结果、截图/trace链接和版本摘要；取证据始终服务端校验project作用域，不直连宿主路径。失败、阻塞、取消中、已取消和成功明确区分。
- [x] 服务端 availableActions 与UI均只开放运行中cancel、终态replay；拒绝pause/resume/steer/node retry。replay新task/execution引用同一冻结bundle/input，不触发模型；旧结果可查看。
- [x] 组件和契约 RED/GREEN，`make contracts`，隔离UI验证真实API与持久记录，不用localStorage模拟完成。更新现状架构与运行指南，提交并审查。

## Task 5：核心闭环真实验收与合入

**Files:** Create `scripts/run-browser-core-e2e.py`、隔离合成站点和UI E2E；Create `docs/guides/2026-11-07-tapper-web-automation-core.md`；Modify Makefile/architecture/product roadmap。

- [x] 从Tapper入口在自有站点完成查询提取、表单核对、多页动作，真实浏览器产出可读截图/trace；错误预期失败、缺输入等待、预算/取消停止与跨项目拒绝。至少一个冻结bundle通过产品入口重跑，新ID、旧证据不变、模型调用为0。
- [x] 实现后选择第二站点/新任务，真实模型经相同产品链路生成并执行，不能修改产品代码或预写业务脚本；每个实验事先限定调用/时间/费用并审计旧账本。FWD仅可选询价例，止于报价，不创建投保/付款。
- [x] 首版必需门禁全部有真实证据后，运行 `make check`、`make test` 和独立核心E2E；已通过且树未变化的检查不重跑。一次整体分支审查，修复后只复核改动。更新文档中实际能力/限制，集成本地develop，不push/PR，保留无关未提交文件。
