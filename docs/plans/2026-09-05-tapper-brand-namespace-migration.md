---
status: completed
date: 2026-09-05
---

# Tapper Brand and Namespace Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 TAP 平台内的智能工作区完整迁移为 Tapper，并让当前工作树中的品牌文案、代码符号、活动运行命名空间、路径、图表和客户截图保持一致。

**Architecture:** 保留 TAP 平台、provider-neutral HTTP API、`tap` Python package 与 `knowledge_*` 表；Tapper 只替换智能工作区及其活动运行命名空间。迁移采用验证阶段 clean cut，新 Compose/Blob/Milvus/Redis/LiteLLM 命名空间重新建立状态，不解析旧键、不迁移旧数据，也不自动删除旧资源。

**Tech Stack:** React 19、TypeScript 5、Ant Design 6、Vite 8、Vitest 4、Playwright 1.62、Python 3.13、FastAPI、Alembic、MySQL、Redis、Azurite/Blob、Milvus、LiteLLM、Docker Compose、Markdown、Mermaid、Draw.io/SVG。

**Spec:** [`../proposals/2026-09-05-rfc-010-tapper-brand-namespace-migration.md`](../proposals/2026-09-05-rfc-010-tapper-brand-namespace-migration.md)

## Global Constraints

- TAP 是平台品牌；Tapper 是智能工作区、知识问答和 AI Agent 入口。
- 最左上角显示完整 `TAP`；一级 Tapper 入口使用 `tapper-mark-ink.svg`；展开的二级栏使用 `tapper-wordmark-ink.svg`。
- 保持 68px 一级产品栏、Codex 式二级栏、现有内容布局、折叠动画、键盘行为和响应式交互。
- 不增加依赖，不改变 `/v1/knowledge/*`、OpenAPI operation ID、`tap` package 或 `knowledge_*` 表名。
- 当前本地验证数据可重建；不实现 alias、dual-read、backfill 或旧进程入口 shim。
- 不执行 volume/container/collection 删除；旧外部状态只会因新命名空间而不再被读取。
- 命名归一化前的字节级事实固定为 commit `0eab801`；用户已单独授权整理最后三次未推送的收口提交，以纠正生命周期顺序。整理前历史保留在 `codex/tapper-before-history-fix`，更早提交保持不变。
- 只提交实际使用的两个 `ink` SVG；PNG 导出和 `.DS_Store` 不提交。
- 已退役名称不得以任意大小写出现在受版本控制的内容或路径中。守卫使用 ASCII byte tuple `(65, 116, 104, 101, 110, 97)` 构造检测词，避免守卫自身重新引入该文本。
- 每个行为改动先运行对应的失败测试，再实现最小变更并验证转绿。

## File and Responsibility Map

| Unit                                                          | Responsibility                                                       |
| ------------------------------------------------------------- | -------------------------------------------------------------------- |
| `scripts/check_brand_namespace.py`                            | 扫描 Git tracked path 和文件字节，阻止已退役名称重新进入当前工作树。 |
| `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx`     | 表达 TAP/Tapper 两级品牌、mark/wordmark 和侧栏可访问语义。           |
| `apps/web/src/pages/TapperPage.tsx`                           | Web 根页面的规范 Tapper 入口。                                       |
| `apps/web/src/widgets/tapper/TapperWorkspace.tsx`             | 真实 Knowledge 纵向切片的 Tapper 工作区。                            |
| `apps/web/src/widgets/tap/prototype/TapperChat.tsx`           | 产品原型中的 Tapper 对话界面。                                       |
| `apps/backend/src/tap/entrypoints/tapper_runtime.py`          | Tapper 本地运行时、设置、依赖装配和命名空间约束。                    |
| `apps/backend/src/tap/entrypoints/tapper_api.py`              | Tapper API 进程入口。                                                |
| `apps/backend/src/tap/entrypoints/tapper_ingestion_worker.py` | Tapper ingestion worker 入口。                                       |
| `scripts/run-tapper-dev.sh` / `scripts/run-tapper-e2e.sh`     | 新命名空间的开发与隔离 E2E 生命周期。                                |
| `apps/web/tests/e2e/prototype-demo-capture.spec.ts`           | 可重复生成 40 张 1280×720 客户演示截图。                             |
| `docs/reviews/2026-09-05-tapper-brand-migration-review.md`    | 保存零残留、测试、视觉与数据非删除证据。                             |

## Acceptance Coverage

| Accepted requirement                                             | Implementation tasks      | Proof                                                                  |
| ---------------------------------------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| 左上角平台标识由单字母改为完整 `TAP`                             | Task 2                    | DOM/ARIA assertion plus screenshots 01 and 39.                         |
| Tapper 一级入口使用 mark，二级标题使用 wordmark                  | Task 2                    | Asset-specific assertions plus screenshots 01, 02 and 39.              |
| 当前受控树只保留 Tapper 名称与路径                               | Tasks 1, 2, 3, 4, 5, 6, 7 | Case-insensitive tracked path/content guard exits `0`.                 |
| 活动运行标识 clean cut，不读写旧状态                             | Tasks 3 and 4             | Runtime/command contracts and isolated Demo E2E.                       |
| 不删除旧外部资源、不扩大到数据迁移                               | Tasks 3, 4 and 7          | Review records namespace switch and absence of destructive commands.   |
| 保持 provider-neutral API、`tap` package 和 `knowledge_*` tables | Tasks 2, 3 and 7          | Contract generation/checks and backend contract tests.                 |
| README、规范文档、图表与客户演示材料一致                         | Tasks 5 and 6             | Link/lifecycle/XML checks and 40-image inventory/dimension assertions. |
| 只采用品牌包内两个 ink SVG                                       | Tasks 2 and 7             | Cached file inventory; PNG and `.DS_Store` remain untracked.           |

---

### Task 1: Build the retired-name guard without gating the repository yet

**Files:**

- Create: `scripts/check_brand_namespace.py`
- Create: `apps/backend/tests/contract/test_brand_namespace_contract.py`

**Interfaces:**

- Produces: `BrandViolation(kind: Literal["path", "content"], path: str)`.
- Produces: `scan_tracked_files(root: Path, paths: Sequence[str]) -> tuple[BrandViolation, ...]`.
- Produces: CLI exit `0` for no violations, `1` for violations, `2` for Git or I/O errors.
- Constraint: this task does not add the guard to `make check`; the repository-wide command remains red until Task 7.

- [ ] **Step 1: Write the failing synthetic contract tests**

