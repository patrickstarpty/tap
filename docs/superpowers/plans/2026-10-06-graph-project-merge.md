# 项目级图谱合并与查询实施计划（PR 2/5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PR 1 产出的按修订片段图全量重放合并成一张带别名、社区与度数的项目级图，按（project_id、version）缓存邻接表对外提供总览、搜索、邻居、路径、节点详情、高亮与按切片反查接口，并让 `GraphAnswerEnricher` 改用项目图（检索切片种子 + 别名最长匹配）替代整句子串匹配与按来源集合查快照。

**Architecture:** 新增 `graph_project_*` 八张表与一张按项目合并的队列表；合并 job 由 graph worker 进程的 `pending_work` 钩子串行执行，复用 `MysqlGraphJobStore` 的 `FOR UPDATE` 租约与幂等模式。实体对齐、边合并、标签传播、别名最长匹配全部是不依赖 MySQL 的纯函数，MySQL 适配器只负责装表与写版本；`LoadedProjectGraph` 是进程内邻接表，`InMemoryProjectGraphStore` 与 `MysqlProjectGraphStore` 共用同一组查询函数。旧片段快照路由原样保留到 PR 4。

**Tech Stack:** Python 3.13、SQLAlchemy async + Alembic（MySQL）、pytest-asyncio、FastAPI/pydantic 契约、`make contracts` 生成 OpenAPI 与 TypeScript 类型。

**Spec:** [知识图谱脉络分析设计](../specs/2026-10-06-knowledge-graph-reasoning-design.md) 第 1.2、1.3、3.1、5.1、5.2、5.3 节（PR 2 范围）。前置：[PR 1 计划](2026-10-06-graph-extraction-batching.md) 已合并（`GraphNode.aliases`、`GraphEdge.relation_label`、`GraphSnapshot` 的 `PARTIAL`、`vocabulary.normalize_key`、`GraphFragmentBatch`、`MysqlGraphJobStore.renew/record_batch/load_batches`、`rule_based_draft`）。

## Global Constraints

- 不新增 Python 或 Node 依赖。
- 迁移编号 `0029_project_graph`，`down_revision = "0028_graph_fragment_batch"`。新表（spec 1.2）：`graph_project_version`、`graph_project_node`、`graph_project_edge`、`graph_project_node_source`、`graph_project_edge_evidence`、`graph_project_alias`、`graph_project_community`、`graph_merge_log`；`graph_fragment_batch` 已由 PR 1 建好。本 PR 另加合并队列表 `graph_project_merge_job`（见 Task 4，spec 只规定"复用租约与幂等模式"，未规定承载表）。
- 对齐规范化（spec 1.2）：casefold、全角转半角、去标点与空白、去尾部修饰词（有限列表："规则""条款""流程""政策""要求""policy""rule""rules""process""clause"）。三级对齐依次为：1）规范化 canonical key 精确匹配；2）任一规范化别名相同且节点类型相同；3）embedding 相似度 ≥ `TAPPER_GRAPH_ALIGN_THRESHOLD`（默认 0.92）且节点类型相同，仅当 `TAPPER_GRAPH_ALIGN_EMBEDDING=1` 时启用，默认 `0` 关闭。三级都要求节点类型相同（第 1 级比 spec 文字更严，见 Review Focus 第 1 条）。
- 合并后节点 label 取出现次数最多的片段 label，其余进别名表；边按两端节点与 `relation_type` 合并，置信度取最大值，证据取并集，`relation_label` 取出现次数最多者。
- 社区：标签传播，迭代上限 20 轮，社区数上限 50，小于 3 个节点的社区并入"其他"；节点度数同时写入。
- 合并为全量重放，输入是项目内当前已发布修订的全部 READY/PARTIAL 且 `extraction_profile_digest == GRAPH_EXTRACTION_PROFILE_DIGEST` 的片段；片段集合摘要 `fragment_digest` 与上一版本相同则跳过；版本就绪后原子切换 `status=READY`，保留一个旧版本，更早版本清理。
- 邻接表缓存按（project_id、version）保存在进程内，版本切换时失效；1 万节点、5 万边装载一次 ≤ 1 秒（装表，不经整快照对象图）。
- 配置：`TAPPER_GRAPH_ALIGN_EMBEDDING=0`、`TAPPER_GRAPH_ALIGN_THRESHOLD=0.92`、`TAPPER_GRAPH_OVERVIEW_LIMIT=150`。
- 所有项目图响应带 `graphVersion`；请求带 `graphVersion` 且与当前不符时返回 409。
- `GET /snapshots` 与现有片段语义路由在本 PR 保持可用（PR 4 退役）。
- 文档、代码与夹具中不得出现客户企业名称（英文缩写或中文全称均不得出现），示例语料只用公开的友邦条款。
- 提交信息用小写祈使句 Conventional Commit；每个任务结束前 `git diff --check`。
- 每个任务的测试命令在 `apps/tap-ai-backend` 目录下用 `uv run pytest` 运行；需要 MySQL 的集成测试沿用 `owned_project_mysql` 夹具与缺失时跳过的约定。

## Review Focus

- 两个来源各自定义"保单"但类型不同（ENTITY 与 REQUIREMENT）：不得合并为一个节点，各自保留来源证据（Task 1 Step 1 的 `test_same_key_different_type_stays_separate`）。
- 删除某节点唯一的来源后重放：该节点、它的别名与只靠它支撑的边全部从新版本消失，其余节点的 node_id 不变（Task 4 Step 5 的 `test_replay_after_source_delete_drops_orphan_nodes`）。
- 合并 job 租约过期被另一个 worker 认领后，原 worker 继续完成：不得发布重复版本，`graph_project_version` 对同一 `fragment_digest` 只有一行 READY（Task 4 Step 5 的 `test_lost_lease_cannot_publish_duplicate_version`）。
- 带来源过滤的总览：唯一证据落在过滤之外的节点必须被丢弃，同时丢弃以它为端点的边；两端都在但证据全在过滤之外的边也丢弃（Task 3 Step 5 的 `test_overview_source_filter_drops_nodes_without_in_filter_evidence`）。
- 别名索引同时含"健康告知书"与"健康告知"时，对"健康告知书需要什么"必须只命中"健康告知书"（Task 1 Step 1 的 `test_alias_longest_match_prefers_longer_alias`）。

---

