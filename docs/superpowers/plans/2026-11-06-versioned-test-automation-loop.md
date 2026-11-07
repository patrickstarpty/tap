# 版本化测试资产与自动化执行闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 需求经过 Agent 工作流生成独立版本的测试计划和用例，产出可发布的自动化代码，在隔离容器实际执行并回写可追溯结果。

**Architecture:** TAP 非 AI 后端持有测试资产、制品和执行事实；TAP AI 通过版本化 HTTP 提交候选、查询执行结果并关联工作流。先解决旧 AI 测试计划的单写迁移，再实现步骤/代码制品、人工发布、执行器和证据投影。

**Tech Stack:** TAP Python/FastAPI/SQLAlchemy/MySQL、MinIO、Playwright 1.62.1 + TypeScript、本机 Docker、两套现有 React 前端。

**Spec:** [整体设计](../specs/2026-11-06-agent-workflows-and-test-loop-design.md) §8–11；[总计划与路径缩写](2026-11-06-agent-workflows-and-test-loop.md)。

## 当前交付顺序

2026-11-07 用户明确批准先交付整体 spec §11.2 的通用 Web 核心闭环；完整测试计划/用例资产管理、发布与迁移随后实施。本文件保留完整闭环要求，未完成项不得计为已交付。核心首版复用现有任务和产物边界，不建立第二个正式测试资产主写入口。

## Global Constraints

- 阶段 3 的领域归属是待用 ADR 固化的实施决策；在该决策明确前不新建第二个资产主写入口。
- 独立 PlanRevision 与 CaseRevision；计划固定引用用例版本；Run 固定计划、用例、自动化修订、代码/环境/数据摘要。
- 普通动作确定性生成 Playwright；SDET 提供的 helper 必须显式版本化绑定并进入同一制品摘要；禁止直接改生成文件造成步骤/代码分叉。
- 发布需要人工确认具体摘要，来源权限/有效性重验；任何后续修改使旧审核不适用于新版本。
- 确定性回归使用自有、无租户数据的可复现 Web 示例；按整体 spec §11.1，独立 opt-in 的真实业务验收覆盖 FWD HK MyTerm Plus 合成资料询价，止于报价。不得扩展为生产投保平台或偷偷执行导入 Skill 脚本。
- 本机隔离执行器与编码环境分离；测试容器无模型 key、宿主目录或 Docker socket，worker 在容器外管理执行。
- 不伪造 Git commit、真实执行、截图、trace 或模型验证；旧模拟运行保持明确标识，不进入真实结果统计。
- 旧失败与发布记录不可覆盖；脚本修复新版本、新发布、新 Run。产品缺陷与证据不足不能自动改断言变绿。

## 文件边界

| 区域 | 新文件 |
| --- | --- |
| `P/test_management/` | `domain/models.py`、`application/assets.py`、`application/migration.py`、`adapters/mysql.py`、`contracts.py`、`http.py` |
| `P/automation/` | `domain/steps.py`、`domain/bundles.py`、`application/generate.py`、`application/publish.py`、`adapters/mysql.py`、`adapters/objects.py`、`contracts.py`、`http.py` |
| `P/execution/` | `domain/models.py`、`ports.py`、`application/service.py`、`application/results.py`、`adapters/mysql.py`、`adapters/docker.py`、`contracts.py`、`http.py`、`worker.py` |
| `B/modules/workflows/adapters/test_assets.py` | AI 到 TAP 的 HTTP 候选与结果适配 |
| `W/features/testManagement/`、`W/features/automation/` | 正式资产/制品/运行历史 API 与界面，替换原型模拟路径 |
| `deploy/execution/` | 固定运行镜像、reporter 与构建输入 |

## 窄测命令与隔离 fixture

阶段 3.2 创建 `PT/conftest.py` 的 `owned_tap_mysql` fixture：仅在 `TAP_RUN_AUTOMATION_INTEGRATION=1` 时创建独立本机 MySQL 容器和数据库，用 TAP Alembic 链升级，退出时只删除自己标签的容器；禁止复用可指向共享库的 `TAP_DATABASE_URL`。阶段 3.4 在同一文件增加 `owned_tap_minio`，阶段 3.5 增加 `owned_execution_network`；均沿用该 opt-in。现有 report recovery 的 `TAP_TASK9_MYSQL_URL` 不自动接管，旧测试单独保留。