```python
from pathlib import Path

from scripts.check_brand_namespace import scan_tracked_files


def retired_name() -> str:
    return bytes((65, 116, 104, 101, 110, 97)).decode("ascii")


def test_scan_reports_case_insensitive_path_and_content(tmp_path: Path) -> None:
    token = retired_name()
    path_violation = f"src/{token.lower()}_runtime.py"
    content_violation = "src/runtime.py"
    (tmp_path / "src").mkdir()
    (tmp_path / path_violation).write_text("clean", encoding="utf-8")
    (tmp_path / content_violation).write_text(token.upper(), encoding="utf-8")

    violations = scan_tracked_files(tmp_path, (path_violation, content_violation))

    assert {(item.kind, item.path) for item in violations} == {
        ("path", path_violation),
        ("content", content_violation),
    }


def test_scan_reads_binary_bytes_and_ignores_untracked_files(tmp_path: Path) -> None:
    token = retired_name().encode("ascii")
    tracked = "assets/reference.bin"
    untracked = "assets/local.bin"
    (tmp_path / "assets").mkdir()
    (tmp_path / tracked).write_bytes(b"\x00" + token + b"\xff")
    (tmp_path / untracked).write_bytes(token)

    assert [(item.kind, item.path) for item in scan_tracked_files(tmp_path, (tracked,))] == [
        ("content", tracked),
    ]
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --project apps/backend pytest apps/backend/tests/contract/test_brand_namespace_contract.py -v
```

Expected: FAIL during collection because `scripts.check_brand_namespace` does not exist.

- [ ] **Step 3: Implement the scanner and stable CLI output**

```python
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

_RETIRED = bytes((65, 116, 104, 101, 110, 97))


@dataclass(frozen=True, slots=True)
class BrandViolation:
    kind: Literal["path", "content"]
    path: str


def scan_tracked_files(root: Path, paths: Sequence[str]) -> tuple[BrandViolation, ...]:
    violations: list[BrandViolation] = []
    needle = _RETIRED.lower()
    for relative in sorted(paths):
        if needle in relative.encode("utf-8").lower():
            violations.append(BrandViolation("path", relative))
        candidate = root / relative
        if candidate.is_file() and needle in candidate.read_bytes().lower():
            violations.append(BrandViolation("content", relative))
    return tuple(violations)


def tracked_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        violations = scan_tracked_files(root, tracked_files(root))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"brand namespace check failed: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(f"{violation.kind}: {violation.path}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verify GREEN behavior and the intentional repository RED state**

Run:

```bash
uv run --project apps/backend pytest apps/backend/tests/contract/test_brand_namespace_contract.py -v
uv run --project apps/backend python scripts/check_brand_namespace.py
```

Expected: contract tests PASS; CLI exits `1` and reports current violations without modifying files.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_brand_namespace.py apps/backend/tests/contract/test_brand_namespace_contract.py
git commit -m "test(brand): add tapper namespace guard"
```

### Task 2: Rebrand the Web product shell and semantic model

**Files:**

- Add: `apps/web/assets/brand/tapper/svg/tapper-mark-ink.svg`
- Add: `apps/web/assets/brand/tapper/svg/tapper-wordmark-ink.svg`
- Rename to: `apps/web/src/pages/TapperPage.tsx`
- Rename to: `apps/web/src/pages/TapperPage.test.tsx`
- Rename to: `apps/web/src/widgets/tapper/TapperWorkspace.tsx`
- Rename to: `apps/web/src/widgets/tapper/TapperWorkspace.test.tsx`
- Rename to: `apps/web/src/widgets/tap/prototype/TapperChat.tsx`
- Modify: `apps/web/index.html`
- Modify: `apps/web/src/app/App.tsx`
- Modify: `apps/web/src/app/main.tsx`
- Modify: `apps/web/src/app/providers.tsx`
- Modify: `apps/web/src/app/styles.css`
- Modify: `apps/web/src/app/theme.ts`
- Modify: `apps/web/src/features/knowledge/api/client.test.ts`
- Modify: `apps/web/src/features/knowledge/components/CitationViewer.tsx`
- Modify: `apps/web/src/features/knowledge/components/DocumentDetail.tsx`
- Modify: `apps/web/src/features/knowledge/components/DocumentStatus.tsx`
- Modify: `apps/web/src/features/knowledge/components/DocumentTable.tsx`
- Modify: `apps/web/src/features/knowledge/components/GroundedAnswer.tsx`
- Modify: `apps/web/src/features/knowledge/components/KnowledgeLibrary.test.tsx`
- Modify: `apps/web/src/features/knowledge/components/KnowledgeLibrary.tsx`
- Modify: `apps/web/src/features/knowledge/components/MarkdownSafety.test.tsx`
- Modify: `apps/web/src/features/knowledge/components/QuestionComposer.tsx`
- Modify: `apps/web/src/features/knowledge/components/SourcesPanel.tsx`
- Modify: `apps/web/src/features/knowledge/components/UploadDialog.tsx`
- Modify: `apps/web/src/features/knowledge/copy.ts`
- Modify: `apps/web/src/features/knowledge/testing/fakeKnowledgeClient.ts`
- Modify: `apps/web/src/shared/api/contractDefaults.test.ts`
- Modify: `apps/web/src/shared/testing/productionBuild.test.ts`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.css`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.interactions.test.tsx`
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/KnowledgeGraph.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx`
- Modify: `apps/web/src/widgets/tap/prototype/artifacts/persistence.test.ts`
- Modify: `apps/web/src/widgets/tap/prototype/artifacts/persistence.ts`
- Modify: `apps/web/src/widgets/tap/prototype/artifacts/model.ts`
- Modify: `apps/web/src/widgets/tap/prototype/artifacts/state.test.ts`
- Modify: `apps/web/src/widgets/tap/prototype/copy.ts`
- Modify: `apps/web/src/widgets/tap/prototype/model.test.ts`
- Modify: `apps/web/src/widgets/tap/prototype/model.ts`
- Modify: `apps/web/src/widgets/tap/prototype/testManagement/TestManagementWorkspace.tsx`

**Interfaces:**

- Produces: `TapperPage`, `TapperWorkspace`, `TapperChat`, `tapperTheme`.
- Changes: `ProductModule` root key becomes `"tapper"`; artifact source becomes `"Tapper" | "Manual"`.
- Changes: CSS/DOM prefix becomes `.tapper-*` for the Knowledge slice and `.tap-tapper-*` for the product prototype.
- Changes: prototype snapshot version becomes `2` and storage key becomes `tap.prototype.workspace.v2`; v1 browser state is ignored without being deleted, while v2 Conversation history remains restorable.
- Preserves: public API client, generated contracts and existing interaction behavior after the expected one-time local prototype reset.

- [ ] **Step 1: Rename and update focused tests first**

Use `git mv` for page/workspace test files and change assertions to:

```tsx
it("shows TAP platform and Tapper workspace identities", () => {
  renderKnowledgeApp(<TapperPage />);

  expect(screen.getByLabelText("TAP platform")).toHaveTextContent(/^TAP$/);
  const entry = screen.getByRole("button", { name: "Tapper" });
  expect(entry.querySelector('img[src*="tapper-mark-ink.svg"]')).not.toBeNull();
  const heading = screen.getByRole("heading", { name: "Tapper" });
  expect(
    heading.querySelector('img[src*="tapper-wordmark-ink.svg"]'),
  ).not.toBeNull();
});
```

Update interaction expectations to `Tapper tools`, `Message Tapper`, `Tapper assistant`, `Imported from Tapper`, root module `tapper` and source `Tapper`.

Update `artifacts/persistence.test.ts` first to require version `2`, key `tap.prototype.workspace.v2`, rejection of a v1 snapshot and successful round-trip of a Conversation plus Tapper-linked artifacts.

- [ ] **Step 2: Verify RED before moving production files**

```bash
corepack pnpm --dir apps/web test -- --run src/pages/TapperPage.test.tsx src/widgets/tap/prototype/artifacts/persistence.test.ts src/widgets/tap/TapProductPrototype.interactions.test.tsx
```

Expected: FAIL because the Tapper modules, copy and SVG elements do not exist.

- [ ] **Step 3: Move production files and apply case-preserving replacement**

```bash
legacy_lower="$(printf '\141\164\150\145\156\141')"
legacy_title="$(printf '\101\164\150\145\156\141')"
git mv "apps/web/src/pages/${legacy_title}Page.tsx" apps/web/src/pages/TapperPage.tsx
git mv "apps/web/src/widgets/${legacy_lower}" apps/web/src/widgets/tapper
git mv "apps/web/src/widgets/tap/prototype/${legacy_title}Chat.tsx" apps/web/src/widgets/tap/prototype/TapperChat.tsx
```

Apply title/lower/upper replacements to the listed Web text files. Manually review discriminated unions, local snapshot data, ARIA labels, selectors and test descriptions.

- [ ] **Step 4: Implement the TAP badge and Tapper assets**

`PrototypeSidebar.tsx` must render:

```tsx
<div className="tap-brand" role="img" aria-label="TAP platform">
  <span aria-hidden="true">TAP</span>