### Task 1: 项目图 domain 模型与纯函数（对齐、边合并、标签传播、别名索引）

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/domain/project.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/alignment.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/communities.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/alias_index.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/merger.py`
- Test: `apps/tap-ai-backend/tests/unit/graph/test_project_alignment.py`、`tests/unit/graph/test_project_communities.py`、`tests/unit/graph/test_alias_index.py`、`tests/unit/graph/test_project_merger.py`（均新建）

**Interfaces:**
- Consumes：`GraphSnapshotDraft`、`GraphNode.aliases`、`GraphEdge.relation_label`、`RelationOrigin`（`domain/models.py:36-295`），`normalize_key`（PR 1 `domain/vocabulary.py`）。
- Produces（`domain/project.py`，全部 `@dataclass(frozen=True, slots=True)`）：
  - `ProjectGraphVersion(project_id: str, version: int, status: Literal["MERGING", "READY", "FAILED"], fragment_digest: str, node_count: int, edge_count: int, merged_at: datetime | None)`；属性 `version_id -> str` 返回 `f"gpv_{version}"`（写入 Turn 的 `graph_snapshot_id`）。
  - `ProjectNode(node_id: str, label: str, node_type: str, canonical_key: str, degree: int = 0, community_id: str | None = None, aliases: tuple[str, ...] = ())`
  - `ProjectEdge(edge_id: str, source_node_id: str, target_node_id: str, relation_type: str, relation_label: str, origin: RelationOrigin, confidence: float)`
  - `NodeSource(node_id: str, source_revision_id: str, document_revision_id: str, chunk_id: str, anchor: Mapping[str, object], fragment_snapshot_id: str, fragment_node_id: str)`
  - `EdgeEvidence(edge_id: str, source_revision_id: str, document_revision_id: str, chunk_id: str, anchor: Mapping[str, object], content_digest: str, fragment_snapshot_id: str, fragment_edge_id: str)`
  - `Alias(alias_norm: str, node_id: str, origin: Literal["LABEL", "MODEL", "MERGE"])`
  - `Community(community_id: str, label: str, size: int)`
  - `MergeLogEntry(node_id: str, merged_from: tuple[tuple[str, str], ...], rule: Literal["EXACT", "ALIAS", "EMBEDDING"])`，`merged_from` 元素为（片段 snapshot_id、片段 node_id）。
  - `ProjectGraphDraft(fragment_digest: str, nodes: tuple[ProjectNode, ...], edges: tuple[ProjectEdge, ...], node_sources: tuple[NodeSource, ...], edge_evidence: tuple[EdgeEvidence, ...], aliases: tuple[Alias, ...], communities: tuple[Community, ...], merge_log: tuple[MergeLogEntry, ...])`；校验：边端点存在、每个 EXTRACTED 边至少一条证据、`alias_norm` 非空、同一（alias_norm、node_id）唯一。
  - `ProjectSubgraph(version: int, nodes: tuple[ProjectNode, ...], edges: tuple[ProjectEdge, ...], sources: tuple[NodeSource, ...] = (), evidence: tuple[EdgeEvidence, ...] = ())`
  - `ProjectNodeDetail(version: int, node: ProjectNode, community: Community | None, sources: tuple[NodeSource, ...], edges: tuple[ProjectEdge, ...], neighbors: tuple[ProjectNode, ...], evidence: tuple[EdgeEvidence, ...])`
  - `FragmentRecord(snapshot_id: str, revision_id: str, status: Literal["READY", "PARTIAL"], content_digest: str, draft: GraphSnapshotDraft)`
  - `fragment_digest(records: Iterable[FragmentRecord]) -> str`：对 `sorted((snapshot_id, content_digest))` 的 JSON 取 `"sha256:" + hexdigest`。
  - 标识：`project_node_id(node_type: str, key: str) -> str` 返回 `"gpn_" + sha256(f"{node_type}|{key}")[:32]`；`project_edge_id(source: str, relation_type: str, target: str) -> str` 返回 `"gpe_" + sha256(f"{source}|{relation_type}|{target}")[:32]`。节点 id 只依赖类型与对齐 key，因此跨版本稳定。
- Produces（`application/alignment.py`）：
  - `ALIGNMENT_SUFFIXES: tuple[str, ...] = ("规则", "条款", "流程", "政策", "要求", "policy", "rule", "rules", "process", "clause")`
  - `alignment_key(text: str) -> str`：`normalize_key(text)` 后去掉一个尾部修饰词；结果为空则不去。
  - `ResolvedEntity(node_id: str, label: str, node_type: str, canonical_key: str, aliases: tuple[str, ...], members: tuple[tuple[str, str], ...], rule: Literal["EXACT", "ALIAS", "EMBEDDING"])`
  - `resolve_entities(fragments: Sequence[GraphSnapshotDraft], *, embeddings: Mapping[tuple[str, str], tuple[float, ...]] | None = None, threshold: float = 0.92) -> tuple[ResolvedEntity, ...]`：按片段 snapshot_id、节点首次出现顺序逐个并入；第 1 级 key 为 `alignment_key(canonical_key)`，第 2 级 key 为 `alignment_key(label)` 与每个 `alignment_key(alias)`，第 3 级仅在 `embeddings` 非空时用余弦相似度；三级都要求 `node_type` 相同；`rule` 记录该实体最后一次并入所用的级别，单成员为 `EXACT`。label 取成员 label 的众数（并列取先出现者），其余 label 与全部 aliases 去重后进 `aliases`。
  - `merge_edges(fragments: Sequence[GraphSnapshotDraft], entity_of: Mapping[tuple[str, str], str]) -> tuple[tuple[ProjectEdge, tuple[tuple[str, str], ...]], ...]`：键为（源项目节点、relation_type、目标项目节点），两端合并后相同则丢弃（自环）；`confidence` 取最大值，`relation_label` 取众数，`origin` 任一为 EXTRACTED 则为 EXTRACTED；第二项是（片段 snapshot_id、片段 edge_id）列表。
- Produces（`application/communities.py`）：
  - `OTHER_COMMUNITY_ID = "community_other"`、`OTHER_COMMUNITY_LABEL = "其他"`
  - `propagate_labels(node_ids: Sequence[str], edges: Sequence[tuple[str, str]], *, max_rounds: int = 20, max_communities: int = 50, min_size: int = 3) -> dict[str, str]`：初始标签为节点自身 id；每轮按 `sorted(node_ids)` 顺序取邻居标签众数（并列取字典序最小）；无变化或到 20 轮停止。社区按（大小降序、最小成员 id）排序取前 50 个编号 `community_001..`，其余与大小 < 3 的并入 `community_other`。
  - `community_label(member_ids: Sequence[str], degree: Mapping[str, int], label: Mapping[str, str]) -> str`：度数最高者的 label（并列取 id 最小）；`community_other` 固定为 `"其他"`。
- Produces（`application/alias_index.py`）：
  - `AliasMatch(alias_norm: str, node_id: str, start: int, end: int)`
  - `class AliasIndex`：`@classmethod build(cls, aliases: Iterable[Alias]) -> AliasIndex`；`match(self, text: str) -> tuple[AliasMatch, ...]`：对 `normalize_key(text)` 按别名长度降序逐个 `str.find` 所有出现位置，命中区间遮蔽后不再参与更短别名匹配；同一 node_id 只返回首次命中；`__len__`。
- Produces（`application/merger.py`）：
  - `class ProjectGraphMerger`：`__init__(self, *, align_threshold: float = 0.92)`；`merge(self, scope: ProjectScopeContext, fragments: Sequence[FragmentRecord], *, embeddings: Mapping[tuple[str, str], tuple[float, ...]] | None = None) -> ProjectGraphDraft`：依次 `resolve_entities` → `merge_edges` → 度数 → `propagate_labels` → `community_label` → 别名（LABEL：成员 label 的 `normalize_key`；MODEL：片段 aliases；MERGE：被合并掉的 label）→ `NodeSource`/`EdgeEvidence`（从片段 `evidence_ids` 展开）→ `MergeLogEntry`（仅多成员实体）→ `fragment_digest(fragments)`。片段为空时返回零节点的草稿。

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/graph/test_project_alignment.py
def test_alignment_key_strips_one_trailing_modifier():
    assert alignment_key("核保流程") == "核保" and alignment_key("Refund Policy") == "refund"
    assert alignment_key("流程") == "流程"

def test_exact_key_merges_across_fragments_and_keeps_majority_label(): ...
    # 片段 A 两个节点 label "健康告知"，片段 B 一个节点 label "健康告知书" canonical_key 相同 → 一个实体，label "健康告知"，aliases 含 "健康告知书"，rule EXACT

def test_same_key_different_type_stays_separate():
    a = _fragment("frag-a", [("n1", "保单", "ENTITY", "保单")])
    b = _fragment("frag-b", [("n1", "保单", "REQUIREMENT", "保单")])
    entities = resolve_entities([a, b])
    assert len(entities) == 2 and {e.node_type for e in entities} == {"ENTITY", "REQUIREMENT"}

def test_alias_level_requires_same_type_and_records_rule(): ...
    # 片段 A "Underwriting" aliases ("核保",) CONCEPT；片段 B "核保" CONCEPT → 合并 rule ALIAS；若 B 为 PROCESS → 不合并

def test_embedding_level_only_when_vectors_supplied(): ...
    # 无 embeddings → 两个不同 key 不合并；给出相同向量且 threshold 0.92 → 合并 rule EMBEDDING

def test_merge_edges_unions_evidence_and_takes_max_confidence(): ...
    # 同一（源、REQUIRES、目标）两条边 confidence 0.6/0.9、relation_label "需要"/"需要"/"须提供" → 一条，0.9，"需要"，证据两条；端点合并为同一节点的边被丢弃
```

```python
# tests/unit/graph/test_project_communities.py
def test_two_cliques_joined_by_one_edge_form_two_communities(): ...   # 两个 4 节点团各一社区，id 为 community_001/community_002
def test_small_communities_fold_into_other():
    assignment = propagate_labels(["a", "b", "c", "d", "e"], [("a", "b")], min_size=3)
    assert set(assignment.values()) == {OTHER_COMMUNITY_ID}
def test_label_propagation_is_deterministic_and_bounded(): ...       # 同一输入两次结果相同；max_rounds=1 仍返回全部节点
def test_community_count_is_capped_at_fifty(): ...                   # 60 个独立三元组 → 50 个编号社区 + community_other
```

```python
# tests/unit/graph/test_alias_index.py
def test_alias_longest_match_prefers_longer_alias():
    index = AliasIndex.build([Alias("健康告知", "n-short", "LABEL"), Alias("健康告知书", "n-long", "LABEL")])
    assert [m.node_id for m in index.match("健康告知书需要什么？")] == ["n-long"]
def test_alias_match_masks_used_spans_and_dedupes_nodes(): ...       # "核保流程之后是理赔流程，核保流程由谁负责" → 每个 node 一次
def test_alias_match_ignores_width_case_and_punctuation(): ...       # "Ｈealth-Disclosure" 命中 alias_norm "healthdisclosure"
```