以下命令分别在任务红、绿阶段运行；集成测试首次缺少实现时应失败，缺少 opt-in 只能 skip。新测试 fixture 不依赖另一应用的 Python import。

```bash
uv run --project apps/backend pytest apps/backend/tests/contract/test_test_asset_contracts.py -q
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_test_asset_client.py -q
uv run --project apps/backend pytest apps/backend/tests/unit/test_asset_versions.py -q
uv run --project apps/backend pytest apps/backend/tests/unit/test_automation_codegen.py apps/backend/tests/contract/test_automation_versions.py -q
uv run --project apps/backend pytest apps/backend/tests/unit/test_bundle_publish.py -q
uv run --project apps/backend pytest apps/backend/tests/unit/test_execution_state.py -q
uv run --project apps/backend pytest apps/backend/tests/unit/test_execution_results.py -q
TAP_RUN_AUTOMATION_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_asset_migration.py apps/backend/tests/integration/test_bundle_store.py apps/backend/tests/integration/test_execution_recovery.py apps/backend/tests/integration/test_execution_writeback.py -q
```

### Task 3.1：领域归属、跨应用合同与迁移规则

**Files:** Create `docs/superpowers/specs/2026-11-06-versioned-test-automation-loop-design.md`、`docs/decisions/2026-11-06-adr-036-test-asset-authority-and-local-execution.md`；Modify ADR-025、`docs/decisions/index.md`；Create `P/test_management/contracts.py`、`PT/contract/test_test_asset_contracts.py`、`B/modules/workflows/adapters/test_assets.py`、`BT/contract/test_test_asset_client.py`。

**Interfaces:** API `/api/v1/projects/{project_id}/test-assets` 管理计划、用例和修订；`/automation` 管理候选/制品/审核；`/executions` 管理 Run 和证据。AI 请求携带 project、actor/授权上下文、workflow_task_id、idempotency_key、资产版本引用。返回正式 ID 与 revision，AI 不直写业务表。

`AssetRevisionRef(kind: Literal["plan","case"], asset_id: str, revision_id: str)`；`CandidateInput(source_refs: tuple[dict[str, object], ...], workflow_task_id: str, content: dict[str, object])`。不把模型生成内容视为已发布资产。`validate_case_refs(refs: tuple[AssetRevisionRef, ...]) -> None` 要求 kind=case、稳定 ID/版本非空、同一用例无重复引用。

- [ ] 写阶段 spec 和 ADR-036，明确 TAP 资产权威、AI 只生成候选、旧 ID 映射/只读历史、项目权限传递、本机执行器替代 Jenkins-first 方向，保留 ADR-024 的不可变制品和幂等约束。ADR-025 现为 deferred，按文档治理记为被新决策替代，不改写历史背景；V1 已验收行为保持兼容。
- [ ] 写失败合同测试，验证缺修订引用拒绝，cross-project 拒绝，重复请求不生成两个 revision：

```python
import pytest
from tap_platform.test_management.contracts import AssetRevisionRef, validate_case_refs

def test_plan_cannot_reference_a_moving_case_head():
    with pytest.raises(ValueError):
        validate_case_refs((AssetRevisionRef("case", "case-1", ""),))
```

- [ ] `uv run --project apps/backend pytest apps/backend/tests/contract/test_test_asset_contracts.py -q` 先失败；实现合同及 AI HTTP client，HTTP 超时先查询 idempotency receipt，不盲目重发写请求。
- [ ] 明确旧数据迁移规则：逐个旧 plan revision 导出内嵌 case 与来源证据，映射键为 `(project, legacy_plan_revision, legacy_case_id)`；无法证明跨旧修订为同一用例时不自动合并身份。保留原 source digest、新 canonical digest 和原发布/审核来源，不伪造新的人工审核。
- [ ] 确认 ADR 与接口符合现有应用边界后运行契约测试；提交 `feat: define test asset authority contracts`。

### Task 3.2：计划/用例独立版本与单写切换

**Files:** Create `P/test_management/domain/models.py`、`application/assets.py/migration.py`、`adapters/mysql.py`、`http.py`；Create `apps/backend/migrations/versions/0006_test_assets.py`、`P/db.py` 组合现有与新 metadata；Modify `apps/backend/migrations/env.py`、`P/app.py`、`.env.example`；Create `PT/conftest.py`、`PT/unit/test_asset_versions.py`、`PT/integration/test_asset_migration.py`；Modify 旧 `B/interfaces/http/routes/test_plans.py`、`B/modules/test_management/application/plans.py/publish.py`；Create `scripts/migrate-test-assets.py`。