</div>

<button type="button" aria-label={copy.navigation.tapper}>
  <img className="tap-tapper-rail-mark" src={tapperMark} alt="" />
  <span className="tap-sidebar-label">{copy.navigation.tapper}</span>
</button>

<h2 aria-label={copy.navigation.tapper}>
  <img className="tap-tapper-wordmark" src={tapperWordmark} alt="" />
</h2>
```

Use this sizing without changing rail widths:

```css
.tap-brand > span {
  font-size: 0.625rem;
  font-weight: 750;
  letter-spacing: -0.035em;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}

.tap-tapper-rail-mark {
  width: 24px;
  height: 24px;
  object-fit: contain;
}

.tap-tapper-wordmark {
  display: block;
  width: 96px;
  height: auto;
}
```

- [ ] **Step 5: Verify GREEN across the Web package**

```bash
corepack pnpm --dir apps/web test -- --run src/pages/TapperPage.test.tsx src/widgets/tapper/TapperWorkspace.test.tsx src/widgets/tap/prototype/artifacts/persistence.test.ts src/widgets/tap/TapProductPrototype.interactions.test.tsx
corepack pnpm --dir apps/web run check
corepack pnpm --dir apps/web test -- --run
```

Expected: focused and full tests PASS; lint, format, architecture and build PASS.

- [ ] **Step 6: Commit**

```bash
git add -u apps/web/index.html apps/web/src
git add apps/web/assets/brand/tapper/svg/tapper-mark-ink.svg apps/web/assets/brand/tapper/svg/tapper-wordmark-ink.svg
git diff --cached --name-only
git commit -m "feat(web): rebrand workspace as tapper"
```

Expected staging: only the listed Web source changes plus the two approved SVGs; no PNG export or `.DS_Store`.

### Task 3: Rename the backend runtime and create the new storage namespace

**Files:**

- Rename to: `apps/backend/migrations/versions/0003_tapper_documents.py`
- Rename to: `apps/backend/src/tap/entrypoints/tapper_api.py`
- Rename to: `apps/backend/src/tap/entrypoints/tapper_ingestion_worker.py`
- Rename to: `apps/backend/src/tap/entrypoints/tapper_runtime.py`
- Modify: `apps/backend/migrations/versions/0004_knowledge_projection_authority.py`
- Modify: `apps/backend/src/tap/entrypoints/relay_reconciler.py`
- Modify: `apps/backend/src/tap/interfaces/http/knowledge_service.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/blob_artifacts.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/milvus_documents.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/mysql_documents.py`
- Modify: `apps/backend/src/tap/modules/knowledge/adapters/mysql_projection.py`
- Modify: `apps/backend/src/tap/modules/knowledge/application/answers.py`
- Modify: `apps/backend/src/tap/modules/knowledge/application/demo_policy.py`
- Modify: `apps/backend/src/tap/modules/knowledge/application/ingestion.py`
- Modify: `apps/backend/src/tap/modules/knowledge/domain/documents.py`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/answers.py`
- Modify: `apps/backend/src/tap/modules/knowledge/ports/documents.py`
- Modify: `apps/backend/src/tap/operations/milvus/client.py`
- Modify: `apps/backend/src/tap/testing/__init__.py`
- Modify: `apps/backend/src/tap/testing/deterministic_model.py`
- Modify: `apps/backend/src/tap/testing/failure_injection.py`
- Rename to: `apps/backend/tests/contract/test_tapper_http_contract.py`
- Rename to: `apps/backend/tests/fixtures/tapper/source.md`
- Rename to: `apps/backend/tests/fixtures/tapper/source.txt`
- Rename to: `apps/backend/tests/integration/test_tapper_milvus_projection.py`
- Rename to: `apps/backend/tests/integration/test_tapper_milvus_rebuild.py`
- Rename to: `apps/backend/tests/integration/test_tapper_persistence_restart.py`
- Rename to: `apps/backend/tests/smoke/test_tapper_codex_smoke.py`
- Rename to: `apps/backend/tests/smoke/test_tapper_real_model.py`
- Rename to: `apps/backend/tests/unit/entrypoints/test_tapper_runtime.py`
- Modify: `apps/backend/tests/contract/test_blob_artifact_contract.py`
- Modify: `apps/backend/tests/contract/test_codex_exec_strict.py`
- Modify: `apps/backend/tests/contract/test_document_index_contract.py`
- Modify: `apps/backend/tests/contract/test_litellm_strict.py`
- Modify: `apps/backend/tests/contract/test_milvus_target_binding.py`
- Modify: `apps/backend/tests/contract/test_milvus_transport.py`
- Modify: `apps/backend/tests/integration/test_azurite_artifacts.py`
- Modify: `apps/backend/tests/integration/test_citation_snapshot_transaction.py`
- Modify: `apps/backend/tests/integration/test_ingestion_entrypoint.py`
- Modify: `apps/backend/tests/integration/test_ingestion_recovery.py`
- Modify: `apps/backend/tests/integration/test_knowledge_answer_http.py`
- Modify: `apps/backend/tests/integration/test_projection_coordinator.py`
- Modify: `apps/backend/tests/integration/test_relay_entrypoint.py`
- Modify: `apps/backend/tests/unit/knowledge/test_answer_service.py`
- Modify: `apps/backend/tests/unit/knowledge/test_citation_resolver.py`
- Modify: `apps/backend/tests/unit/knowledge/test_demo_policy.py`
- Modify: `apps/backend/tests/unit/knowledge/test_ingestion_worker.py`
- Modify: `apps/backend/tests/unit/knowledge/test_knowledge_http_service.py`
- Modify: `apps/backend/tests/unit/operations/test_milvus_doc_provisioner.py`
- Modify: `apps/backend/tests/unit/operations/test_milvus_embeddings.py`

