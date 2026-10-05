# Milvus 删除去掉 flush 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除文档时不再调用 Milvus `flush`，让删除任务在 Milvus 默认 flush 限速（每个 collection 0.1 qps）下也能在 10 秒内完成，`make demo-e2e` 在默认配置下稳定通过。

**Architecture:** 参考 Dify：删除只调用 `delete`，不 flush；正确性由 MySQL fence（`record_fence`）、MySQL 发布权限校验和 Milvus `Strong` 一致性读保证。只改 `MilvusDocumentIndex` 的删除相关路径，发布（upsert）路径保留一次 flush。

**Tech Stack:** Python 3.13、pymilvus、pytest（contract 测试用内存版 `MemoryMilvus`）、Milvus v2.6.22。

**Spec:** 无独立 spec。设计在对话中确认，要点如下：
- 根因：`deploy/local/milvus/milvus.yaml` 的 `quotaAndLimits.flushRate.collection.max: 0.1` 是上游默认值。每次删除约 3–4 次 flush，导致删除任务卡 25–30 秒；ingestion worker 串行处理，新上传只能排队，`knowledge-upload-security` 的 45 秒轮询因此超时。
- Dify 的 Milvus 适配器没有任何 `flush`，删除是 `client.delete(pks=ids)`，靠回数据库过滤保证正确性。
- 本地 Milvus 配置还原为上游默认 `0.1`，让 E2E 在真实限速条件下验证。
- 不引入 Celery，不拆分 worker 队列（并入 worker 可靠性那项）。

## Global Constraints

- 不新增依赖，不改 worker、队列和任务表结构。
- `MilvusDocumentIndex.upsert_revision` 中 upsert 之后的 `flush` 保留（`milvus_documents.py:281`）。
- 删除后的反向检查（`_count_revision_locked`、`purge_document` 末尾的 `query_persisted_rows`）保留，不得削弱。
- `deploy/local/milvus/milvus.yaml` 的 `flushRate.collection.max` 必须是 `0.1`。
- 提交信息使用小写祈使句的 Conventional Commit。

## Review Focus

- fence 之后迟到的 upsert 仍被拒绝（`IndexFenced`），即使 fence 行没有 flush：由现有 `test_durable_fence_blocks_late_upsert_and_survives_index_reconstruction` 守住，Task 1 Step 4 必须跑到。
- 删除后立即做的反向检查必须看到 0 行。内存实现无法证明这一点，真实 Milvus 下由 Task 2 的 `make demo-e2e`（删除后轮询到 404、再上传到 `ready`）验证。
- 发布路径的 flush 仍然恰好 1 次：Task 1 的新测试显式断言，防止误删。
- 索引重建时保留 fence：由现有 `test_rebuild_*` 与 `test_empty_rebuild_preserves_fences_*` 守住，Task 1 Step 4 跑完整 contract 文件。
- worker 在删除中途重启后能继续：删除本身幂等，由现有 `tests/integration/test_ingestion_recovery.py` 守住，Task 2 的 `make test` 覆盖。

---