**Interfaces:** 新表 `tap_test_plan/plan_revision/test_case/case_revision/plan_case_ref/asset_migration_map/asset_write_authority`；revision 不可变，草稿编辑也形成新 revision，draft head 用 CAS。`PlanRevision(plan_id, revision_id, case_refs: tuple[tuple[str,str], ...], source_refs, content_digest)`；`CaseRevision(case_id, revision_id, steps, expected_results, source_refs, content_digest)`。`pin_cases(case_heads: dict[str,str], ordered_case_ids: tuple[str,...]) -> tuple[tuple[str,str], ...]` 复制当时引用，不保存 latest 选择器。

- [ ] 写失败测试：

```python
from tap_platform.test_management.domain.models import pin_cases

def test_published_plan_keeps_original_case_revision():
    heads = {"login": "case-r1"}
    refs = pin_cases(heads, ("login",))
    heads["login"] = "case-r2"
    assert refs == (("login", "case-r1"),)
```

- [ ] `uv run --project apps/backend pytest apps/backend/tests/unit/test_asset_versions.py -q` 先失败；实现模型、MySQL 和 HTTP。沿用 If-Match/幂等键；修改 plan head 不影响既有版本，Case 独立复用到多个计划。
- [ ] 迁移工具支持 `export/verify/cutover`：export 只读旧库输出带摘要的包；import 位于新业务 API，重复导入验证映射/摘要；verify 比较数量、引用、证据和发布状态；cutover 先停止旧写入口并排空/转交生成作业，再最终核对后打开新入口。全程不双写。切换失败保留旧只读与新未开放状态，明确恢复操作。
- [ ] 旧测试计划 API 转为兼容门面，旧 ID 经映射查询或转发新权威，禁止再调用旧 repository 写入。旧数据不删；回退在新写入前可恢复旧入口，新写入后只前滚修复，不能删表丢历史。
- [ ] MySQL 隔离测试：重复导入无重复资产、导入中断可续、跨项目拒绝、未知 lineage 不误合并、旧发布 digest 保存、cutover 期间两入口不能同时写、AI 生成候选能关联新 plan/case。保留旧发布门禁语义，包括来源权限与来源更新校验。
- [ ] 提交 `feat: version test plans and cases with single-writer migration`。

### Task 3.3：自动化步骤、helper 与确定性代码制品

**Files:** Create `P/automation/domain/steps.py/bundles.py`、`application/generate.py`、`adapters/mysql.py`、`contracts.py/http.py`；Create TAP migration `0007_automation_bundles.py`；Create `PT/unit/test_automation_codegen.py`、`PT/contract/test_automation_versions.py`、`deploy/execution/templates/`。

**Interfaces:** `Step(id: str, action: Literal["goto","fill","click","expect_text","expect_url","helper"], target: str | None, value: str | None, helper_ref: str | None)`；`AutomationRevision(id, case_revision_id, steps: tuple[Step,...], helper_refs: tuple[str,...], data_digest, generator_version)`。`render_steps(steps: tuple[Step,...]) -> str` 使用 JSON 字符串编码生成 TS 字面量，禁止字符串拼接执行用户表达式。`bundle_digest(files: dict[str, bytes]) -> str` 按规范化路径和每文件内容 digest 排序编码计算，拒绝绝对路径、父目录和 symlink。

- [ ] 写失败测试：

```python
from tap_platform.automation.domain.bundles import bundle_digest

def test_helper_change_invalidates_bundle_identity():
    original = {"test.spec.ts": b"test", "helpers/login.ts": b"v1"}
    changed = {**original, "helpers/login.ts": b"v2"}
    assert bundle_digest(original) != bundle_digest(changed)
    assert bundle_digest(original) == bundle_digest(dict(reversed(list(original.items()))))
```