**Interfaces:**

- Produces: `TapperSettings`, `TapperApiRuntime`, `TapperAnswerBackend`, `TapperFailureController`, `TapperEmbeddingPort`, `TapperMilvusConfig`, `TapperDocumentMilvusClients`, `DeterministicTapperModel`.
- Produces: `create_tapper_document_clients(...)` and `worker_settings_from_tapper(...)`.
- Preserves: HTTP paths, response schemas and `knowledge_*` SQL tables.
- Breaks intentionally: existing local Alembic state and retired Python imports.

- [ ] **Step 1: Move and rewrite backend tests before production files**

The runtime contract must start with:

```python
from tap.entrypoints.tapper_runtime import TapperSettings


def test_tapper_settings_use_the_new_namespace() -> None:
    settings = TapperSettings.from_mapping({})

    assert settings.collection == "kb_doc_v1_tapper_demo"
    assert settings.alias == "kb_doc_tapper_demo_active"
    assert settings.corpus_version == "tapper-demo-v1"
    assert settings.chat_alias == "tapper-chat"
    assert settings.embedding_alias == "tapper-embedding"
```

Rename the fixture directory to `apps/backend/tests/fixtures/tapper/` and update generated fixture IDs and content.

- [ ] **Step 2: Verify RED for the missing Tapper runtime**

```bash
uv run --project apps/backend pytest apps/backend/tests/unit/entrypoints/test_tapper_runtime.py apps/backend/tests/contract/test_tapper_http_contract.py -v
```

Expected: FAIL during import because the Tapper entrypoint and types do not exist.

- [ ] **Step 3: Move entrypoints and rename symbols**

Use `git mv` with the encoded variables from Task 2. Apply case-preserving replacements to imports, class names, factories, state attributes, errors and helpers. Do not keep old-module shims.

- [ ] **Step 4: Switch activity-bearing values**

| Contract              | Value                                                                    |
| --------------------- | ------------------------------------------------------------------------ |
| Alembic revision      | `0003_tapper_documents`                                                  |
| Blob containers       | `tapper-originals`, `tapper-artifacts`                                   |
| Blob copy hash domain | `tapper-original-copy-v1`                                                |
| Milvus physical/alias | `kb_doc_v1_tapper_demo`, `kb_doc_tapper_demo_active`                     |
| Corpus/project/group  | `tapper-demo-v1`, `tapper-demo`, `tapper-local`                          |
| Model aliases         | `tapper-chat`, `tapper-embedding`                                        |
| Fence source type     | `tapper_fence`                                                           |
| Parser/chunker        | `tapper-parser-v1`, `tapper-structure-512-v1`                            |
| Pipeline/index        | `tapper-ingestion-v1`, `tapper-index-v1`                                 |
| Worker/Redis group    | `tapper-local-worker`, `tapper-ingestion`                                |
| MySQL lock            | `tap:tapper:answer-snapshot-retention:v1`                                |
| Policy identifiers    | `tapper-demo-policy-v1`, `tapper-demo-acl-v1`, `tapper-demo-decision-v1` |
| Readiness ID          | `__tapper_readiness_reserved_never_persisted__`                          |

Set the `0004` down revision to `0003_tapper_documents`. Assert that old local revision IDs are rejected rather than silently rewritten.

- [ ] **Step 5: Verify GREEN**

```bash
uv run --project apps/backend pytest apps/backend/tests/unit apps/backend/tests/contract/test_tapper_http_contract.py apps/backend/tests/contract/test_blob_artifact_contract.py apps/backend/tests/contract/test_document_index_contract.py apps/backend/tests/contract/test_litellm_strict.py apps/backend/tests/contract/test_milvus_target_binding.py apps/backend/tests/contract/test_milvus_transport.py -v
```

Expected: PASS with Tapper imports, values and failure messages; provider-neutral HTTP contracts remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add -u apps/backend/migrations apps/backend/src apps/backend/tests
git diff --cached --name-only
git commit -m "refactor(backend): move runtime to tapper namespace"
```

### Task 4: Rename local operations, configuration and deterministic E2E

**Files:**

- Rename to: `scripts/tapper_collection.py`
- Rename to: `scripts/check-tapper-demo.py`
- Rename to: `scripts/run-tapper-dev.sh`
- Rename to: `scripts/run-tapper-e2e.sh`
- Modify: `.env.example`
- Modify: `Makefile`
- Modify: `compose.yaml`
- Modify: `deploy/local/litellm/config.yaml`
- Modify: `apps/backend/tests/contract/test_demo_commands.py`
- Modify: `apps/web/playwright.config.ts`
- Modify: `apps/web/vite.config.ts`
- Rename to: `apps/web/tests/e2e/tapper.spec.ts`
- Modify: `apps/web/tests/e2e/fixtureBuilder.ts`
- Modify: `apps/web/tests/e2e/persistence.spec.ts`

**Interfaces:**

- Produces: `TAP_TAPPER_COMPOSE_PROJECT`, `TAP_ALLOW_TAPPER_VOLUME_RESET`, `TAPPER_*`, `LITELLM_TAPPER_*` and `TAP_RUN_TAPPER_*`.
- Preserves: neutral targets `demo-up`, `demo-check`, `demo-dev`, `demo-e2e`, `demo-down` and `demo-reset`.
- Produces: default Compose project `tap-tapper-demo`; E2E project `tap-tapper-e2e`.

- [ ] **Step 1: Rewrite command contract tests first**

```python
EXPECTED_ENV = {
    "TAP_TAPPER_COMPOSE_PROJECT",
    "TAPPER_API_HOST",
    "TAPPER_API_PORT",
    "TAPPER_WEB_HOST",
    "TAPPER_WEB_PORT",
    "TAPPER_MODEL_BACKEND",
    "TAPPER_ANSWER_BACKEND",
    "LITELLM_TAPPER_EMBEDDING_MODEL",
}