```python
# tests/unit/graph/test_project_merger.py
def test_merge_produces_stable_ids_degrees_communities_and_log():
    draft = ProjectGraphMerger().merge(VALIDATION_SCOPE, [_record("frag-a", ...), _record("frag-b", ...)])
    assert draft.fragment_digest == fragment_digest([...])
    assert all(n.node_id == project_node_id(n.node_type, alignment_key(n.canonical_key)) for n in draft.nodes)
    assert {n.community_id for n in draft.nodes} <= {c.community_id for c in draft.communities}
    assert any(entry.rule == "EXACT" and len(entry.merged_from) == 2 for entry in draft.merge_log)
def test_merge_of_no_fragments_is_an_empty_draft(): ...
def test_merge_rejects_scope_mismatch(): ...                        # 片段 project_id 与 scope 不同 → ValueError
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_project_alignment.py tests/unit/graph/test_project_communities.py tests/unit/graph/test_alias_index.py tests/unit/graph/test_project_merger.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `domain/project.py`、`alignment.py`、`communities.py`、`alias_index.py`、`merger.py`**

余弦相似度用标准库 `math` 手写，不引入 numpy。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/graph -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph/domain/project.py apps/tap-ai-backend/src/tap/modules/graph/application apps/tap-ai-backend/tests/unit/graph
git commit -m "feat: add project graph model with entity alignment and label propagation"
```

---

### Task 2: 迁移 `0029_project_graph` 与项目图表定义

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0029_project_graph.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_project.py`（表定义与写入函数；查询在 Task 3）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql.py:242-253`（`GRAPH_TABLES` 追加 `*PROJECT_GRAPH_TABLES`）
- Modify: `apps/tap-ai-backend/tests/architecture/test_migration_metadata.py:11-89`（`EXPECTED_TABLES`）
- Test: `apps/tap-ai-backend/tests/integration/test_mysql_project_graph.py`（新建）、`tests/integration/test_schema_drift.py`（现有）

**Interfaces:**
- Produces（`mysql_project.py`，全部经 `mysql.py:63-86` 的 `_scoped` 创建，列名照 spec 1.2）：
  - `graph_project_version`：`version Integer` 主键、`status String(16)`、`fragment_digest String(71)`、`node_count Integer`、`edge_count Integer`、`merged_at DATETIME(6) null`、`created_at DATETIME(6)`。
  - `graph_project_node`：主键（`version`、`node_id String(128)`），`label String(512)`、`node_type String(32)`、`canonical_key String(512)`、`degree Integer`、`community_id String(64) null`、`aliases JSON`；索引 `ix_graph_project_node_key (project_id, version, canonical_key)`、`ix_graph_project_node_community (project_id, version, community_id)`。
  - `graph_project_edge`：主键（`version`、`edge_id String(128)`），`source_node_id`、`target_node_id`、`relation_type String(64)`、`relation_label String(64)`、`origin String(16)`、`confidence Float`；索引 `ix_graph_project_edge_source (project_id, version, source_node_id)`、`ix_graph_project_edge_target (project_id, version, target_node_id)`。
  - `graph_project_node_source`：主键（`version`、`node_id`、`source_revision_id String(128)`、`chunk_id String(128)`），`document_revision_id`、`anchor_json JSON`、`fragment_snapshot_id String(64)`、`fragment_node_id String(128)`；索引 `ix_graph_project_node_source_chunk (project_id, version, chunk_id)`。
  - `graph_project_edge_evidence`：主键（`version`、`edge_id`、`source_revision_id`、`chunk_id`），`document_revision_id`、`anchor_json JSON`、`content_digest String(71)`、`fragment_snapshot_id`、`fragment_edge_id`；索引 `ix_graph_project_edge_evidence_chunk (project_id, version, chunk_id)`。
  - `graph_project_alias`：主键（`version`、`alias_norm String(512)`、`node_id`），`origin String(8)`。
  - `graph_project_community`：主键（`version`、`community_id String(64)`），`label String(512)`、`size Integer`。
  - `graph_merge_log`：主键（`version`、`node_id`），`merged_from JSON`、`rule String(16)`。
  - `graph_project_merge_job`：主键 `project_id`（由 `_scoped` 提供，无额外主键列），`due_at DATETIME(6) null`、`last_reason String(32) null`、`claimed_due_at DATETIME(6) null`、`lease_owner String(128) null`、`lease_token String(64) null`、`lease_expires_at DATETIME(6) null`、`attempt_count Integer server_default "0"`、`failure_code String(64) null`、`created_at`、`updated_at`。
  - `PROJECT_GRAPH_TABLES: tuple[Table, ...]`（上述九张）。
  - `async def publish_project_version(session: AsyncSession, scope: ProjectScopeContext, draft: ProjectGraphDraft, *, version: int, now: datetime) -> ProjectGraphVersion`：要求活动事务；先插入 `status="MERGING"` 的版本行，批量 `insert().values([...])` 写八张表（每批 500 行），最后把版本行更新为 `READY`、`merged_at=now`，返回 READY 版本。
  - `async def prune_project_versions(session, scope, *, keep_latest: int = 2) -> int`：删除比第 `keep_latest` 新版本更早的全部行，返回删除的版本数。
  - `async def load_fragment_draft(session, scope, snapshot_id: str) -> GraphSnapshotDraft`：从 `mysql.py:478-617` 的 `_memory` 抽出的只读装载（含 `aliases` 与 `relation_label`），`_memory` 改为调用它。

- [ ] **Step 1: 写失败的集成测试**

```python
# tests/integration/test_mysql_project_graph.py
@pytest.mark.asyncio
async def test_publish_version_writes_all_tables_and_prunes_old_versions(owned_project_mysql):
    # 发布 version 1、2、3（各一节点一边）→ graph_project_version 只剩 2、3；version 3 status READY、merged_at 非空；
    # graph_project_node_source / edge_evidence / alias / community / merge_log 的 version=3 行数与草稿一致
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/integration/test_mysql_project_graph.py tests/architecture/test_migration_metadata.py -v`
Expected: FAIL（`ImportError: mysql_project`；`EXPECTED_TABLES` 不含新表）

- [ ] **Step 3: 写迁移 `0029_project_graph.py`**

`_scope_columns`/`_scope_constraints` 写法照抄 `0027_prompt_suggestions.py:17-37`；每张表都带 `project_id`、`actor_id`、`enterprise_id`、`identity_mode`、`identity_origin` 与 `uq_<table>_project_pk` 唯一约束；`downgrade()` 按依赖反序 `drop_table`。

- [ ] **Step 4: 实现 `mysql_project.py`，扩展 `GRAPH_TABLES`，把九张表加入 `EXPECTED_TABLES`**

- [ ] **Step 5: 运行集成、架构与 schema drift 测试**

Run: `uv run pytest tests/integration/test_mysql_project_graph.py tests/integration/test_mysql_graph_store.py tests/architecture -v && (cd ../.. && make schema-drift)`
Expected: 全部 PASS；schema drift 无差异（无隔离 MySQL 时跳过并在 PR 描述注明）

- [ ] **Step 6: 提交**

```bash
git add apps/tap-ai-backend/migrations/versions/0029_project_graph.py apps/tap-ai-backend/src/tap/modules/graph/adapters apps/tap-ai-backend/tests
git commit -m "feat: add project graph tables and version publication"
```

---