### Task 1: 删除路径不再 flush

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/milvus_documents.py`（`fence_revision` 约 307–335 行，`_delete_ids` 约 1204–1209 行）
- Test: `apps/tap-ai-backend/tests/contract/test_document_index_contract.py`（`MemoryMilvus` 约 583 行起，新测试放在 `test_document_purge_removes_every_revision_and_keeps_only_deletion_fence` 之后）

**Interfaces:**
- Consumes: 无
- Produces: `MemoryMilvus.flushes: list[str]`，每次 `flush(name)` 追加 `name`

- [x] **Step 1: 建分支**

```bash
git switch -c fix/milvus-delete-without-flush
```

- [x] **Step 2: 给 `MemoryMilvus` 记录 flush，并写失败测试**

在 `MemoryMilvus.__init__` 加 `self.flushes: list[str] = []`，`flush` 在原有断言后追加 `self.flushes.append(name)`。新增测试：

```python
@pytest.mark.asyncio
async def test_deletion_path_never_flushes_and_publish_flushes_once() -> None:
    memory = MemoryMilvus()
    index = index_for(memory)
    await index.ensure_target()
    chunks = tuple(chunk(i, "rev_a") for i in range(1, 3))
    memory.flushes.clear()
    await index.upsert_revision(
        work("rev_a"), chunks, _revision_embeddings(chunks), index_version="tapper-index-v1"
    )
    assert len(memory.flushes) == 1
    target = DeletionTarget(
        "doc_a", work("rev_a").revision_id, tuple(str(c.chunk_id) for c in chunks), ()
    )
    memory.flushes.clear()

    await index.fence_revision(target)
    await index.delete_revision(target)
    assert await index.count_revision(target) == 0
    await index.purge_document("doc_a", keep_revision_id=None)

    assert memory.flushes == []
    assert _document_revisions(memory) == {f"fence:{target.revision_id}": 1}
```

- [x] **Step 3: 确认测试失败**

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_document_index_contract.py::test_deletion_path_never_flushes_and_publish_flushes_once -v`
Expected: FAIL，`assert memory.flushes == []` 处显示非空列表。

- [x] **Step 4: 删掉两处 flush 并跑测试**

删除 `_delete_ids` 末尾的 `await self._writer.flush(physical)`，以及 `fence_revision` 中 upsert fence 行之后的 `await self._writer.flush(physical)`。其余代码不动。

Run: `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_document_index_contract.py apps/tap-ai-backend/tests/unit/operations/test_milvus_publish.py apps/tap-ai-backend/tests/unit/operations/test_milvus_health.py -v`
Expected: 全部 PASS（包括新测试和 Review Focus 中列出的 fence、重建测试）。

- [x] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/knowledge/adapters/milvus_documents.py apps/tap-ai-backend/tests/contract/test_document_index_contract.py
git commit -m "fix: stop flushing milvus on document deletion"
```

### Task 2: 还原 Milvus 默认限速并在真实环境验证

**Files:**
- Modify: `deploy/local/milvus/milvus.yaml:1118`（确认为 `max: 0.1`）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 5"工程完成"勾选）
- Modify: `docs/superpowers/plans/2026-10-01-product-roadmap.md`（子项目 2 行状态）

**Interfaces:**
- Consumes: Task 1 的删除路径
- Produces: 无

- [x] **Step 1: 还原本地 Milvus 配置**

Run: `git checkout main -- deploy/local/milvus/milvus.yaml && grep -n "max: 0.1 # qps, default no limit, rate for flush at collection level" deploy/local/milvus/milvus.yaml`
Expected: 输出一行，`git status` 中该文件无改动。

- [x] **Step 2: 全量检查**

Run: `make check && make test`
Expected: 两者都以 0 退出。

- [x] **Step 3: 默认限速下跑 E2E，并记录删除耗时**

Run: `make demo-e2e`
Expected: 输出 `Tapper isolated E2E journey passed.`，退出码 0。运行期间轮询隔离库 `tap-tapper-e2e` 的 `knowledge_ingestion_job`，确认 `kind = 'deletion'` 的任务从领取到完成都小于 10 秒。若解析器镜像过期导致启动失败，先执行 `make parser-build` 再重跑。

- [x] **Step 4: 更新路线图并提交**

E2E 通过后，在 V1 总纲中勾选能力 5 的"工程完成"；在产品路线图中把"子项目 2 可观测性（能力 5）"的状态改为已完成，并附本 PR 编号。

```bash
git add docs/superpowers/plans/2026-09-29-v1-roadmap.md docs/superpowers/plans/2026-10-01-product-roadmap.md docs/superpowers/plans/2026-10-01-milvus-delete-without-flush.md
git commit -m "docs: mark observability engineering complete"
```