def test_demo_up_uses_tapper_project_and_collection_script() -> None:
    result = run_make("demo-up")
    assert "tap-tapper-demo" in result.stdout
    assert "scripts/tapper_collection.py ensure" in result.stdout
```

Keep the destructive reset negative contract: it fails unless the project equals `tap-tapper-demo` and `TAP_ALLOW_TAPPER_VOLUME_RESET=1`.

- [ ] **Step 2: Verify RED**

```bash
uv run --project apps/backend pytest apps/backend/tests/contract/test_demo_commands.py -v
```

Expected: FAIL because recipes, paths and environment keys still use the retired namespace.

- [ ] **Step 3: Move scripts and replace the operational namespace**

All active keys use the case-preserving mapping `TAPPER_*`, `TAP_TAPPER_*`, `LITELLM_TAPPER_*` and `TAP_RUN_TAPPER_*`. Apply it to shell-local test variables too.

```dotenv
TAP_TAPPER_COMPOSE_PROJECT=tap-tapper-demo
TAP_ALLOW_TAPPER_VOLUME_RESET=0
TAPPER_API_HOST=127.0.0.1
TAPPER_API_PORT=8000
TAPPER_WEB_HOST=127.0.0.1
TAPPER_WEB_PORT=5173
TAPPER_MODEL_BACKEND=litellm
TAPPER_ANSWER_BACKEND=litellm
TAPPER_COLLECTION=kb_doc_v1_tapper_demo
TAPPER_ALIAS=kb_doc_tapper_demo_active
TAPPER_CORPUS_VERSION=tapper-demo-v1
TAPPER_CHAT_ALIAS=tapper-chat
TAPPER_EMBEDDING_ALIAS=tapper-embedding
LITELLM_TAPPER_EMBEDDING_MODEL=dashscope/text-embedding-v4
```

The real-model and deterministic flags become `TAP_RUN_TAPPER_REAL_MODEL_SMOKE`, `TAP_RUN_TAPPER_CODEX_CONFORMANCE` and `TAP_RUN_TAPPER_E2E`.

- [ ] **Step 4: Preserve ownership and reset safety**

`demo-down` preserves volumes. `demo-reset` may remove volumes only for `tap-tapper-demo` with the explicit new reset flag. `run-tapper-e2e.sh` uses a unique isolated project and rejects the default project.

- [ ] **Step 5: Verify GREEN**

```bash
uv run --project apps/backend pytest apps/backend/tests/contract/test_demo_commands.py -v
make demo-e2e
```

Expected: contract tests PASS; E2E reports zero skip/flake and touches only `tap-tapper-e2e` resources.

- [ ] **Step 6: Commit**

```bash
git add -u .env.example Makefile compose.yaml deploy/local/litellm/config.yaml scripts apps/backend/tests/contract/test_demo_commands.py apps/web/playwright.config.ts apps/web/vite.config.ts apps/web/tests/e2e
git diff --cached --name-only
git commit -m "chore(runtime): switch local demo to tapper"
```

### Task 5: Normalize governed documentation, paths and diagrams

**Files:**

- Rename to: `docs/architecture/2026-09-04-tapper-knowledge-web-automation-overview.md`
- Rename to: `docs/decisions/2026-08-31-adr-017-tapper-local-codex-answer-backend.md`
- Rename to: `docs/decisions/2026-09-01-adr-018-tapper-local-codex-tool-free-answer.md`
- Rename to: `docs/plans/2026-08-27-tapper-local-knowledge-demo.md`
- Rename to: `docs/plans/2026-08-31-tapper-local-codex-answer-backend.md`
- Rename to: `docs/plans/2026-09-02-tapper-interaction-prototype.md`
- Rename to: `docs/plans/2026-09-03-tapper-library-graph-visual-unification.md`
- Rename to: `docs/plans/2026-09-04-tapper-knowledge-web-automation-platform.md`
- Rename to: `docs/proposals/2026-08-27-rfc-005-tapper-local-knowledge-demo.md`
- Rename to: `docs/proposals/2026-08-31-rfc-006-tapper-local-codex-answer-backend.md`
- Rename to: `docs/proposals/2026-09-04-rfc-009-tapper-knowledge-web-automation-platform.md`
- Rename to: `docs/reference/2026-09-04-tapper-platform-contracts.md`
- Rename to: `docs/reviews/2026-08-27-tapper-local-knowledge-demo.md`
- Rename to: `docs/reviews/2026-09-05-tapper-platform-design-baseline-review.md`
- Rename to: `.impeccable/critique/2026-09-01T17-11-19Z__apps-web-src-pages-tapperpage-tsx.md`
- Rename to: `.superpowers/sdd/2026-09-02-tapper-interaction-prototype/task-2-report.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `apps/web/PRODUCT.md`
- Modify: `docs/index.md`
- Modify: `docs/architecture/2026-08-20-overview.md`
- Modify: `docs/architecture/2026-08-21-knowledge-chat-ui.md`
- Modify: `docs/architecture/2026-08-27-tap-platform-architecture.drawio`
- Modify: `docs/architecture/2026-08-27-tap-platform-architecture.svg`
- Modify: `docs/architecture/index.md`
- Modify: `docs/architecture/rag/2026-08-21-ai-search-index.md`
- Modify: `docs/architecture/rag/2026-08-21-chunking-and-provenance.md`
- Modify: `docs/architecture/rag/2026-08-21-foundation.md`
- Modify: `docs/architecture/rag/2026-08-21-retrieval-tuning.md`
- Modify: `docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.drawio`
- Modify: `docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.svg`
- Modify: `docs/architecture/rag/index.md`
- Modify: `docs/decisions/2026-08-21-adr-014-codex-specialist-runtime.md`
- Modify: `docs/decisions/2026-09-02-adr-019-phase-1-intelligence-layer-exploration.md`
- Modify: `docs/decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md`
- Modify: `docs/decisions/2026-09-04-adr-025-jenkins-first-execution-provider.md`
- Modify: `docs/decisions/index.md`
- Modify: `docs/plans/2026-08-20-roadmap.md`
- Modify: `docs/plans/2026-08-23-phase-1-application-implementation.md`
- Modify: `docs/plans/2026-09-02-phase-1-intelligence-core-implementation.md`
- Modify: `docs/plans/2026-09-03-low-code-automation-interaction-prototype.md`
- Modify: `docs/plans/index.md`
- Modify: `docs/proposals/2026-08-20-open-questions.md`
- Modify: `docs/proposals/2026-08-23-rfc-003-phase-1-application-structure.md`
- Modify: `docs/proposals/2026-08-24-rfc-004-provider-neutral-search-backends.md`
- Modify: `docs/proposals/2026-09-02-rfc-007-phase-1-intelligence-layer-exploration.md`
- Modify: `docs/proposals/2026-09-03-rfc-008-tap-product-shell-and-low-code-automation.md`
- Modify: `docs/proposals/index.md`
- Modify: `docs/reference/2026-08-20-contracts.md`
- Modify: `docs/reference/2026-08-20-source-notes.md`
- Modify: `docs/reference/index.md`
- Modify: `docs/reviews/2026-09-03-low-code-automation-prototype-review.md`
- Modify: `docs/reviews/index.md`