- [ ] `uv run --project apps/backend pytest apps/backend/tests/unit/test_automation_codegen.py -q` 先失败；实现六种首版动作，`goto` 限定任务配置的测试环境，locator 使用显式角色/测试 ID/允许 selector 结构，不能当任意 JS 执行。
- [ ] 生成代码把 step ID 包装进 `test.step`，计划/用例/步骤/attempt 能映射回结果。helper 保存为独立不可变源码资产，必须通过 schema/编译/检查并显式绑定；未声明 helper、孤立源码或生成后文件篡改拒绝发布。
- [ ] 最小制品包括 `manifest.json`、生成的 `.spec.ts`、helper、锁定依赖清单、Playwright 配置、数据引用；manifest 包含 plan/case/automation revisions、generator version、环境镜像 digest、数据 digest、文件摘要。普通动作不调用 coding model；SDET 只交付结构化变更和绑定 helper 候选。
- [ ] 运行生成 TypeScript 的真实编译检查（锁定 Playwright/TS 的隔离构建环境），测试引号/换行/Unicode、重复 step ID、未知 helper、数据缺失与代码注入；确定性重复生成字节一致。
- [ ] 提交 `feat: generate version-bound automation bundles`。

### Task 3.4：对象存储、人工审查与发布

**Files:** Create `P/automation/adapters/objects.py`、`application/publish.py`；Modify automation schema/http、TAP app settings、`.env.example`、`apps/backend/pyproject.toml`/`uv.lock`；Create `PT/unit/test_bundle_publish.py`、`PT/integration/test_bundle_store.py`。采用本仓库已锁定的 `botocore==1.43.75` 提供同步 S3 客户端，作为 TAP 后端新增直接依赖，不跨应用 import AI 存储代码。

**Interfaces:** `BundleStore.put(files, manifest_digest) -> str`、`verify(bundle_ref, manifest_digest) -> bool`、`read_verified(bundle_ref, manifest_digest) -> dict[str,bytes]`；`ReviewRecord(actor_id, manifest_digest, decision, reviewed_at)`；`can_publish(reviewed_digest: str, current_digest: str, sources_current: bool) -> bool`。发布操作使用同一幂等键返回相同不可变 bundle ref，不能覆盖对象。

- [ ] 写失败测试：

```python
from tap_platform.automation.application.publish import can_publish

def test_old_review_does_not_approve_changed_code():
    assert not can_publish("old", "new", True)
    assert not can_publish("same", "same", False)
    assert can_publish("same", "same", True)
```

- [ ] 窄测先失败；实现 staging 上传→逐项校验→不可变 manifest→DB published 指针。MinIO 与 DB 不做假原子事务：失败留下 staging 可回收，DB 不指向未校验对象；重试通过摘要查找复用，不二次发布。
- [ ] 审查页面展示步骤、代码/helper 差异、验证证据、固定来源版本；人工确认绑定 manifest_digest，沿用现有项目审阅者策略。AI Agent 无直接自我发布权限。
- [ ] 独立 MinIO 集成测试：上传部分失败、上传成功 DB 失败、重试、对象篡改、缺文件、路径逃逸、审核后修改、权限撤销；未发布候选只允许标记为调试执行。
- [ ] 提交 `feat: publish reviewed immutable automation bundles`。

### Task 3.5：本机隔离执行器、worker 与恢复

**Files:** Create `P/execution/domain/models.py`、`ports.py`、`application/service.py`、`adapters/mysql.py/docker.py`、`worker.py`、`http.py/contracts.py`；Create TAP migration `0008_execution_runs.py`、`deploy/execution/Dockerfile`、`build-inputs.json`、`reporter.ts`；Modify `.env.example`、TAP app/startup、Makefile；Create `PT/unit/test_execution_state.py`、`PT/integration/test_execution_recovery.py`。

**Interfaces:** `ExecutionRequest(run_id, project_id, bundle_ref, bundle_digest, environment_digest, data_digest, mode: Literal["debug","published"], idempotency_key)`；`ExecutionProvider.submit(request) -> str`、`inspect(job_id) -> dict[str,object]`、`cancel(job_id) -> None`、`collect(job_id) -> dict[str,object]`。方法同步与现有 TAP worker 模式一致；长耗时操作在 worker，HTTP 只持久化请求。`Run` 和 `Attempt` 分开；attempt 记录 lease/fence、container ID、开始/结束、取消请求与 evidence 状态。

- [ ] 写失败测试，定义 `classify_execution(exit_code: int | None, report_complete: bool, canceled_confirmed: bool) -> str`：

```python
from tap_platform.execution.domain.models import classify_execution

def test_success_waits_for_complete_report():
    assert classify_execution(0, False, False) == "collecting"
    assert classify_execution(1, True, False) == "failed"
    assert classify_execution(None, False, True) == "canceled"
```

