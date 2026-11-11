# Tapper BDD Executor Implementation Plan

> **For agentic workers:** Use executing-plans inline. The user requested the primary agent to implement personally; do not delegate.

**Goal:** Execute validated BDD contracts in order, reject out-of-contract actions, and compile confirmed receipts into zero-model replay.

**Architecture:** A pure contract/state module owns validation and transitions. Browser-use supplies current-page targeting through restricted TAP actions; Browser API guards and executes writes. The existing compiler produces scripts from normalized confirmed records.

**Tech Stack:** Existing Python/Pydantic, browser-use 0.11.13, Playwright 1.62.1; no new dependency.

**Spec:** ../specs/2026-11-08-tapper-bdd-executor-design.md

## Global Constraints

- No additional paid run until local gates pass; preserve failed/canceled recordings and settle pending usage.
- No site-specific selectors, values or prices in product code.
- Preserve shared services and existing uncommitted changes; use the existing isolated worktree.
- Frozen inputs/assertions cannot be edited by execution decisions. Unknown dispatched writes stop without retry.

## Task 1: Contract validation and deterministic state

Files: create `apps/tap-ai-backend/src/tap/modules/workflows/application/bdd_executor.py` and `apps/tap-ai-backend/tests/unit/workflows/test_bdd_executor.py`.

Interfaces: `BDDPlan` Pydantic schema; `BDDExecutor(plan, start_url)` with `current`, `digest`, `authorize(step_id, digest)`, `accept(action, receipt)`, `failed(signature, error)` and `observe()`; normalized confirmed records usable by `DirectBrowserResult`.

- [x] Write red tests constructing a plan with navigation, When fill and Then equals, and a result assertion. Verify missing/duplicate requirement references fail, legitimate When/Then passes, wrong digest/step is rejected, failure does not advance, unknown receipt stops, three failures and observation quota stop.
- [x] Implement schema validation, frozen plan copy/digest, ordered state and bounded failure counters. Plan operation enum excludes artifact work. Then assertions come only from frozen plan.
- [x] Run the new test module and Ruff/mypy; inspect actual negative failures before implementing.

## Task 2: Guarded browser operations and SDK integration

Files: `browser_use_runtime.py`, `deploy/browser/page-actions.mjs`, `deploy/browser/inspector.mjs`, existing browser/runtime tests.

Interfaces: `guardTarget(page, action)` checks frozen `target_texts` and optional `scope_text` against the actual element; `tap_execute(step, plan_digest, index?, selector?)` builds actions from the contract. Read-only observation remains separately bounded.

- [x] Add real-browser tests proving wrong label/scope rejects before a click, word boundaries distinguish Male/Female, readonly selection uses the requested value, and Then rejects mutation.
- [x] Add adapter tests for plan review rejection before business actions, current-step-only tools, automatic advancement by confirmed receipts and assertions, stale plan refusal and no native action bypass.
- [x] Implement bounded planning/review; use only restricted TAP actions and safe observation tools. Resolve SDK index to locator, guard in the service, execute through Browser API and emit existing progress events.
- [x] Run targeted Python and real-browser tests, then inspect the produced action sequence.

## Task 3: Deterministic replay from confirmed records

Files: `direct_browser.py`, `browser_use_runtime.py`, compiler tests.

Interface: `compile_replay(DirectBrowserResult) -> str`; unsupported operations raise `ValueError`, never disappear. BrowserUseRuntime delegates to this compiler without coding/model calls.

- [x] Write red tests for unsupported action, frozen target/assertion preservation, artifact source mapping, and compiler invocation with a gateway that fails if called.
- [x] Implement required operation/attribute handling and source comments. Preserve actual DOM reads; do not embed observed quote constants as outputs.
- [x] Run generated script on a local dynamic fixture and compare real original/replay outputs and action order.

## Task 4: Integration gates and acceptance

Files: affected architecture/ADR/roadmap and existing isolated E2E harness.

- [x] Run affected tests then `make test`, Ruff/mypy and `git diff --check`; review only new failures before widening checks.
- [x] Update implementation status and limitations in architecture, ADR-038 and roadmap; do not relabel older evidence.
- [ ] After local gates pass, run bounded FWD and AIA full flows, inspect outputs, script and both videos; settle every run. No repeated paid attempt without a distinct reproduced local fix.
- [ ] Report passed gates, remaining blockers and exact evidence; integrate only accepted changes without unrelated files.

## 本轮验收记录

- 本地门禁：最终 49 项定向 Python、6 项真实浏览器测试、606 项核心回归通过，Ruff 和相关类型检查通过；包含按操作区分的 Schema 回归。确定性脚本在新页面重放 readonly 选择与动态报价，结果及 BDD 顺序一致。
- FWD 新架构运行 `57db5af372464b77aaf9695472a372d0` 在计划阶段停止，尚未执行网页业务动作；导航步骤带入了不允许的目标字段，格式纠正一次后仍相同。真实返回已用于本地 Schema 回归；修正尚未付费重跑验证。本轮 3 次调用，实际费用 $0.030611498。
- AIA 在独立 Compose 环境启动阶段超时，应用和浏览器服务均未启动，0 次模型调用、费用 $0。用量预留已结算。
- Docker 拒绝停止本轮 `tap-browser-core-d43e96a30d6f` 的 mysql、redis、litellm 容器（终止信号后未收到退出事件）；保留其私有状态目录，未重启共享 Docker 环境。专用模型网关已停机并删除临时凭据。
- FWD/AIA 仍未完成业务、录像与独立回放验收，代码尚未提交或合并。恢复公共验收前需先恢复 Docker 状态；不得把上述本地通过记录当成公共站验收。