**Interfaces:**

- Produces: Tapper as the only current product name in normalized README prose, PRODUCT, governance, RFC/ADR/Plan/Review and diagrams.
- Defers: the customer screenshot guide and the 26 matching README image-link destinations to Task 6 so links remain valid until image paths move.
- Preserves: every RFC/ADR ID, date, lifecycle state, supersedes relation, decision meaning and review conclusion.
- Preserves: relative links and date-prefixed documentation paths.

- [ ] **Step 1: Capture the documentation RED baseline**

```bash
uv run --project apps/backend python scripts/check_brand_namespace.py
```

Expected: exit `1`, including README/docs/AGENTS/PRODUCT content and documentation/media paths.

- [ ] **Step 2: Rename governed files and repair links atomically**

Use `git mv` to the exact target paths above. Update README and all eight documentation indexes in the same commit; create no redirect, duplicate page or symlink.

- [ ] **Step 3: Normalize terminology and identifiers**

Replace title/lower/upper forms with `Tapper`/`tapper`/`TAPPER` across the listed tracked text. Manually review fenced commands so identifiers match Tasks 2–4. In `README.md`, normalize prose now but leave the 26 matching image-link destinations byte-for-byte unchanged; do not modify `docs/reference/2026-09-04-customer-prototype-demo-guide.md` in this task. Task 6 moves those image paths and updates both consumers atomically.

Add this exact note immediately after front matter—or immediately after the H1 when the record has no front matter—in every affected terminal or accepted historical record whose command/evidence text changed:

```markdown
> **命名归一化说明（2026-09-05）：** 本文只对产品和仓库标识做 Tapper 命名归一化，原日期、状态、决策、范围与评审结论未改变。命名归一化前的字节级原文以 Git commit `0eab801` 为准；下列命令或证据文本属于 identifier-normalized transcription，不再声明与该提交逐字节相同。
```

Do not add the note to normative documents created after RFC-010.

The exact historical-note set is ADR-014/017/018/019/021/025, RFC-003/004/005/006/007/008/009, the completed or cancelled plans dated 2026-08-23, 2026-08-27, 2026-08-31, 2026-09-02 (both matching plans) and 2026-09-03 (both matching plans), plus the three affected review records. The active roadmap, current planned platform plan, README and reference material receive normalized prose without the historical note.

- [ ] **Step 4: Update Mermaid, Draw.io and SVG together**

Change labels and IDs in Mermaid blocks, both Draw.io sources and both paired SVGs. Source/export labels must match, and no diagram may claim planned capability is implemented.

- [ ] **Step 5: Validate governance**

```bash
apps/web/node_modules/.bin/prettier --write README.md AGENTS.md apps/web/PRODUCT.md docs
xmllint --noout docs/architecture/2026-08-27-tap-platform-architecture.drawio docs/architecture/2026-08-27-tap-platform-architecture.svg docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.drawio docs/architecture/rag/2026-08-27-rag-knowledge-business-flow.svg
git diff --check
```

Run this read-only link/index/lifecycle audit:

````bash
uv run --project apps/backend python - <<'PY'
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path.cwd()
DOCS = ROOT / "docs"
LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")
INDEXED_DIRS = (
    DOCS,
    DOCS / "architecture",
    DOCS / "architecture" / "rag",
    DOCS / "decisions",
    DOCS / "plans",
    DOCS / "proposals",
    DOCS / "reference",
    DOCS / "reviews",
)
ALLOWED = {
    "rfc": {"draft", "in-review", "accepted", "implemented", "rejected", "withdrawn"},
    "adr": {"proposed", "accepted", "superseded"},
    "plan": {"planned", "active", "completed", "cancelled"},
}


def visible_markdown(path: Path) -> str:
    visible: list[str] = []
    fenced = False
    marker = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            current = stripped[:3]
            if not fenced:
                fenced, marker = True, current
            elif current == marker:
                fenced, marker = False, ""
            continue
        if not fenced:
            visible.append(line)
    return "\n".join(visible)


def front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise AssertionError(f"missing front matter: {path.relative_to(ROOT)}")
    raw = text.split("\n---\n", 1)[0][4:]
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"invalid front matter: {path.relative_to(ROOT)}")
    return value


problems: list[str] = []
for markdown in DOCS.rglob("*.md"):
    for raw in LINK.findall(visible_markdown(markdown)):
        destination = raw.strip().strip("<>").split("#", 1)[0]
        if not destination or "://" in destination or destination.startswith("mailto:"):
            continue
        target = (markdown.parent / unquote(destination)).resolve()
        if not target.exists():
            problems.append(f"broken link: {markdown.relative_to(ROOT)} -> {raw}")

for directory in INDEXED_DIRS:
    index = directory / "index.md"
    index_text = index.read_text(encoding="utf-8")
    for child in directory.glob("*.md"):
        if child.name in {"index.md", "rfc-template.md", "adr-template.md"}:
            continue
        if child.name not in index_text:
            problems.append(f"unindexed child: {child.relative_to(ROOT)}")

records: dict[str, tuple[Path, dict[str, object]]] = {}
for kind, directory, marker in (
    ("rfc", DOCS / "proposals", "-rfc-"),
    ("adr", DOCS / "decisions", "-adr-"),
    ("plan", DOCS / "plans", None),
):
    for path in directory.glob("20*.md"):
        if marker is not None and marker not in path.name:
            continue
        metadata = front_matter(path)
        lifecycle = metadata.get("status")
        if lifecycle not in ALLOWED[kind]:
            problems.append(f"invalid {kind} status: {path.relative_to(ROOT)} -> {lifecycle!r}")
        if kind in {"rfc", "adr"}:
            identity = metadata.get("id")
            if not isinstance(identity, str) or identity in records:
                problems.append(f"invalid or duplicate id: {path.relative_to(ROOT)} -> {identity!r}")
            else:
                records[identity] = (path, metadata)