- [ ] 窄测先失败；实现幂等 Run 创建、领取与 fencing。容器使用 run+attempt 标签，submit 回执丢失按标签核对；旧 worker 不能结算新 attempt；非零退出区分断言失败与环境/报告错误，不全部归类产品缺陷。
- [ ] 固定镜像内含浏览器、Playwright、TypeScript 和 reporter；worker 校验 bundle 后只读挂载源码，单独写入 artifacts；非 root、限 CPU/内存/PID、read-only rootfs、独立临时目录和测试网络，不使用 privileged/host network。每个测试容器只能访问其配置的自有测试应用；依赖在镜像构建时锁定，不在运行时任意安装。
- [ ] 实现停止新 case、确认当前动作安全停止、收集已有证据。浏览器已退出时，恢复重建隔离数据前置条件并新建当前 case attempt；不能继续某个旧点击或跳过尚未验证步骤。无法恢复数据则 awaiting_input。
- [ ] `TAP_EXECUTION_MAX_SECONDS=900`、`TAP_EXECUTION_MAX_ATTEMPTS=3`、镜像 digest 和资源值作为创建时快照；权限撤销、超时、取消均由 worker 终止并确认容器状态。
- [ ] 独立本机 Docker 测试：重复 submit 只有一容器、worker 崩溃时子容器仍运行可接管、终态事件丢失能核对、取消竞态不变成功、数据重置失败不重跑；测试 fixture 只清自己 label 资源。提交 `feat: execute and recover isolated playwright runs`。

### Task 3.6：证据回写、版本关联与失败修复交接

**Files:** Create `P/execution/application/results.py`、`PT/unit/test_execution_results.py`、`PT/integration/test_execution_writeback.py`；Modify reporter、execution schema/http、`B/modules/workflows/adapters/test_assets.py`；Create `BT/integration/test_automation_repair_handoff.py`。

**Interfaces:** 结果包含 run_id、attempt、case_revision_id、step_id、status、exit_code、started/finished_at、artifact refs（kind/sha256/size）。`result_identity(run_id: str, attempt: int, case_revision_id: str, step_id: str) -> tuple[str,int,str,str]` 为幂等键；同键不同 payload 为 conflict，不覆盖旧结果。`writeback(scope, manifest, results, idempotency_key) -> dict[str,object]` 先校验对象和版本再同事务写结果与事件。

- [ ] 写失败测试：

```python
from tap_platform.execution.application.results import result_identity

def test_retry_keeps_previous_failure_as_distinct_record():
    first = result_identity("run-1", 1, "case-r1", "login")
    retry = result_identity("run-1", 2, "case-r1", "login")
    assert first != retry
```

- [ ] 窄测先失败；实现结果导入，拒绝跨项目、未知 case revision、非制品内 step、错误 digest、缺必需 evidence；日志/截图/trace 通过授权对象接口访问并脱敏，不暴露宿主路径。长任务界面区分运行、收集证据、测试失败和环境失败。
- [ ] TAP 执行事件通过正式 API 交给 AI workflow adapter，按 event ID 去重；TAP 是执行事实源，AI 只投影。同步失败重放游标，不把重复回调当新 Run。
- [ ] 失败分析把原证据和冻结版本作为 QAE/SDET 的新输入，得到修复候选；新代码产生新 automation revision、重新审核发布再运行。原缺陷、失败和旧制品保留；删除断言/无依据放宽预期必须被审查测试拒绝。
- [ ] 本阶段不强制接入 Insights 聚合。若传入既有报告接口，必须扩展正式 provenance 合同再接，不填写虚构 application_commit/script_commit。当前验收以 execution 权威记录与 UI 为准。
- [ ] 集成测试：丢失/重复回调、导入中断续接、损坏 trace、修复新版本保留旧失败、产品缺陷分类不修改脚本。提交 `feat: write verified execution evidence to versioned assets`。

### Task 3.7：真实资产界面与完整隔离验收

**Files:** Create `W/features/testManagement/{api.ts,TestAssetsPage.tsx,RevisionHistory.tsx}`、`W/features/automation/{api.ts,AutomationPage.tsx,RunDetail.tsx}` 及对应测试；Modify `W/widgets/tap/TapProductPrototype.tsx`、`W/legacy/automation/AutomationWorkspace.tsx` 的正式入口连接；Modify `F/features/testManagement/api/client.ts`、`queries.ts` 中旧计划兼容调用及 `F/features/workflows/components/NodeDetails.tsx`；Create `apps/web/tests/e2e/automation-loop.spec.ts`、`apps/web/playwright.automation.config.ts`、`scripts/run-automation-loop-e2e.py`、`docs/guides/2026-11-06-versioned-test-automation.md`。