### Task 3: `ProjectGraphStore` 端口、邻接表缓存、内存与 MySQL 查询实现

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/ports/project_store.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/project_queries.py`（`LoadedProjectGraph`、`ProjectGraphCache`、`InMemoryProjectGraphStore`）
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_project.py`（新增 `MysqlProjectGraphStore`）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_project_queries.py`（新建）、`tests/contract/test_project_graph_store_contract.py`（新建，内存实现）、`tests/integration/test_mysql_project_graph.py`

**Interfaces:**
- Consumes：Task 1 的 domain 类型与 `AliasIndex`；Task 2 的表与 `publish_project_version`。
- Produces（`ports/project_store.py`）：
  - `class ProjectGraphNotReady(LookupError)`：项目没有 READY 版本。
  - `class ProjectGraphStorePort(Protocol)`：
    - `async def get_current(self, scope: ProjectScopeContext) -> ProjectGraphVersion | None`
    - `async def communities(self, scope) -> tuple[Community, ...]`
    - `async def overview(self, scope, *, source_revision_ids: tuple[str, ...] = (), community_ids: tuple[str, ...] = (), node_limit: int = 150) -> ProjectSubgraph`：按社区轮转取度数最高节点直到 `node_limit`，边为所选节点之间的边。
    - `async def search(self, scope, text: str, *, source_revision_ids: tuple[str, ...] = (), node_limit: int = 50) -> ProjectSubgraph`：`normalize_key(text)` 对 `alias_norm` 前缀命中优先、包含命中其次，按度数降序；`"*"` 返回度数最高的前 `node_limit` 个。
    - `async def neighbors(self, scope, node_id: str, *, depth: int = 1, node_limit: int = 50, source_revision_ids: tuple[str, ...] = ()) -> ProjectSubgraph`：语义同 `queries.py:117-133`（`depth` 1–2）。
    - `async def path(self, scope, source_node_id: str, target_node_id: str, *, max_hops: int = 3, source_revision_ids: tuple[str, ...] = ()) -> ProjectSubgraph`：BFS 最短路径，超过 `max_hops` 返回空子图（语义同 `queries.py:135-163`，节点预算改为跳数）。
    - `async def node_detail(self, scope, node_id: str) -> ProjectNodeDetail`：不存在抛 `GraphFactNotFound`。
    - `async def highlight(self, scope, edge_ids: tuple[str, ...]) -> ProjectSubgraph`：这些边、端点与端点 1 跳内的边（上限 200 条）。
    - `async def nodes_for_chunks(self, scope, chunk_ids: tuple[str, ...]) -> tuple[str, ...]`：经 `node_sources` 与 `edge_evidence`（边两端）反查，按首次出现去重。
    - `async def match_aliases(self, scope, text: str) -> tuple[AliasMatch, ...]`
    - `async def nodes(self, scope, node_ids: tuple[str, ...]) -> tuple[ProjectNode, ...]`：按给定顺序返回存在的节点（PR 3 用它把 `nodes_for_chunks` 与 `match_aliases` 的 id 变成节点）。
  - 版本钉住（PR 3 的 STALE 判定依赖此语义）：`overview`、`search`、`neighbors`、`path`、`node_detail`、`highlight`、`nodes_for_chunks`、`match_aliases`、`nodes` 都接受关键字参数 `version: int | None = None`；`None` 表示当前 READY 版本；给定版本仍在 `ProjectGraphCache` 保留窗口内（`keep_per_project=2`）则按该版本查询，否则抛 `ProjectGraphVersionMismatch(current: int)`（定义在 `ports/project_store.py`，HTTP 层映射为 409）。`ProjectSubgraph` 与 `ProjectNodeDetail` 都带 `graph_version: int`。
  - 来源过滤语义（所有带 `source_revision_ids` 的方法）：非空时，节点须至少一条 `NodeSource.source_revision_id` 在集合内，边须两端可见且至少一条 `EdgeEvidence.source_revision_id` 在集合内；返回的 `sources`/`evidence` 也只含集合内的行。
- Produces（`application/project_queries.py`）：
  - `@dataclass(frozen=True, slots=True) class LoadedProjectGraph`：`version: ProjectGraphVersion`、`nodes: Mapping[str, ProjectNode]`、`edges: Mapping[str, ProjectEdge]`、`adjacency: Mapping[str, tuple[tuple[str, str], ...]]`（节点 → (邻居, edge_id)）、`node_sources: Mapping[str, tuple[NodeSource, ...]]`、`edge_evidence: Mapping[str, tuple[EdgeEvidence, ...]]`、`chunk_nodes: Mapping[str, tuple[str, ...]]`、`communities: tuple[Community, ...]`、`alias_index: AliasIndex`；`@classmethod from_draft(cls, version: ProjectGraphVersion, draft: ProjectGraphDraft) -> LoadedProjectGraph`；同步方法 `overview/search/neighbors/path/node_detail/highlight/nodes_for_chunks/match_aliases`，参数与端口一致（去掉 `scope`）。
  - `class ProjectGraphCache`：`__init__(self, *, keep_per_project: int = 2)`；`get(self, project_id: str, version: int) -> LoadedProjectGraph | None`；`put(self, graph: LoadedProjectGraph) -> None`（同项目超过 `keep_per_project` 个版本时淘汰最旧）；`invalidate(self, project_id: str) -> None`；`loaded_versions(self, project_id) -> tuple[int, ...]`。
  - `class InMemoryProjectGraphStore(ProjectGraphStorePort)`：`async def publish(self, scope, draft: ProjectGraphDraft, *, now: datetime) -> ProjectGraphVersion`（版本号递增，摘要相同则返回现有版本）；`async def mark_merging(self, scope) -> None`（供 `GET /project` 测试展示 MERGING）。
- Produces（`MysqlProjectGraphStore`）：`__init__(self, sessions: async_sessionmaker[AsyncSession], *, cache: ProjectGraphCache | None = None)`；`get_current` 读 `status="READY"` 的最大版本；其余方法先 `get_current`，缓存未命中时 `_load(scope, version) -> LoadedProjectGraph`（八张表各一条 `select ... where project_id, version`，不经 `GraphSnapshotDraft`），`put` 后委托 `LoadedProjectGraph`；没有 READY 版本抛 `ProjectGraphNotReady`。

- [ ] **Step 1: 写失败的单元测试**

```python
# tests/unit/graph/test_project_queries.py
def test_cache_keeps_two_versions_per_project_and_invalidates():
    cache = ProjectGraphCache(keep_per_project=2)
    for version in (1, 2, 3):
        cache.put(_loaded(version))
    assert cache.loaded_versions("tapper-demo") == (2, 3) and cache.get("tapper-demo", 1) is None
    cache.invalidate("tapper-demo")
    assert cache.loaded_versions("tapper-demo") == ()

def test_overview_rotates_across_communities_by_degree(): ...     # 两个社区各 5 节点、node_limit 4 → 每社区 2 个且是度数最高者
def test_path_respects_max_hops(): ...                            # 4 跳链路 max_hops=3 → 空；max_hops=3 的 3 跳链路 → 4 节点 3 边
def test_highlight_returns_edges_endpoints_and_one_hop_context(): ...
def test_nodes_for_chunks_uses_node_sources_and_edge_evidence(): ...
def test_loading_ten_thousand_nodes_takes_under_one_second():
    draft = _synthetic_draft(nodes=10_000, edges=50_000)
    started = time.perf_counter(); LoadedProjectGraph.from_draft(_version(1), draft)
    assert time.perf_counter() - started < 1.0
```

```python
# tests/contract/test_project_graph_store_contract.py（InMemoryProjectGraphStore）
async def test_store_requires_a_ready_version(): ...              # 未发布 → get_current 为 None，overview 抛 ProjectGraphNotReady
async def test_same_digest_republish_returns_existing_version(): ...
async def test_cross_project_reads_see_no_graph(): ...
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_project_queries.py tests/contract/test_project_graph_store_contract.py -v`
Expected: FAIL（`ModuleNotFoundError: project_queries`）

- [ ] **Step 3: 实现端口、`LoadedProjectGraph`、`ProjectGraphCache`、`InMemoryProjectGraphStore`**

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/graph tests/contract/test_project_graph_store_contract.py -v`
Expected: PASS

- [ ] **Step 5: 写失败的 MySQL 集成测试（追加到 `test_mysql_project_graph.py`）**

```python
@pytest.mark.asyncio
async def test_version_switch_invalidates_cache_and_serves_new_version(owned_project_mysql):
    # 发布 v1 → store.overview() 命中 v1；发布 v2 → get_current 为 v2，overview 节点来自 v2，cache.loaded_versions == (1, 2)

@pytest.mark.asyncio
async def test_overview_source_filter_drops_nodes_without_in_filter_evidence(owned_project_mysql):
    # 节点 A 证据只在 rev-1，节点 B/C 证据在 rev-2，边 A-B 证据在 rev-1，边 B-C 证据在 rev-2
    # overview(source_revision_ids=("rev-2",)) → 节点 {B, C}，边 {B-C}；evidence 全部 source_revision_id == "rev-2"
    # neighbors(B, source_revision_ids=("rev-2",)) 同样不含 A

@pytest.mark.asyncio
async def test_mysql_queries_are_project_scoped(owned_project_mysql): ...   # 其他 project 的 scope get_current 为 None
```

- [ ] **Step 6: 实现 `MysqlProjectGraphStore`**

- [ ] **Step 7: 运行集成测试确认通过**

Run: `uv run pytest tests/integration/test_mysql_project_graph.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add apps/tap-ai-backend/src/tap/modules/graph apps/tap-ai-backend/tests
git commit -m "feat: serve project graph queries from a per-version adjacency cache"
```

---