for identity, (path, metadata) in records.items():
    if not identity.startswith("ADR-"):
        continue
    for field, reverse in (("supersedes", "superseded-by"), ("superseded-by", "supersedes")):
        values = metadata.get(field, [])
        if not isinstance(values, list):
            problems.append(f"invalid {field}: {path.relative_to(ROOT)}")
            continue
        for related in values:
            target = records.get(related)
            if target is None or identity not in target[1].get(reverse, []):
                problems.append(f"asymmetric ADR relation: {identity} {field} {related}")

if problems:
    raise SystemExit("\n".join(problems))
print("documentation governance audit passed")
PY
````

Expected: no broken relative links, every direct Markdown child indexed, valid lifecycle front matter, unique RFC/ADR IDs and bidirectional ADR supersession.

Run the Task 1 guard again. It must still exit `1`, but every remaining result must be confined to the deferred README/guide screenshot references, the 26 screenshot paths, or the three stale generated design-evidence directories owned by Task 6.

- [ ] **Step 6: Commit**

```bash
git add -u README.md AGENTS.md apps/web/PRODUCT.md docs .impeccable .superpowers
git diff --cached --name-only
git commit -m "docs: normalize tapper product terminology"
```

### Task 6: Replace stale visual evidence and regenerate customer screenshots

**Files:**

- Remove: `apps/web/.impeccable/build/`
- Remove: `apps/web/.impeccable/mocks/decision/`
- Remove: `apps/web/.impeccable/review/`
- Preserve: `apps/web/.impeccable/config.json`
- Preserve: `apps/web/.impeccable/questions/`
- Create: `apps/web/playwright.prototype.config.ts`
- Create: `apps/web/tests/e2e/prototype-demo-capture.spec.ts`
- Modify: `apps/web/package.json`
- Rename: the 26 Tapper-related JPEG paths in `docs/assets/prototype-demo/` by replacing their encoded legacy prefix with `tapper`.
- Replace: all 40 JPEG contents in `docs/assets/prototype-demo/`.
- Modify: `docs/reference/2026-09-04-customer-prototype-demo-guide.md`
- Modify: `README.md`

**Interfaces:**

- Produces: `pnpm prototype:capture`, an isolated Vite server on `127.0.0.1:15174`, deterministic mocked Knowledge HTTP responses, 1280×720 JPEG output and exactly 40 customer screenshots.
- Removes: generated comps/diffs that no longer describe current design; Git history remains the archive.

- [ ] **Step 1: Add the failing capture inventory**

```ts
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

const OUTPUT_DIR = resolve(process.cwd(), "../../docs/assets/prototype-demo");

const OUTPUTS = [
  "01-tapper-new-chat.jpg",
  "02-tapper-conversation-minimap.jpg",
  "03-tapper-model-selector.jpg",
  "04-tapper-context-menu.jpg",
  "05-tapper-source-picker.jpg",
  "06-tapper-agent-picker.jpg",
  "07-tapper-skill-picker.jpg",
  "08-tapper-selected-context.jpg",
  "09-tapper-agent-catalog.jpg",
  "10-tapper-create-agent.jpg",
  "11-tapper-skill-catalog.jpg",
  "12-tapper-create-skill.jpg",
  "13-tapper-library-empty.jpg",
  "14-tapper-library-all.jpg",
  "15-tapper-library-filtered.jpg",
  "16-tapper-add-source.jpg",
  "17-tapper-knowledge-graph.jpg",
  "18-tapper-knowledge-graph-node.jpg",
  "19-test-management-plans.jpg",
  "20-test-plan-detail-linked.jpg",
  "21-test-plan-run-config.jpg",
  "22-test-plan-run-result.jpg",
  "23-test-plan-detail-unlinked.jpg",
  "24-test-management-test-data.jpg",
  "25-automation-library.jpg",
  "26-create-automation.jpg",
  "27-web-automation-bdd-mapping.jpg",
  "28-web-automation-action-editor.jpg",
  "29-web-automation-ai-agent.jpg",
  "30-web-automation-run-history.jpg",
  "31-mobile-automation-device.jpg",
  "32-mobile-automation-run-result.jpg",
  "33-tapper-test-plan-first.jpg",
  "34-tapper-test-plan-review.jpg",
  "34b-tapper-generate-linked-automation.jpg",
  "35-tapper-channel-choice.jpg",
  "36-tapper-linked-artifacts.jpg",
  "37-tapper-minimap-preview.jpg",
  "38-tapper-sources-collapsed.jpg",
  "39-tapper-sidebar-collapsed.jpg",
] as const;

test("checked-in screenshot inventory is canonical", () => {
  expect(OUTPUTS).toHaveLength(40);
  const actual = readdirSync(OUTPUT_DIR)
    .filter((name) => name.endsWith(".jpg"))
    .sort();
  expect(actual).toEqual([...OUTPUTS].sort());
});
```

Create `playwright.prototype.config.ts` with its own `webServer`:

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prototype-demo-capture.spec.ts",
  outputDir: "./test-results/prototype-capture",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:15174",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "corepack pnpm exec vite --host 127.0.0.1 --port 15174",
    url: "http://127.0.0.1:15174",
    reuseExistingServer: false,
    env: {
      TAPPER_API_HOST: "127.0.0.1",
      TAPPER_API_PORT: "18001",
      TAPPER_WEB_HOST: "127.0.0.1",
      TAPPER_WEB_PORT: "15174",
    },
  },
});
```

Add the package script before running the RED check:

```json
"prototype:capture": "playwright test --config playwright.prototype.config.ts"
```

Run:

```bash
corepack pnpm --dir apps/web run prototype:capture -- --grep "checked-in screenshot inventory"
```

Expected: FAIL because 26 checked-in screenshot paths still use the retired prefix.

- [ ] **Step 2: Implement deterministic capture helpers**

```ts
test.use({ viewport: { width: 1280, height: 720 } });