**Interfaces:** 页面从生成的 `contracts/openapi/tap-api.json` 对应 TS schema 读取；旧原型数据只能显示为模拟历史，不迁入正式运行表。资产历史页按 immutable revision 查询，运行详情链接对应 plan/case/automation/bundle；AI 节点产物链接指向版本，不指向会变化的 latest。

- [ ] 写失败前端测试：计划引用旧 case revision 仍显示旧内容；发布后修改显示新草稿；调试不计正式通过；取消中/收集证据/失败分开；刷新恢复历史；历史模拟数据有标签。接口组件边界示例断言：

```typescript
import { expect, it } from "vitest";
import { runLabel } from "./api";

it("does not present debug success as a published pass", () => {
  expect(runLabel("debug", "succeeded")).toBe("调试成功");
  expect(runLabel("published", "succeeded")).toBe("正式执行通过");
});
```

- [ ] 定义 `runLabel(mode: "debug" | "published", status: string): string`，其余状态按明确中文字典展示。`corepack pnpm --filter @tap/web exec vitest run src/features/testManagement src/features/automation` 先失败；实现正式 API 与页面，不再用 localStorage/simulatedRun 承担正式功能。
- [ ] `make contracts` 同时生成两侧 API 客户端；检查跨应用 import 禁令、旧计划兼容门面和正式资产链接；测试计划/用例版本界面覆盖查看、比较、草稿修订、发布引用和执行历史。
- [ ] E2E 脚本创建独立数据库、MinIO、测试应用、浏览器容器和项目，注入确定性模型走完整生产控制流。新增 `make automation-loop-e2e`；不使用现有共享 Demo 数据。用例从登录/错误密码/锁定场景生成计划与用例，SDET 输出自动化候选，人工确认发布，执行产生真实失败、截图和 trace，修复 helper 新版后重跑，旧失败仍可查。
- [ ] 再用真实 OpenCode/BYOK 走同场景，需 `TAP_RUN_TAPPER_REAL_MODEL_SMOKE=1` 和 `TAP_RUN_OPENCODE_PROBE=1`，报告独立列出真实模型、真实执行、恢复/重复回调证据；未跑不能计最终验收通过。
- [ ] 增加独立 FWD 真实目标 opt-in 验收（仍需真实模型双开关）：从 Tapper 入口提交 spec §11.1 的合成询价目标，真实 OpenCode 生成脚本，TAP 隔离执行器从官网选择 MyTerm Plus、填写并回读数据、提取非零报价，截图/trace/步骤结果返回同一任务和版本。通过产品入口以同一冻结制品与数据新建第二个 Run，两个结果分别可查。不得由验收控制器直接调用 FWD 代替产品执行链路；定位器须由实际页面验证，报价须来自本次浏览器结果。
- [ ] FWD 样本单独记录外部站点时间、目标、输入数据摘要、代码制品、Run ID、保费/币种/周期和证据摘要；验证码/登录/地区限制或要求真实资料时明确 blocked，不能用本地示例或模拟响应替代。默认测试不访问 FWD、不提交投保、不付款。部署/运行配置限制目标网络，真实资料接入不在本次验收内。
- [ ] 运行总计划检查与 `make automation-loop-e2e`，预览 UI/文档，更新架构、指南及产品路线图，提交 `feat: complete versioned automation execution journey`。

- [ ] 按整体 spec §11.2 增加通用性验收：至少两个站点、查询提取/表单核对/多页面操作三类流程，含实现后才选定的站点与任务组合。只改变自然语言目标、地址与数据，不修改产品代码；网站定位器仅进入本次生成制品。领域设计须说明普通 Web 业务任务如何复用现有资产与执行接口，用户无需手写测试计划。FWD 是样例，不作为唯一产品目标。

## 本阶段退出条件

整体 spec §11 的九条验收全部有可定位证据；旧数据可查且仅一个业务写入权威；无真实模型 opt-in 时只完成确定性验证，不标记整个测试闭环业务验收完成。外部 Git、Jenkins/Azure Pipelines、桌面和移动 RPA 继续保留扩展边界，不在本阶段新增实现。