### Task 4: 合并队列、`project_merge` worker、触发器、配置与 `graph rebuild` CLI

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/merge_jobs.py`（端口与内存队列）
- Create: `apps/tap-ai-backend/src/tap/modules/graph/application/merge_worker.py`
- Create: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_merge.py`（`MysqlProjectMergeQueue`、`MysqlMergeInputs`）
- Create: `apps/tap-ai-backend/src/tap/entrypoints/graph_project_knowledge.py`（`MysqlCurrentRevisions`，知识库表只在 entrypoints 读取，同 `prompt_suggestion_knowledge.py` 的理由）
- Create: `apps/tap-ai-backend/src/tap/entrypoints/graph_operator.py`、`scripts/graph-operator.py`
- Modify: `apps/tap-ai-backend/src/tap/modules/graph/adapters/mysql_jobs.py:45-47`（`MysqlGraphJobStore.__init__` 加 `merge_queue`）、`:211-277`（`complete` 内请求合并）、新增 `reset_for_profile`
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/mysql_documents.py:795-820`（`SourceDeletedProjection` 协议与构造参数）、`:973-1060`（`_delete_source_once` 调用钩子）
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/adapters/mysql_review.py:1869`（`MysqlKnowledgeReviewRepository.publish` 末尾调用可选 `publication_projection.after_publication_changed`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py:95-135`（设置字段）、`:355-420`（解析）、`:917-945`（`_build_document_repository` 注入删除钩子）、`:948-999`（`create_graph_worker_runtime` 挂 `pending_work`）
- Modify: `.env.example:104` 之后、`Makefile:339-341` 之后（`graph-rebuild` 目标）
- Test: `apps/tap-ai-backend/tests/unit/graph/test_merge_worker.py`（新建）、`tests/unit/test_tapper_settings.py`（PR 1 创建）、`tests/integration/test_project_merge_mysql.py`（新建）、`tests/unit/test_graph_operator.py`（新建）

**Interfaces:**
- Consumes：Task 1 `ProjectGraphMerger`、`FragmentRecord`、`fragment_digest`；Task 2 `publish_project_version`、`prune_project_versions`、`load_fragment_draft`；Task 3 `ProjectGraphCache.invalidate`。
- Produces（`merge_jobs.py`）：
  - `@dataclass(frozen=True, slots=True) class MergeClaim(project_id: str, lease_owner: str, lease_token: str, lease_expires_at: datetime, claimed_due_at: datetime, reason: str, attempt: int)`
  - `class ProjectMergeLeaseLost(Exception)`
  - `class ProjectMergeQueue(Protocol)`：`async def request(self, scope, *, reason: str, now: datetime) -> None`；`async def claim(self, scope, *, worker_id: str, now: datetime, lease_duration: timedelta) -> MergeClaim | None`；`async def renew(self, scope, claim, *, now, lease_duration) -> MergeClaim`；`async def complete(self, scope, claim, draft: ProjectGraphDraft, *, now) -> ProjectGraphVersion | None`（摘要与当前 READY 版本相同返回 `None` 且不写版本；否则发布 `version = 当前最大 + 1`、`prune_project_versions(keep_latest=2)`；`claimed_due_at == due_at` 时清空 `due_at`，否则保留以便下一轮）；`async def fail(self, scope, claim, *, failure_code: str, now) -> None`（`attempt_count + 1`，`due_at = now + min(60 × 2**attempt, 900) 秒`）。
  - `class MergeInputsPort(Protocol)`：`async def load_fragments(self, scope) -> tuple[FragmentRecord, ...]`。
  - `class InMemoryProjectMergeQueue(ProjectMergeQueue)`：内含 `InMemoryProjectGraphStore`，属性 `store`。
- Produces（`mysql_merge.py`）：
  - `MysqlProjectMergeQueue(sessions, *, cache: ProjectGraphCache | None = None)`；另有 `request_in_transaction(session, scope, *, reason, now)`（`insert ... on_duplicate_key_update(due_at=now, last_reason=reason, updated_at=now)`）。`claim/renew/complete/fail` 都先 `SELECT ... FOR UPDATE` 校验 `lease_token`、`lease_expires_at > now`，不符抛 `ProjectMergeLeaseLost`（同 `mysql_jobs.py:337-352` 的 `_owned_row`）。`complete` 成功后 `cache.invalidate(project_id)` 并写审计 `AuditAction.GRAPH_SNAPSHOT_READY`、事件 `knowledge.graph-project.ready`（payload：`graphVersion`、`fragmentDigest`、`nodeCount`、`edgeCount`），`idempotency_key=f"graph-project:{version}"`。
  - `MysqlMergeInputs(sessions, current_revisions: CurrentRevisionsPort)`：`load_fragments` = `graph_extraction_job ⋈ graph_snapshot ⋈ graph_snapshot_revision`，条件 `graph_snapshot.status IN ("READY", "PARTIAL")`、`extraction_profile_digest == GRAPH_EXTRACTION_PROFILE_DIGEST`、`revision_id IN current_revisions`，每条经 `load_fragment_draft`。
  - `class CurrentRevisionsPort(Protocol)`（放在 `graph/ports/project_store.py`）：`async def current_revision_ids(self, scope) -> frozenset[str]`。
- Produces（`entrypoints/graph_project_knowledge.py`）：`MysqlCurrentRevisions(sessions, *, scope)`，`current_revision_ids` = `MysqlReadySources(sessions, scope).list_sources()` 各项的 `revision_id`（该查询已排除 `deleted_at` 非空的来源与文档，并只含已发布就绪来源，上限 100 份）。
- Produces（`merge_worker.py`）：`ProjectGraphMergeWorker(*, queue: ProjectMergeQueue, inputs: MergeInputsPort, merger: ProjectGraphMerger, scope, worker_id: str, lease_duration: timedelta = timedelta(seconds=300), embeddings: Callable[[Sequence[str]], Awaitable[Sequence[tuple[float, ...]]]] | None = None)`；`async def run_once(self, limit: int) -> int`：`claim` 为空返回 0；否则在 `bind_trace(job_kind="project_merge")` 与 `span("job.project_merge")` 内 `load_fragments` → `renew` → `merge` → `complete`；`embeddings` 非空时先对全部实体 label 取向量作为 `merge(embeddings=...)`；异常 → `fail(failure_code=type(exc).__name__[:64])`；`ProjectMergeLeaseLost` → 放弃且不发布；返回 1。
- Produces（触发）：
  - `MysqlGraphJobStore.__init__(self, sessions, *, merge_queue: MysqlProjectMergeQueue | None = None)`；`complete` 在 `publish_graph_snapshot` 之后、同一事务内 `merge_queue.request_in_transaction(session, scope, reason="fragment-ready", now=now)`。
  - `class SourceDeletedProjection(Protocol)`：`async def after_source_deleted(self, session, scope, source_id: str, *, now: datetime) -> None`；`MysqlDocumentRepository(..., source_deleted_projection: SourceDeletedProjection | None = None)`，`_delete_source_once` 在更新 `knowledge_source.deleted_at` 之后调用；实现 `GraphMergeOnSourceChange(queue)`（在 `mysql_merge.py`），同时实现 `after_publication_changed(self, session, scope, *, now)` 供 `MysqlKnowledgeReviewRepository.publish` 调用，reason 分别为 `"source-deleted"`、`"publication-changed"`。
  - `MysqlGraphJobStore.reset_for_profile(self, scope, revision_id: str, *, extraction_profile_digest: str, model_alias: str, now: datetime) -> GraphJob`：按 `revision_id` 锁定 job 行，删除该 snapshot 的 `graph_inference_provenance`、`graph_edge_evidence`、`graph_node_evidence`、`graph_edge`、`graph_node`、`graph_snapshot_revision`、`graph_fragment_batch` 行，快照置回 `CANDIDATE`，job 行改写为新 `request_digest`/`extraction_profile_digest`/`model_alias`、`status=PENDING`、`attempt_count=0`、租约清空；写审计 key `f"{job_id}:rebuild:{digest[7:23]}"`；job 为 RUNNING 且租约未过期时抛 `ValueError("graph job is running")`。
- Produces（设置）：`TapperSettings.graph_align_embedding: bool`（`TAPPER_GRAPH_ALIGN_EMBEDDING`，`_fixed_choice` 取值 `{"0","1"}`，默认 `"0"`）、`graph_align_threshold: float`（`TAPPER_GRAPH_ALIGN_THRESHOLD`，`_duration(values, name, 0.92, maximum=1.0)`）、`graph_overview_limit: int`（`TAPPER_GRAPH_OVERVIEW_LIMIT`，`_integer(... 150, minimum=10, maximum=500)`）。`create_graph_worker_runtime` 构造 `MysqlProjectMergeQueue(sessions, cache=None)`、`MysqlMergeInputs(sessions, MysqlCurrentRevisions(sessions, scope=scope))`、`ProjectGraphMergeWorker(...)`，并把 `GraphWorker(jobs=MysqlGraphJobStore(sessions, merge_queue=queue))`、`WorkerRuntime(..., pending_work=merge_worker.run_once)` 接起来；`graph_align_embedding` 为真时 `embeddings` 取 `_create_embeddings(settings, ...)` 的向量函数。`_build_document_repository` 传入 `source_deleted_projection=GraphMergeOnSourceChange(MysqlProjectMergeQueue(sessions))`；`MysqlKnowledgeReviewRepository` 的构造处传同一对象为 `publication_projection`。
- Produces（CLI，`entrypoints/graph_operator.py`，风格同 `knowledge_operator.py:37-52, 194-229`）：`parse_arguments(arguments) -> GraphOperation`，argparse 子命令 `graph rebuild --project <id>`（`--project` 仅接受 `VALIDATION_SCOPE.project_id`）、`--limit`（默认 100，1–500）、`--interval-seconds`（默认 1.0，限速）；`async def run(*, settings, operation) -> dict[str, int]` 对 `MysqlCurrentRevisions` 的每个修订读取 `knowledge_document_revision.chunks_blob_locator` 后调用 `reset_for_profile(... extraction_profile_digest=GRAPH_EXTRACTION_PROFILE_DIGEST, model_alias=settings.default_chat_model)`，每次之间 `asyncio.sleep(interval)`，最后 `queue.request(reason="rebuild")`；输出 JSON `{"requeuedCount": n, "skippedCount": m}`（RUNNING 的跳过）。`cli()` 退出码 0/1/130。Make 目标 `graph-rebuild: ## requeue graph extraction for every published revision; pass ARGS` → `uv run --project apps/tap-ai-backend python scripts/graph-operator.py $(ARGS)`。
- `.env.example` 在 `TAPPER_GRAPH_BATCH_RETRIES=3` 之后追加 `TAPPER_GRAPH_ALIGN_EMBEDDING=0`、`TAPPER_GRAPH_ALIGN_THRESHOLD=0.92`、`TAPPER_GRAPH_OVERVIEW_LIMIT=150`。