async function capture(page: Page, name: (typeof OUTPUTS)[number]) {
  await expect(page.getByLabel("TAP platform")).toBeVisible();
  await expect(page.getByRole("button", { name: "Tapper" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const imagePath = resolve(OUTPUT_DIR, name);
  await page.screenshot({
    animations: "disabled",
    caret: "hide",
    path: imagePath,
    type: "jpeg",
    quality: 90,
    fullPage: false,
  });
  const source = `data:image/jpeg;base64,${readFileSync(imagePath).toString("base64")}`;
  const dimensions = await page.evaluate(
    (url) =>
      new Promise<[number, number]>((resolveImage, rejectImage) => {
        const image = new Image();
        image.onload = () =>
          resolveImage([image.naturalWidth, image.naturalHeight]);
        image.onerror = () => rejectImage(new Error("invalid JPEG"));
        image.src = url;
      }),
    source,
  );
  expect(dimensions).toEqual([1280, 720]);
}
```

Before each independent flow, install an init script that calls `window.localStorage.clear()`, freeze the Playwright clock at `2026-09-05T10:00:00+08:00`, navigate to `/` and wait for the composer. Use role/name selectors, never CSS coordinates.

Install `page.route("**/v1/knowledge/**", ...)` before navigation and return fixed, schema-valid document/list/detail/upload/status/answer fixtures required by the Library and knowledge flows. The capture command must not require API, Redis, MySQL, Milvus, Blob or model processes.

- [ ] **Step 3: Implement all state journeys**

| Screens | Required action sequence                                                                                                                 |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 01      | Fresh page with Tapper/New chat active.                                                                                                  |
| 02, 37  | Send two knowledge questions; capture conversation; hover active minimap tick.                                                           |
| 03      | Open model-only selector.                                                                                                                |
| 04–08   | Open composer `+`; open Source/Agent/Skill pickers; select one of each; capture chips.                                                   |
| 09–12   | Open Agent and Skills destinations; open each create dialog.                                                                             |
| 13–18   | Open Library; capture empty/all/filter/add-source; open Graph; select one node.                                                          |
| 19–24   | Open Test Management; open linked plan; run and capture result; open unlinked plan; open Test Data.                                      |
| 25–32   | Open LCA; create an asset; open Web detail; edit an action; open AI Agent; open history; open Mobile detail and result.                  |
| 33–36   | Ask for automation; choose Test Plan first; review BDD; generate linked asset; separately trigger channel choice; capture linked result. |
| 38      | Collapse Knowledge sources.                                                                                                              |
| 39      | Collapse the Tapper secondary sidebar.                                                                                                   |

Assert each dialog/menu/result state before capture.

- [ ] **Step 4: Remove stale generated design evidence**

Use `git rm -r` only on the three listed generated directories. Preserve config, questions, source, brand SVGs and plate assets. The removal is recoverable through Git.

- [ ] **Step 5: Capture and wire images**

Rename the 26 affected paths before capture without placing the retired token in the shell history:

```bash
legacy_lower="$(printf '\141\164\150\145\156\141')"
for source in docs/assets/prototype-demo/*-"${legacy_lower}"-*.jpg; do
  target="${source/-${legacy_lower}-/-tapper-}"
  git mv "$source" "$target"
done
```

The package script is:

```json
"prototype:capture": "playwright test --config playwright.prototype.config.ts"
```

Run the dedicated Playwright/Vite harness. Assert exactly 40 JPEGs, every image is 1280×720, and README/guide links resolve.

- [ ] **Step 6: Perform one bounded visual review**

Inspect 01, 02, 17, 20, 27, 36, 38 and 39 together. Confirm the TAP badge is legible, mark is sharp, wordmark aligns without crowding, drawers animate cleanly, minimap avoids the composer and no image shows the retired name. Apply at most one batch of fixes, recapture all 40 once and perform one confirmation pass.

- [ ] **Step 7: Commit**

```bash
git add -u apps/web/.impeccable apps/web/package.json docs/assets/prototype-demo README.md docs/reference/2026-09-04-customer-prototype-demo-guide.md
git add apps/web/playwright.prototype.config.ts apps/web/tests/e2e/prototype-demo-capture.spec.ts
git diff --cached --name-only
git commit -m "docs: refresh tapper prototype visuals"
```

Expected staging: the three generated evidence deletions, two capture-harness files, package script, 40 screenshot paths/contents and the two screenshot consumers only.

### Task 7: Enforce zero residue and close the migration

**Files:**

- Modify: `Makefile`
- Create: `docs/reviews/2026-09-05-tapper-brand-migration-review.md`
- Modify: `docs/reviews/index.md`
- Modify: `docs/proposals/2026-09-05-rfc-010-tapper-brand-namespace-migration.md`
- Modify: `docs/proposals/index.md`
- Modify: `docs/plans/2026-09-05-tapper-brand-namespace-migration.md`
- Modify: `docs/plans/index.md`

**Interfaces:**

- Produces: `make brand-check`, included by `make check`.
- Produces: Review conclusion `pass | fail`; RFC-010 becomes `implemented` and this plan becomes `completed` only on `pass`.

- [ ] **Step 1: Wire and prove the guard**

```make
brand-check:
	uv run --project apps/backend python scripts/check_brand_namespace.py
```

Add `brand-check` to `.PHONY` and `make check`. The synthetic test must still prove a controlled violation exits `1`; the real repository command must exit `0`.

- [ ] **Step 2: Run complete static, test and build verification**

```bash
make contracts
make check
make test
corepack pnpm --dir apps/web run check
corepack pnpm --dir apps/web test -- --run
git diff --check
```

Expected: every command exits `0`; the Web run reports the full current test count with zero failures.

- [ ] **Step 3: Run isolated lifecycle verification**

```bash
make demo-e2e
```

Expected: Tapper E2E project starts from zero, migrates, ingests, answers with citations, survives persistence checks and tears down only its own resources with zero skip/flake.

- [ ] **Step 4: Check residue and asset scope**

```bash
uv run --project apps/backend python scripts/check_brand_namespace.py
git ls-files apps/web/assets | sort
git status --short
```

Expected: brand check exits `0`; only two supplied Tapper SVGs are newly tracked; no `.DS_Store` or unused PNG export is staged.

- [ ] **Step 5: Run final UI detector and browser checks**

```bash
node .agents/skills/impeccable/scripts/detect.mjs --json apps/web/src/widgets/tap/prototype/PrototypeSidebar.tsx apps/web/src/widgets/tap/TapProductPrototype.css apps/web/src/widgets/tap/TapProductPrototype.tsx
```

At 1280×720 and 390×844 verify keyboard focus, Tapper sidebar and Knowledge sources toggles, model menu, composer, minimap hover and reduced-motion behavior. Record defects and fixes in the Review.

- [ ] **Step 6: Write Review and close lifecycle metadata**

The Review records the commit range from `0eab801`, brand-check output, tracked-path count, Web/backend test counts, Demo E2E result, 40/40 screenshot dimensions, visual/accessibility observations and confirmation that old resources were not deleted. Only conclusion `pass` allows RFC-010=`implemented` and Plan=`completed`.

- [ ] **Step 7: Commit the completed migration**

```bash
git add -u Makefile docs/proposals/2026-09-05-rfc-010-tapper-brand-namespace-migration.md docs/proposals/index.md docs/plans/2026-09-05-tapper-brand-namespace-migration.md docs/plans/index.md docs/reviews/index.md
git add docs/reviews/2026-09-05-tapper-brand-migration-review.md
git diff --cached --name-only
git diff --cached --check
git commit -m "feat: complete tapper product migration"
```

Expected: only Task 7's gate/lifecycle/review files are staged; no `.DS_Store`, unused brand export or unrelated user file is staged; post-commit status contains only intentionally untracked user-owned assets.