- [ ] **Step 1: 写失败的单元测试（内存队列与 worker）**

```python
# tests/unit/graph/test_merge_worker.py
async def test_merge_worker_publishes_a_version_and_clears_due():
    # 队列 request 一次；inputs 返回两个片段 → run_once 返回 1；queue.store.get_current().version == 1；再 run_once 返回 0
async def test_unchanged_digest_skips_publication(): ...          # 同样片段再次 request → run_once 返回 1 但 get_current().version 仍为 1
async def test_request_during_merge_keeps_queue_due(): ...        # inputs 在 load_fragments 内再 request → complete 后 claim 仍能取到
async def test_failure_backs_off_and_records_code(): ...         # inputs 抛 RuntimeError → fail，failure_code "RuntimeError"，due_at == now+60s
async def test_lease_lost_is_not_published(): ...                 # queue.renew 抛 ProjectMergeLeaseLost → get_current() 为 None
```

```python
# 追加到 tests/unit/test_tapper_settings.py
def test_graph_alignment_settings_defaults_and_bounds():
    settings = TapperSettings.from_mapping({**_minimal_env()})
    assert (settings.graph_align_embedding, settings.graph_align_threshold, settings.graph_overview_limit) == (False, 0.92, 150)
    with pytest.raises(ValueError):
        TapperSettings.from_mapping({**_minimal_env(), "TAPPER_GRAPH_ALIGN_THRESHOLD": "1.5"})
```

```python
# tests/unit/test_graph_operator.py
def test_graph_rebuild_arguments_bind_the_validation_project():
    operation = parse_arguments(["graph", "rebuild", "--project", VALIDATION_SCOPE.project_id, "--limit", "5"])
    assert operation.command == "rebuild" and operation.limit == 5 and operation.interval_seconds == 1.0
    with pytest.raises(SystemExit):
        parse_arguments(["graph", "rebuild", "--project", "other"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/graph/test_merge_worker.py tests/unit/test_tapper_settings.py tests/unit/test_graph_operator.py -v`
Expected: FAIL（`ModuleNotFoundError`、`AttributeError: graph_align_embedding`）

- [ ] **Step 3: 实现 `merge_jobs.py`、`merge_worker.py`、设置字段与解析、`graph_operator.py`、`scripts/graph-operator.py`、Make 目标、`.env.example`**

- [ ] **Step 4: 运行单元测试确认通过**

Run: `uv run pytest tests/unit/graph tests/unit/test_tapper_settings.py tests/unit/test_graph_operator.py -v`
Expected: PASS

- [ ] **Step 5: 写失败的 MySQL 集成测试**

```python
# tests/integration/test_project_merge_mysql.py（用 MysqlGraphStore.publish 发布片段快照，用 MysqlGraphJobStore 建 job 行；CurrentRevisions 用可变集合的 fake）
async def test_full_replay_merges_cross_fragment_entities(owned_project_mysql):
    # rev-1 片段 "健康告知" ENTITY，rev-2 片段 "健康告知" ENTITY + 边 → 合并后一个节点、两条 node_source、graph_merge_log 一行 rule EXACT；版本 1 READY

async def test_replay_after_source_delete_drops_orphan_nodes(owned_project_mysql):
    # 先合并 rev-1 + rev-2（节点 A 只来自 rev-1）；current_revisions 去掉 rev-1 并 request(reason="source-deleted") → 版本 2 不含 A、不含 A 的别名与边；其他节点 node_id 与版本 1 相同；版本 1 仍可查、版本 0 不存在

async def test_lost_lease_cannot_publish_duplicate_version(owned_project_mysql):
    # worker-1 claim 后把 lease_expires_at 改到过去；worker-2 claim 并 complete → 版本 1；worker-1 complete 抛 ProjectMergeLeaseLost；
    # graph_project_version 中 status=READY 且 fragment_digest 相同的行恰好 1 行

async def test_fragment_completion_enqueues_merge_in_the_same_transaction(owned_project_mysql):
    # MysqlGraphJobStore(sessions, merge_queue=queue).complete(...) 后 graph_project_merge_job.due_at 非空、last_reason == "fragment-ready"

async def test_reset_for_profile_clears_fragment_and_requeues_job(owned_project_mysql):
    # READY 片段 + 两条批次行 → reset_for_profile 后 job PENDING、新 request_digest、graph_node/graph_fragment_batch 为 0 行、快照 CANDIDATE
```

- [ ] **Step 6: 实现 `mysql_merge.py`、`graph_project_knowledge.py`、`MysqlGraphJobStore` 的 `merge_queue` 与 `reset_for_profile`、`SourceDeletedProjection` 钩子、`publish` 钩子与运行时接线**

- [ ] **Step 7: 运行集成测试与相关现有测试确认通过**

Run: `uv run pytest tests/integration/test_project_merge_mysql.py tests/integration/test_mysql_graph_store.py tests/integration/test_graph_snapshot_publication.py tests/integration/test_document_ledger.py tests/unit -k "graph or settings or operator" -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add apps/tap-ai-backend/src apps/tap-ai-backend/tests apps/tap-ai-backend/migrations scripts/graph-operator.py Makefile .env.example
git commit -m "feat: merge published graph fragments into project versions with a leased queue"
```

---

### Task 5: 项目图 HTTP 读接口、契约模型、409 与片段重试

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/contracts/http.py:1196-1258`（新增 `ProjectGraph*` 模型；放宽 `GraphSearchRequest.snapshot_id`、`GraphPathRequest.snapshot_id` 为可选并加项目图字段）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/routes/knowledge_graph.py`
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/dependencies.py:232-250`（`HttpServices.project_graph: ProjectGraphStorePort | None`、`graph_jobs: object | None`、`project_graph_service(request)`、`class GraphVersionMismatch(Exception)`）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/problems.py:147-150` 旁（`GraphVersionMismatch` → 409 `/graph-version-mismatch`；`ProjectGraphNotReady` → 空 `EMPTY` 响应由路由处理，不映射为错误）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py:1592-1599`、`:1725`（构造 `MysqlProjectGraphStore(graph_sessions, cache=ProjectGraphCache())` 与 `MysqlGraphJobStore(graph_sessions)` 注入 `HttpServices`）
- Test: `apps/tap-ai-backend/tests/contract/test_graph_http.py`（追加）、`tests/contract/test_project_graph_http.py`（新建）

**Interfaces:**
- Consumes：Task 3 `ProjectGraphStorePort`、`ProjectGraphNotReady`；Task 4 `MysqlGraphJobStore.load_batches/record_batch`（PR 1）与新方法 `retry_failed_batches`。
- Produces（契约模型，字段名经 `ContractModel` 转 camelCase）：
  - `ProjectGraphCommunityView(community_id, label, size)`
  - `ProjectGraphView(graph_version: int | None, status: Literal["EMPTY", "MERGING", "READY", "FAILED"], node_count: int, edge_count: int, communities: list[ProjectGraphCommunityView], merged_at: datetime | None, extracting_revision_ids: list[str], partial_revision_ids: list[str])`
  - `ProjectGraphNodeView(node_id, label, node_type, canonical_key, degree, community_id: str | None, aliases: list[str])`
  - `ProjectGraphEdgeView(edge_id, source_node_id, target_node_id, relation_type, relation_label, origin: Literal["EXTRACTED", "INFERRED"], confidence)`
  - `ProjectGraphEvidenceView(owner_kind: Literal["node", "edge"], owner_id, source_revision_id, document_revision_id, chunk_id, anchor: dict[str, object], content_digest: CanonicalSha256 | None, snippet: str | None = None)`：`snippet` 只在 `GET /nodes/{node_id}` 中填充（≤300 字，经现有知识切片读取路径取正文），其他接口为 `None`。
  - `ProjectGraphSubgraphView(graph_version: int, nodes, edges, evidence: list[ProjectGraphEvidenceView] = [])`
  - `ProjectGraphRelationGroupView(relation_type, edges: list[ProjectGraphEdgeView])`、`ProjectGraphSourceGroupView(source_revision_id, document_revision_id, source_name: str | None, evidence: list[ProjectGraphEvidenceView])`（`source_name` 经知识来源读取路径按修订解析，解析不到为 `None`）
  - `ProjectGraphNodeDetailView(graph_version, node: ProjectGraphNodeView, community: ProjectGraphCommunityView | None, sources: list[ProjectGraphSourceGroupView], relations: list[ProjectGraphRelationGroupView], neighbors: list[ProjectGraphNodeView])`
  - `GraphSearchRequest`：`snapshot_id: str | None = None`（有值走旧片段分支）、`query`、`node_limit`、新增 `source_revision_ids: list[str] = []`（≤50）、`graph_version: int | None = None`。
  - `GraphPathRequest`：`snapshot_id: str | None = None`、`source_node_id`、`target_node_id`、`node_limit`、新增 `max_hops: Annotated[StrictInt, Field(ge=1, le=3)] = 3`、`source_revision_ids`、`graph_version`。
  - `ProjectGraphNeighborRequest(node_id, depth: 1..2 = 1, node_limit: 1..500 = 50, source_revision_ids, graph_version)`
  - `ProjectGraphHighlightRequest(edge_ids: list[str]`（1–50）`, graph_version: int | None = None)`
  - `GraphFragmentRetryView(revision_id, requeued_batches: int, job_status: Literal["PENDING", "RUNNING", "READY", "FAILED"])`
- Produces（路由，前缀不变 `/knowledge/graph`，鉴权 `knowledge.read`；重试为 `knowledge.write`）：
  - `GET /project` `operation_id="graph_get_project"`：无 READY 版本且队列无 `due_at` → `status="EMPTY"`、`graph_version=None`；队列 `due_at` 非空或正在租约中 → `MERGING`（需要 `graph_jobs` 提供 `merge_state(scope) -> Literal["IDLE","PENDING","RUNNING","FAILED"]`，`MysqlProjectMergeQueue.merge_state`）；`extracting_revision_ids` = job PENDING/RUNNING 的修订，`partial_revision_ids` = 快照 PARTIAL 的修订（`MysqlGraphJobStore.list_fragment_states(scope) -> tuple[tuple[str, str, str], ...]`：revision_id、job status、snapshot status）。
  - `GET /overview?sourceRevisionId[]&communityId[]&nodeLimit&graphVersion` `graph_get_overview`：`nodeLimit` 默认取 `settings.graph_overview_limit`（经 `HttpServices.graph_overview_limit: int = 150` 注入）。
  - `POST /query` `graph_search`：`snapshot_id` 有值 → 现有行为与 `GraphSubgraphView`；否则项目图 `search` → `ProjectGraphSubgraphView`；`response_model=ProjectGraphSubgraphView | GraphSubgraphView`。
  - `POST /neighbors` `graph_project_neighbors`（新路径；旧 `POST /nodes/{node_id}/neighbors` 原样保留）。
  - `POST /path` `graph_bounded_path`：同 `/query` 的双分支。
  - `GET /nodes/{node_id}?snapshotId&graphVersion` `graph_get_node`：`snapshotId` 有值走旧分支；否则 `ProjectGraphNodeDetailView`。
  - `POST /highlight` `graph_highlight`。
  - `POST /fragments/{revision_id}/retry` `graph_retry_fragment`：`MysqlGraphJobStore.retry_failed_batches(scope, revision_id, *, now) -> tuple[int, GraphJobStatus]`：FAILED 批次改 PENDING、`attempt=0`、job 置 PENDING（RUNNING 且租约未过期 → `GraphVersionMismatch` 之外的新异常 `GraphJobBusy` → 409 `/graph-job-busy`）；无 FAILED 批次返回 `(0, 当前状态)`。
  - 所有项目图路由：请求 `graph_version` 非空且 ≠ `get_current().version` → 抛 `GraphVersionMismatch` → 409，problem `type` 以 `/graph-version-mismatch` 结尾，`detail` 含当前版本号；`ProjectGraphNotReady` → `GET /project` 返回 `EMPTY`，其余路由返回 `ProjectGraphSubgraphView(graph_version=0, nodes=[], edges=[])`。
  - `GET /snapshots`、`GET /evidence/{evidence_id}`、`POST /nodes/{node_id}/neighbors` 保持 `knowledge_graph.py:29-129` 原样。

- [ ] **Step 1: 写失败的契约测试**

```python
# tests/contract/test_project_graph_http.py（services = replace(validation_http_services(), graph=InMemoryGraphStore(), project_graph=InMemoryProjectGraphStore(), graph_jobs=InMemoryGraphJobStore())）
def test_project_graph_routes_exist_with_camel_case_schemas():
    # paths 含 /project /overview /neighbors /highlight /fragments/{revision_id}/retry；ProjectGraphView.properties 含 graphVersion、extractingRevisionIds
def test_project_is_empty_before_any_merge(): ...                 # GET /project → {"status": "EMPTY", "graphVersion": None, "nodeCount": 0}
def test_overview_query_neighbors_path_and_highlight_carry_graph_version(): ...   # 发布 v1 后五个接口 200 且 graphVersion == 1
def test_stale_graph_version_is_409(): ...                        # body graphVersion 99 → 409，type 以 /graph-version-mismatch 结尾
def test_query_without_snapshot_id_uses_project_graph(): ...      # {"query": "健康"} → nodes 含别名命中；{"snapshotId": "snapshot-1", "query": "*"} 仍走旧分支
def test_node_detail_groups_sources_and_relations(): ...
def test_fragment_retry_requeues_failed_batches(): ...            # 预置 FAILED 批次 → 200 {"requeuedBatches": 1, "jobStatus": "PENDING"}
```

```python
# 追加到 tests/contract/test_graph_http.py
def test_legacy_snapshot_routes_still_serve_fragment_graphs(): ...  # /snapshots、/nodes/{id}?snapshotId、/evidence、/nodes/{id}/neighbors 与改动前断言一致
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/contract/test_project_graph_http.py tests/contract/test_graph_http.py -v`
Expected: FAIL（404 或 `TypeError: project_graph`）

- [ ] **Step 3: 实现契约模型、依赖、异常映射、路由与运行时注入**

- [ ] **Step 4: 运行契约测试确认通过**

Run: `uv run pytest tests/contract -k graph -v`
Expected: PASS

- [ ] **Step 5: 重新生成契约并确认前端类型编译**

Run（仓库根目录）: `make contracts && corepack pnpm --dir apps/tap-ai-frontend exec tsc -b`
Expected: `contracts/` 与 `apps/tap-ai-frontend/src/shared/api/generated/schema.ts` 出现 `ProjectGraphView` 等类型；`GraphSearchRequest.snapshotId` 变为可选后 tsc 无错误（前端现有调用都传 `snapshotId`）。

- [ ] **Step 6: 提交**

```bash
git add apps/tap-ai-backend/src apps/tap-ai-backend/tests contracts apps/tap-ai-frontend/src/shared/api/generated
git commit -m "feat: expose project graph overview, search, path, detail and highlight routes"
```

---

### Task 6: `GraphAnswerEnricher` 改读项目图

**Files:**
- Modify: `apps/tap-ai-backend/src/tap/modules/knowledge/application/graph_enrichment.py`（整体改写，保留 `GraphContextStatus`、`GraphAnswerContext` 名称）
- Modify: `apps/tap-ai-backend/src/tap/interfaces/http/knowledge_service.py:430-436`（传 `chunk_ids=()`）
- Modify: `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py:1592-1599`（注入 `MysqlProjectGraphStore`）
- Test: `apps/tap-ai-backend/tests/unit/knowledge/test_graph_enrichment.py`（重写）

**Interfaces:**
- Consumes：Task 3 `ProjectGraphStorePort`（`get_current`、`nodes_for_chunks`、`match_aliases`、`neighbors`、`node_detail`）、`ProjectGraphNotReady`；`PublishedKnowledgeAuthority.authorize_selection/authorize_evidence/revalidate`（`publication.py:58-115`）。
- Produces：
  - `GraphContextStatus` 枚举值不变：`APPLIED`、`NOT_READY`、`FAILED`、`UNAVAILABLE`、`NOT_SELECTED`。
  - `GraphAnswerContext(status: GraphContextStatus, snapshot_id: str | None = None, facts: tuple[Mapping[str, object], ...] = (), graph_version: int | None = None, seed_node_ids: tuple[str, ...] = ())`；`snapshot_id` 在 APPLIED 时为 `ProjectGraphVersion.version_id`（`"gpv_<version>"`，继续写入 Turn 的 `graph_snapshot_id`）；原有校验保留，另要求 `graph_version` 与 APPLIED 同在。这是 PR 3 关系分析子图的输入基础：`seed_node_ids` 与 `graph_version` 直接复用。
  - `GraphAnswerEnricher.__init__(self, store: ProjectGraphStorePort, *, node_limit: int = 20, edge_limit: int = 60, publication_authority: PublishedKnowledgeAuthority | None = None)`；`node_limit` 1–60、`edge_limit` 1–200。
  - `async def enrich(self, scope: ProjectScopeContext, source_revision_ids: tuple[str, ...], query: str, *, chunk_ids: tuple[str, ...] = ()) -> GraphAnswerContext`，在 `span("graph.enrich")` 内（PR 3 再拆为 `graph.seed/expand/path`）：
    1. 无来源 → `NOT_SELECTED`。
    2. `authorize_selection`（有授权器时）；`get_current()` 为 `None` → `NOT_READY`。
    3. 种子 = `nodes_for_chunks(chunk_ids)` ∪ `match_aliases(query)` 的 node_id（保持顺序去重）；为空 → `NOT_READY`。
    4. 扩展：每个种子 `neighbors(depth=1 if len(seeds) >= 3 else 2, node_limit=node_limit, source_revision_ids=source_revision_ids)`，合并节点（≤ `node_limit`）与边（≤ `edge_limit`，种子相邻的边优先）。
    5. 证据校验：每条 `NodeSource`/`EdgeEvidence` 的 `source_revision_id` 须在 `source_revision_ids` 内，否则 `FAILED`；有授权器时逐条 `authorize_evidence(project_id, source_revision_id=..., document_revision_id=..., approved_item_id=anchor.get("inventoryItemId"))` 后 `revalidate(publication)`，任一异常 → `FAILED`。
    6. `ProjectGraphNotReady`/`GraphFactNotFound`/`AuthorizationDenied` → `FAILED`；其他异常 → `UNAVAILABLE`。
    7. facts：节点 `{"kind": "node", "id", "label", "type", "aliases", "communityId", "seed": bool, "evidence": [...]}`；边 `{"kind": "edge", "id", "sourceNodeId", "targetNodeId", "relationType", "relationLabel", "origin", "confidence", "evidence": [...]}`；`evidence` 项沿用 `graph_enrichment.py:154-161` 的 `_evidence_locator` 字段。span 属性 `tap.graph.version`、`tap.graph.seed_count`、`tap.graph.node_count`、`tap.graph.edge_count`。
  - `knowledge_service.py:432` 的调用改为 `enrich(self.scope, tuple(frozen_input.source_revision_ids), domain_request.query, chunk_ids=())`：该调用先于检索执行，本 PR 只用问题种子；检索切片种子由 PR 3 把调用移到检索之后时接入。

- [ ] **Step 1: 重写失败的单元测试**

```python
# tests/unit/knowledge/test_graph_enrichment.py（store = InMemoryProjectGraphStore，发布含别名 "健康告知书"/"健康告知" 与两条边的草稿）
async def test_question_seeds_come_from_alias_longest_match_not_substring():
    result = await GraphAnswerEnricher(store).enrich(VALIDATION_SCOPE, ("rev-1",), "投保时健康告知书需要什么材料？")
    assert result.status is GraphContextStatus.APPLIED and result.graph_version == 1
    assert result.seed_node_ids == (node_long,) and result.snapshot_id == "gpv_1"
    assert any(f["kind"] == "edge" and f["relationLabel"] for f in result.facts)

async def test_chunk_seeds_join_question_seeds(): ...             # chunk_ids=("chunk-9",) 命中节点 C → seed_node_ids 含 C
async def test_multi_source_selection_uses_one_project_graph(): ...   # ("rev-1", "rev-2") → APPLIED（旧实现为 NOT_READY）
async def test_evidence_outside_selection_is_filtered_or_failed(): ...   # ("rev-2",) 时只剩 rev-2 证据的节点；无剩余种子 → NOT_READY
async def test_no_ready_version_is_not_ready_and_errors_are_fail_soft(): ...   # 空 store → NOT_READY；store.get_current 抛 RuntimeError → UNAVAILABLE
async def test_publication_authority_denial_is_failed(): ...      # authorize_selection 抛 AuthorizationDenied → FAILED
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/knowledge/test_graph_enrichment.py -v`
Expected: FAIL（`TypeError: chunk_ids`、`AttributeError: graph_version`）

- [ ] **Step 3: 改写 `graph_enrichment.py`，更新 `knowledge_service.py` 调用与 `tapper_runtime.py` 注入**

- [ ] **Step 4: 运行相关测试确认通过**

Run: `uv run pytest tests/unit/knowledge tests/unit/chat tests/contract -k "graph or enrich or answer" -v`
Expected: PASS（`graph_snapshot_id` 继续满足 `conversations.py:292-296` 与 `http.py:890-900` 的 APPLIED 配对校验）

- [ ] **Step 5: 提交**

```bash
git add apps/tap-ai-backend/src apps/tap-ai-backend/tests/unit/knowledge
git commit -m "feat: seed answer graph context from the project graph aliases and chunks"
```

---

### Task 7: 文档、全量检查与隔离 E2E

**Files:**
- Modify: `docs/architecture.md:57-63`（图谱数据流：片段抽取 → 项目合并 → 回答种子/扩展；第 86–87 行已知差距表更新"每次查询把整个快照载入内存"为已解决、"脉络分析"行改为"PR 3 接入关系引用"）
- Modify: `docs/superpowers/plans/2026-09-29-v1-roadmap.md:20`、`docs/superpowers/plans/2026-10-01-product-roadmap.md`（"知识图谱脉络分析"行标注 PR 2 已合并、PR 3 进行中）
- Modify: `docs/superpowers/specs/2026-10-06-knowledge-graph-reasoning-design.md`（1.2 表格补一行 `graph_project_merge_job`，说明为合并队列）
- Modify: `apps/tap-ai-frontend/tests/e2e/knowledge-graph.spec.ts:69`（在片段快照轮询之后追加 API 级轮询 `GET ${root}/knowledge/graph/project` 直到 `status === "READY"` 且 `nodeCount > 0`，超时 30 秒；UI 断言不改）

- [ ] **Step 1: 更新四份文档与 E2E 断言；对客户企业名称做一次大小写不敏感全文检索，必须无输出**

- [ ] **Step 2: 全量检查**

Run（仓库根目录）: `make check && make test && git diff --check`
Expected: 全部通过；无 MySQL 时集成测试按约定跳过，有 MySQL 时 Task 2、3、4 的集成测试运行并通过

- [ ] **Step 3: 隔离 E2E**

Run: `make demo-e2e`
Expected: `knowledge-graph.spec.ts` 通过——规则式假抽取对夹具句子 "A verified claim requires supporting evidence." 产出 `REQUIRES` 边，片段 READY 后 graph worker 的 `pending_work` 合并出项目图 version 1，`GET /project` 返回 `READY`；旧 `GET /snapshots` 与 Library 的"Published source graph"视图行为不变；其余 journey 不劣于当前 main

- [ ] **Step 4: 提交并整理 PR 描述**

```bash
git add docs apps/tap-ai-frontend/tests/e2e/knowledge-graph.spec.ts
git commit -m "docs: record project graph merge and query in architecture and roadmap"
```

PR 描述列出：意图、受影响文档、`make check/test/demo-e2e` 结果、升级后需执行 `make graph-rebuild ARGS="graph rebuild --project tapper-demo"` 让旧档案摘要的片段重新抽取并触发首次合并、`GraphSearchRequest.snapshotId` 改为可选与 `POST /query`/`POST /path`/`GET /nodes/{id}` 的双分支在 PR 4 收敛为项目图语义。
