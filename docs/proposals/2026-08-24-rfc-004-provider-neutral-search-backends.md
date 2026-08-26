---
id: RFC-004
status: draft
date: 2026-08-24
related-adrs:
  - ADR-002
  - ADR-004
  - ADR-005
  - ADR-011
  - ADR-012
---

# RFC-004：以 Milvus 为实验默认的可替换检索后端

## 摘要

本 RFC 将搜索引擎明确为 TAP Knowledge 的可替换基础设施适配器，而不是领域架构的一部分。`KnowledgeAPI`、`SearchPort`、Policy、QueryPlan、Citation、Retrieval Trace 与 provenance 保持 provider-neutral；本地实验默认使用 Milvus Standalone，Azure AI Search 适配器继续保留，共享非生产部署留待本地实验结论后单独批准。两种后端必须满足同一组核心契约；真实 Milvus 数据库门禁在本地/日常 CI 必跑，真实 Azure 门禁对普通提交按需运行、对任何 Azure-backed 发布仍强制。

首个实现采用可复用纵向切片：本地 Docker 部署 Milvus Standalone，只装载 `doc` family 的少量脱敏 fixture，通过 LiteLLM 生成并缓存真实 embedding，验证 BM25、dense vector、hybrid/RRF、查询期 ACL、严格 provenance、撤权、删除、重建与 collection alias 切换。首轮不提前实现完整 Task 4 ingestion，也不承诺生产部署形态或 SLO。

## 背景

现有 [ADR-005](../decisions/2026-08-20-adr-005-four-azure-ai-search-indexes.md) 和 [ADR-012](../decisions/2026-08-21-adr-012-tap-managed-chunking-and-provenance.md) 把 Azure AI Search 写入了已接受的检索决策；[ADR-002](../decisions/2026-08-20-adr-002-azure-enterprise-deployment-baseline.md) 则把它列为企业部署基线，同时明确允许本地 Lab 使用轻量替代品。[RFC-003](2026-08-23-rfc-003-phase-1-application-structure.md) 也把真实 Azure 资源列为 Phase 1 强制依赖。Task 3 的代码切片已落地且本地复验通过，但真实 Azure ACL 与真实 Entra/Project-Policy 外部门禁尚未运行，Task 3 生命周期仍为 `in progress`。应用边界已通过 `SearchPort.search(SearchExecution) -> tuple[SearchHit, ...]` 与 provider 隔离；Task 4 的 ingestion、Index Writer 和四个真实索引尚未实现。现在是验证并确立可替换后端的低返工窗口。

TAP 的 Git、Blob 与 MySQL 才是内容或运行事实的权威源。搜索后端只保存带 ACL 与 provenance 的可重建投影。Azure 官方的自定义文档安全过滤同样要求应用把可信 principal 写入 filterable 字段，并明确指出过滤字段本身不负责认证或授权；Milvus 提供查询期 metadata filtering、ARRAY 操作、BM25、dense+sparse hybrid search 与 collection alias。两种产品的查询 DSL、score、一致性、认证和运维不同，但这些差异应由 adapter 与 conformance tests 吸收，而不是进入公共 Knowledge 契约。

成本是本次实验的约束，不是领域不变量。截至 2026-08-24，Azure AI Search Serverless Developer 仍为无 SLA 的预览能力，初始阶段暂未计费且不支持 index alias；Dedicated Free 只有三个 index。自托管 Milvus 无搜索服务许可费，但需要本地计算、磁盘、升级和恢复工作。因此设计不把任一时点价格写成永久事实，而是保留双后端，用实测资源与运维数据决定后续环境选择。

相关官方资料：

- [Milvus 部署模式](https://milvus.io/docs/install-overview.md)
- [Milvus Standalone 要求](https://milvus.io/docs/prerequisite-docker.md)
- [Milvus filtered search](https://milvus.io/docs/filtered-search.md)
- [Milvus ARRAY operators](https://milvus.io/docs/array-operators.md)
- [Milvus full-text search](https://milvus.io/docs/full-text-search.md)
- [Milvus hybrid search](https://milvus.io/docs/multi-vector-search.md)
- [Milvus collection alias](https://milvus.io/docs/manage-aliases.md)
- [Milvus authentication](https://milvus.io/docs/authenticate.md)
- [Milvus RBAC privileges](https://milvus.io/docs/grant_privileges.md)
- [Azure AI Search security-filter pattern](https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search)
- [Azure AI Search 层级](https://learn.microsoft.com/en-us/azure/search/search-sku-tier)
- [Azure AI Search 容量限制](https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity)

## 目标

- 把搜索 provider 差异限制在装配、adapter、配置与 provider conformance tests 内。
- 让 Milvus Standalone 成为本地实验的默认检索后端，同时保留 Azure AI Search 能力，并以本地实测结果决定是否进入共享非生产。
- 保持当前生成的公共 HTTP schema、Citation、Policy、QueryPlan、SearchExecution、SearchHit 与 provenance 语义不变；只把已经存在的搜索异常提升为 provider-neutral 端口错误。
- 用一个真实 `doc` 纵向切片验证 Milvus 的 BM25、dense、hybrid、ACL、删除、重建和 alias 语义。
- 让所有检索路径在 provider 执行前使用同一份服务端可信 ACL，而不是先召回再过滤。
- 使用少量脱敏数据、付费 embedding 的有界调用和内容寻址缓存控制实验成本。
- 形成可复现的正确性、质量、延迟、资源和成本基线，支持继续、修正或停止 Milvus 路径的决定。

## 非目标

- 不用 Milvus 替代 Microsoft Entra ID、Project Policy、Blob、MySQL、Redis 或 LiteLLM。
- 不让浏览器、模型或最终用户直接连接 Milvus 或 Azure AI Search。
- 首轮不实现 `code`、`bdd`、`failure` 的真实 ingestion，不实现完整 Task 4 worker 和发布账本。
- 不部署 Milvus Distributed 或 Kubernetes，不承诺生产 HA、SLA、RPO/RTO 或容量。
- 不要求两种 provider 的高级特性完全一致；厂商特有 semantic ranker、agentic retrieval 或索引管理能力不进入公共 `SearchPort`。
- 不实现 Milvus 与 Azure 之间的请求级自动 failover。
- 不把小样本质量或延迟结果直接升级为生产门槛。

## 方案

### 稳定边界与依赖方向

Knowledge application 只依赖现有 provider-neutral 端口：

```text
KnowledgeAPI / RetrievalApplication
                 |
              SearchPort
             /          \
MilvusSearchAdapter    AzureAISearchAdapter
```

保持不变的类型和行为包括：

- `KnowledgeAPI.search/answer` 及公开 HTTP DTO。
- `RetrievalPolicyContext`、`QueryPlan` 与 `ContextSnapshot` 的不可变绑定。
- `SearchExecution`、`SearchHit`、`Evidence`、`Citation` 和 `SourceRevisionRef`。
- 检索前、provider 返回后和生成前的当前 Policy 再检查。
- 不同 source family 之间基于 `local_rank` 的应用层 RRF；不得比较不同 collection/index 的原始 provider score。

新增 `MilvusSearchAdapter` 实现现有 `SearchPort`。`search(SearchExecution) -> tuple[SearchHit, ...]` 方法签名不变；现存 Azure adapter 内的 `SearchUnavailable` 与 `SearchBoundsExceeded` 移到 Knowledge port 层的共享错误模块，由 Azure、Milvus 和应用/API 边界共同引用，禁止调用方 import 具体 adapter 才能识别失败。只有 bootstrap/entrypoint 装配层读取 `TAP_SEARCH_BACKEND=milvus|azure` 并选择实现；应用、Chat、HTTP DTO 和模型调用不得按 provider 分支。未知值或缺少所选 provider 的完整配置时启动失败。仓库本地示例显式选择 `milvus`，其他部署环境必须显式声明，不能依靠隐含生产默认值。

运行期间不做 provider 自动切换。Milvus 超时、不可用、collection/alias 缺失或结果不合约时抛出共享 `SearchUnavailable`；API error boundary 将其映射为不含 provider 内情的 RFC 9457 `503` problem details，type URL 后缀固定为 `/search-unavailable`。`SearchBoundsExceeded` 表示服务端生成的执行超过共同安全边界；公共 DTO 或 Policy 自身可归因的输入仍应分别在进入 adapter 前返回既有 `422` 或授权失败，落到 adapter 的该错误统一映射为 type URL 后缀 `/search-execution-rejected` 的 `503`。两类错误都不得转换成“证据不足” abstention、不得缓存成成功结果，也不得产生 citations。这样 provider 故障不会被错误表达为一次正常的零召回；同时不得为追求可用性静默切换到 Azure，导致 ACL、corpus、成本或排序语义变化。

Milvus adapter 的首轮资源边界固定如下；调用方不能通过请求提高这些值：

| 边界 | 默认值 | 绝对上限与来源 |
| --- | --- | --- |
| source family fan-out | `1`（只启用 `doc`） | `4`，来自闭合 `SourceFamily` |
| 每 target 候选/返回行 | `50` | `50`；`QueryPlan.candidate_limit` 仍保持 `1..100`，取两者最小值 |
| provider deadline | `8s` | `30s` |
| provider connections | `4` | `16` |
| Policy group IDs | 最多 `128` 个 | 每个最多 `256` 字符；超限在 authorization/adapter 边界拒绝，禁止截断 |
| resource scope | 最多 `20` 个 resource | 沿用 `QueryPlan` 上限；每个 subtree locator tuple 最多 `32` 项、每项最多 `256` 字符 |
| 编译后 filter | 不超过 `32KiB` UTF-8 | 超限在任何 provider I/O 前拒绝 |

连接、deadline、候选和 filter 大小只能由服务端配置进一步收紧。Milvus 与 Azure conformance tests 使用相同的 Policy/Plan 输入上限；若现有 provider-neutral 类型尚未实现表中更严格的 group 数量/长度限制，首轮必须先在共同授权边界补齐，而不是只在 Milvus 路径特殊截断。

首轮实验使用的 `ProjectPolicy.allowed_source_families` 必须精确为 `frozenset({"doc"})`。当前空 `SearchRequest.source_families` 会展开为 Policy 允许的全部 family，因此不能只配置 `doc` target 却继续给实验主体授权 `code`、`bdd` 或 `failure`。contract test 必须证明默认空 source 请求只生成 `doc` QueryPlan 并成功检索；显式请求任一未配置 family 必须在 provider I/O 前 fail closed，adapter 不允许通过静默丢弃 family 来“修复”上游 Policy。

### 逻辑 family 与物理 target

`doc`、`code`、`bdd`、`failure` 四个 `SourceFamily` 继续是稳定逻辑边界。provider 配置把每个已启用 family 映射到一个可信 target：

| 概念 | Milvus | Azure AI Search |
| --- | --- | --- |
| 查询身份 | collection alias | index name/alias |
| 物理身份 | physical collection | physical index |
| 版本约束 | schema/corpus/model metadata | schema/corpus/model metadata |
| 关键词检索 | BM25 sparse field | searchable text/BM25 |
| 向量检索 | dense vector field | vector field |
| ACL | scalar/ARRAY filter | OData filter |

首轮只配置并只由实验 Policy 允许 `doc`；请求选择未配置的 family 时 fail closed，不能忽略该 family 或放宽到其他 collection。后续扩展另外三个 family 时复用相同端口与门禁，但允许不同 schema、analyzer、向量索引和排序 profile。

Milvus 配置只保存可信 logical alias、family、允许的物理名称模式和预期 schema/corpus/model 元数据。每次执行先用 reader 的 `DescribeAlias` 把 alias 解析成具体 physical collection，验证名称与元数据后，把该物理 collection 绑定进本次内部 target context；随后 BM25、dense 和补充 Query 都直接访问这个已解析物理名称，而不是继续访问可能并发切换的 alias。同一请求因此可以在发布竞争中完成旧版本或新版本之一，但不能混合两者。返回行的 `physical_collection`、`index_family` 和版本字段必须与该可信 context 一致；内部 `IndexRevision.physical_index` 只从可信 alias resolution 赋值，不能从 provider row 覆盖。

内部字段 `IndexRevision.physical_index` 暂不重命名：Milvus 下保存实际 physical collection，Azure 下保存实际 physical index。当前 Pydantic HTTP DTO、生成 OpenAPI 和严格 contract test 都明确不向普通浏览器暴露该字段，但 [reference contracts](../reference/2026-08-20-contracts.md) 的旧 `RetrievalResponse.hits.physicalIndex` 字段及“每个命中返回实际 physical index”说明仍与实现漂移。该 reference 自述为架构级契约而非最终 API，不过 physical target 是否公开属于安全边界，不能让两种表述长期并存。本 RFC 接受前必须把该字段与说明移到只对授权运维可见的 Retrieval Trace 章节或删除，并以 Pydantic DTO 生成的 contract 作为公开在线 schema 的实现来源；在漂移消除前不得宣称公共契约同步完成。未来若出现第三种 provider 且命名造成真实歧义，再通过独立契约变更处理。

### 首轮 `doc` collection schema

首轮 schema 使用显式字段，关闭把未知动态字段当作权威元数据的路径：

| 字段组 | 主要字段 | 用途 |
| --- | --- | --- |
| 稳定身份 | `chunk_id`、`logical_chunk_id`、`root_id`、`parent_id` | 幂等、结构范围与引用 |
| 内容 | `title`、`content`、`content_role` | BM25、回答上下文与展示 |
| 权限 | `tenant_id`、`project_id`、`allowed_group_ids`、`classification_rank`、`environment`、`deleted` | 查询期强制 ACL |
| 版本 | `index_family`、`physical_collection`、`corpus_version`、`schema_version`、`embedding_model_version` | 防止误报逻辑/物理来源或混合不兼容投影 |
| 来源 | `source_id`、`source_type`、`source_revision`、`source_content_hash`、`chunk_content_hash`、`anchor_json` | 不可变 provenance |
| 派生关系 | `derived_from_chunk_ids` | 摘要与派生内容回链 |
| 检索 | BM25 sparse field、`dense_vector` | lexical、vector 与 hybrid |

`chunk_id` 使用现有 `h_` 加 64 位小写 SHA-256 hex。每个实体重复写入发布时已知的 `index_family=doc` 与 `physical_collection`，adapter 将两者与可信 target/alias resolution 交叉验证，不能只凭结果行声明逻辑或物理来源。`allowed_group_ids` 使用有界同质 ARRAY；tenant、project、classification、environment、corpus 和 `deleted` 建立适合过滤的 scalar index。`classification_rank` 只能由闭合的 `Classification` enum 映射，不能接收浏览器数字。`source_type`、anchor 与 revision kind 必须保持 `doc` family 兼容。向量维度和 embedding model ID 在 collection 创建时固定，不允许同 collection 混用。

首轮小数据采用 `FLAT + COSINE` 建立向量正确性基线，减少 ANN recall 对安全/映射实验的干扰。BM25 使用 Milvus 内置 sparse/inverted 能力；中文或多语言 analyzer 由 fixture 语言决定并写入 schema version。HNSW、量化或其他 ANN 只在正确性门禁通过后的独立性能实验中比较。

### Schema digest 与 pinned transport 归一化

Schema digest 的语义不因 SDK transport 形状改变。每个 index 的 canonical 表示仍然严格只有 `index_name`、`field_name`、`index_type`、`metric_type` 与嵌套 `params`；BM25 的 canonical `params` 固定为 `inverted_index_algo="DAAT_MAXSCORE"`、`bm25_k1=1.2` 与 `bm25_b=0.75`。collection description 中的声明 digest、从 fields/functions/indexes/consistency 计算的 digest 和 target 期望 digest 必须继续相同，不能为 publisher 或 reader 引入第二套摘要表示。

2026-08-26 的本地 live probe 观察到，固定组合 Milvus `2.6.22` + PyMilvus `2.6.17` 会把该 BM25 index 的三个设置作为 `describe_index` 结果的顶层字段返回：`bm25_b` 与 `bm25_k1` 是 numeric strings，`inverted_index_algo` 是字符串，且结果没有嵌套 `params`。这是仓库对固定组合的实测，不是 Milvus 官方文档声明，也不能推广为其他版本的兼容承诺。

因此 transport 只增加一个版本锁定、闭合且无歧义的归一化分支：公共 transport key 集合仍限于实际基础字段 `field_name`、`index_name`、`index_type`、可选 `metric_type`、可选 `params`、`total_rows`、`indexed_rows`、`pending_index_rows` 与 `state`；只有已验证为 `SPARSE_INVERTED_INDEX` + `BM25` 的 index 才可额外接受顶层 `bm25_b`、`bm25_k1`、`inverted_index_algo`。`bm25_b` 与 `bm25_k1` 的 numeric string 只允许严格、有限的 JSON number 词法形式，并归一化为 canonical nested `params` 中的数值；`inverted_index_algo` 不做类型转换。任何未知字段、重复 index/设置、嵌套与扁平设置并存（即使值相同）、非有限或非规范 numeric string 都 fail closed。其他 index 继续使用原闭合形状，不能复用该分支做通用字符串到数值 coercion。归一化后的对象必须与上述唯一 canonical 表示逐字段相等后才能参与 digest。

### 可信 ACL filter

Milvus filter 只能由 adapter 从 `SearchExecution.policy` 与已绑定的 `QueryPlan` 编译。公共请求、模型输出、Chat 状态和任意原始 filter 字符串都不能进入编译器。语义固定为：

```text
tenant_id == policy.tenant_id
AND project_id == policy.project_id
AND ARRAY_CONTAINS_ANY(allowed_group_ids, policy.actor.allowed_group_ids)
AND classification_rank IN rank(policy.allowed_classifications)
AND environment IN [plan.effective_environment, "global"]
AND corpus_version == plan.corpus_version
AND deleted == false
AND optional_resource_scope
```

`rank(policy.allowed_classifications)` 由闭合 `Classification` enum 的固定映射生成成员列表，不假设 `RetrievalPolicyContext` 存在未定义的 ceiling 字段，也不把集合偷换成未经证明连续的范围。`effective_environment` 为空时只允许 `global`。空用户组、空 classification 集合、未验证身份、Policy 不可用、超出有界 group/scope 数量或任何字符串无法安全编码时拒绝查询。所有 identifier 与 literal 通过专用 Milvus expression builder 编码；禁止字符串插值未经转义的浏览器值。

`optional_resource_scope` 由已解析、已授权的 source ID、immutable revision/hash 及 root/parent/logical chunk subtree 组成。多个 scope 资源是受控 union，每个资源内部保持 AND 约束。BM25、dense、hybrid、exact、parent/child expansion、依赖补充、facet/count 和任何后续读取都必须应用等价 ACL；结果后的 scope/provenance 检查只是纵深防御，不能替代查询期 filter。

### 查询、融合与结果映射

一次 Milvus 检索按以下顺序执行：

1. 复用现有规则验证 Policy、Plan、ContextSnapshot、query hash、corpus、model 和 vector dimension 的绑定，并确认实验 Policy 只允许 `doc`。
2. 在任何搜索 I/O 前编译有界 ACL/resource filter；随后用只读凭据解析并验证 alias，把可信 physical collection 绑定进本次 target context。
3. 对绑定的 physical collection 以同一 filter 创建 BM25 与 dense request，使用有界候选数、deadline 和 connection concurrency。
4. 使用固定版本的 RRF 配置做单 collection hybrid；首轮不引入 provider 特有 reranker。
5. 只读取公开内容与 provenance 所需字段，不返回 ACL fields 或 vectors。
6. 把命中严格映射为 `SearchHit`，重新验证 `index_family`、chunk ID/hash、source revision/anchor、schema、corpus、model 与 physical collection。
7. 由现有 application 再检查当前 Policy、required/scope/preferred 语义和跨 family RRF。

provider 原始 score 可以进入受控诊断记录，但公共正确性不依赖它；单 collection 结果通过 `local_rank` 进入现有融合。Milvus expression、group IDs、凭据和 vectors 不写普通日志或用户 Trace；审计只保存 provider、target、query plan、ACL digest、版本、provider 返回行数、严格映射丢弃数、耗时和必要的脱敏 request ID。Milvus 内部 ANN/BM25 候选不会暴露给 adapter，因此本 RFC 不把不可观测的引擎内部候选数伪装成安全证据；“provider rows”专指 provider 已返回给 adapter 的行。

### 本地 Docker 与身份

在现有 Compose 中新增 `milvus` profile，包含 Milvus Standalone、etcd、MinIO 与命名持久卷。没有检索实验时无需启动该 profile；本地检索实验通过稳定命令启动和检查。部署遵循：

- 所有宿主端口只绑定 `127.0.0.1`。
- 启用 Milvus authentication，初始化后不让应用使用默认 root 凭据。
- `.env.example` 只含明显的本地占位值，真实共享环境凭据由 secret 注入。
- 本地 loopback 实验可以不启用 TLS；任何共享非生产或远程连接必须启用 TLS，并保持网络入口只对 TAP 后端开放。
- Milvus Server、etcd、MinIO 与 PyMilvus 使用显式兼容版本；不得使用浮动 `latest`。
- Docker Desktop/host 必须满足 Standalone 最低内存要求；实验报告记录实际 CPU、RAM 和磁盘，而不是把开发机结果当生产容量。

本地/CI bootstrap 以默认 root 完成一次性初始化后立即轮换默认密码，并把 root 凭据排除在应用、loader 与普通健康检查环境之外。固定三类运行身份：

| 身份 | 必需权限 | 明确禁止 |
| --- | --- | --- |
| retrieval reader | 已发布 `doc` target 上的 `DescribeAlias`、`DescribeCollection`、`Search`、`Query` | Insert/Upsert/Delete/Flush、collection/index/alias 管理、RBAC |
| fixture writer | 指定未发布 physical fixture collection 上的 `Insert`、`Upsert`、`Delete`、`Flush` 及 flush 状态读取 | Search/Query、alias 切换、collection/index 管理、RBAC |
| provisioner/publisher | 仅本地/CI 编排所需的 collection/index create/drop/load/release、alias create/alter/drop 与逐 target reader/writer 授权 | 被 Retrieval application 持有、出现在普通 runtime env |

发布者在 alias 切换前给 reader 授予新 physical collection 的最小只读权限，回滚窗口结束后再撤销旧 target 权限。Milvus 候选版本对 `alter_alias` 的具体 RBAC 映射必须由版本固定前的行为探针验证，不能根据 SDK 方法名猜权限；实施只授予官方 privilege 表与行为探针共同证明必要的权限。

行为健康门禁由独立的本地/CI probe orchestrator 运行：provisioner 创建隔离临时 collection 并授权，writer insert/flush，reader 使用与应用同级的只读凭据完成 alias describe、filtered hybrid search/query，最后由 writer 删除实体、provisioner 删除 alias/collection。容器 `running` 或端口可连不是充分健康证明，probe 不得修改 active fixture collection。Retrieval application 的 liveness 只检查自身进程/event loop，不访问 Milvus；startup/readiness 才允许用 reader 凭据执行有 deadline 的 `DescribeAlias`、`DescribeCollection` 与预置 canary query。Milvus 故障使实例 not ready 并让已进入的请求获得受控 `503`，不能触发 liveness 重启；应用身份始终没有 create、insert 或 delete 能力。

### Embedding、缓存与成本上限

fixture ingestion 与 query embedding 都通过现有 LiteLLM `ModelPort`，Milvus adapter 不直接连接模型 provider。首轮允许付费 embedding API，但设置以下边界：

- 默认 research profile 最多 `100` 个脱敏 chunks 和 `20` 条有期望 source 的查询；显式扩展 profile 的绝对上限为 `500` chunks、`100` queries，不能由请求或环境变量继续放大。
- 每段文本仍受现有 `8,000` 字符上限约束；运行前先计算所有未缓存输入，任何计数超限都必须在第一次 provider 调用前失败。
- cache key 固定为 `embedding_model_id + dimension + sanitized_content_hash`。
- 缓存位于 git ignored 的本地实验目录，只保存脱敏输入的向量与必要版本元数据。
- 模型 ID、维度或内容 hash 变化自动 miss；禁止复用不同 embedding space 的向量。
- loader 使用有界 batch；超过 chunk/query 上限必须显式实验开关，默认失败。
- 报告模型 ID、维度、调用次数、输入规模、cache hit/miss 和 provider request ID，不保存 API key 或原始敏感输入。

embedding cache 只是成本优化，不是内容、权限或 ingestion checkpoint 的权威源。删除 cache 后必须可以重新生成；ACL 变化不能因复用向量而跳过 metadata 更新与可见性验证。

日常 CI 不调用付费 embedding API。仓库保存一组最小、脱敏、版本化的预计算 fixture/query vectors，并绑定 model ID、dimension 与内容 hash；这些向量是 conformance test input，不是运行时 cache。真实 Milvus CI 门禁使用它们验证数据库、hybrid 与 ACL。完成本次实验报告前必须另外运行一次真实 LiteLLM embedding research profile，重新生成本地 ignored cache，并验证维度、版本、正向检索和成本记录；该外部模型探针不因日常 CI 重复计费。

### 删除、重建与 alias 发布

Milvus 是可重建索引。首轮使用类似 `kb_doc_<schema>_<corpus>` 的物理 collection 和 `kb_doc_active` alias；具体名称经过长度和字符白名单验证。发布流程为：

1. 从脱敏 fixture manifest 创建新物理 collection。
2. 批量写入并确认持久可见，记录 chunk/source/hash/ACL 计数。
3. 运行 schema、provenance、质量正向样本与 ACL negative probes。
4. 对账 manifest，确认无重复、遗漏或额外实体。
5. 切换 alias，并主动确认 alias 指向目标 collection。
6. 原子发布应用侧 `activeCorpusVersion`；在此之前不得放宽 corpus filter。
7. 保留旧 collection 作为实验回滚窗口，确认后再显式清理。

ACL 收紧和删除必须先 upsert 新 metadata 或写入 `deleted=true`，以 strong consistency 查询确认旧主体零命中，再提交首轮 fixture publish/result marker；后续物理 delete 不能成为安全生效的唯一条件。完整 Task 4 实现后，相同条件才允许推进 ingestion checkpoint。容器重启后数据必须仍可检索。首轮不把 Milvus Backup 当准入门禁，因为索引可从 fixture/manifest 重建；实验必须证明删除本地数据卷后能够重建相同 IDs、hashes、ACL 和 provenance。

未来扩展四个 family 时，各 alias 可以逐个切换，但应用 `activeCorpusVersion` 只能在全部 family 确认收敛后发布。传播期间继续使用旧 corpus filter，允许可见 degraded/零结果，禁止混入新旧 corpus。

### 测试与门禁

测试和实验完成门禁分为四项：

| 层级 | 必须覆盖 |
| --- | --- |
| Unit/contract | filter escaping 与 bounds、空 ACL fail closed、默认空 source 只形成 `doc` Plan、同 filter 多通道、严格 row mapping、共享错误与 HTTP 503 映射、deadline/cancel、未配置 family、秘密不进 repr/log |
| Provider conformance | 每条出站请求含精确 ACL filter、allowed positive、denied provider rows/hits zero、tenant/project/classification/environment/corpus 隔离、scope/subtree、撤权窗口、delete、provenance、bounded result、alias/physical identity |
| Real Milvus CI | Docker Milvus 建表/写入/hybrid、版本化预计算 vectors、ACL 收紧、重启持久性、重建对账、alias/corpus 发布 |
| Embedding research | 真实 LiteLLM embedding、ignored cache、模型/维度绑定、正向检索与调用/成本记录；完成实验报告前必跑，日常 CI 不重复调用 |

Milvus real database integration 是本地研究和日常 CI 的必跑门禁。Azure adapter 继续运行相同的 unit/contract/conformance tests；真实 Azure gate 对本地 Milvus 实验与每次提交改为 opt-in，仅在提供 `non-production-sanitized` 凭据时运行，skip 不阻塞日常 Milvus 开发。但任何 Azure-backed 环境的发布、ADR-002 企业基线验证或当前 Azure-specific Task 3 的正式关闭仍必须运行真实 Azure gate；本 RFC 不用一次本地 Milvus GREEN 代替 Azure 部署验收。真实 LiteLLM embedding research profile 是完成首轮实验报告的必跑项，但不是每次 CI 的前置条件。禁止使用 fake/skip 代替 Milvus 的真实 ACL GREEN，也禁止用预计算 vectors 冒充真实 embedding 已运行。

Entra/Project-Policy 门禁保持独立。本实验可使用现有 verified subject 与 current-policy fake 验证 adapter，但不能据此宣称真实身份/撤权集成完成，也不能关闭 Task 3 的 Entra 外部门禁。

安全硬门禁包括：

- 正向主体在 `top 10` 内看到每条 probe 的预期 source。
- 受控 transport/unit test 证明 BM25、dense、hybrid、scope 和补充读取的每一条出站请求都包含由同一 Policy/Plan 编译的精确 ACL filter；不存在无 filter 的旁路。
- denied group、错误 tenant/project、超 classification、错误 environment/corpus 在 provider 返回给 adapter 的 rows、映射 hits 和当前 Search/Answer citations 三层均为零。Milvus 不暴露引擎内部 ANN/BM25 candidates，本门禁不对不可观测内部状态作虚假断言。
- ACL 收紧或删除生效后，旧主体不能再通过当前已实现的 Search/Answer surface 获得内容或新 citation。
- 公共响应、普通日志和用户 Trace 不包含 group IDs、凭据、raw filter 或 vectors。
- 重建前后 manifest、chunk IDs、hashes、ACL 与 provenance 完全一致。
- alias/corpus 切换不返回混合版本。
- 现有 Task 1–3 测试、contract generation、lint、format 与 typecheck 无回归。

旧 citation resolver、history replay 与 Retrieval Trace 的读取时重新授权属于尚未实现的 Task 7。本 RFC 保留该要求并把它列为 Task 7 的后续安全硬门禁，但首轮 Milvus 实验不能用不存在的 endpoint 宣称它已经通过或关闭。

### 实验指标与决策输出

首轮记录但不设生产阈值：

- BM25、dense、hybrid 的 Recall@K、MRR、zero-result 和预期 source rank。
- 查询与写入的 p50/p95、timeout/error、container restart 恢复时间。
- Milvus、etcd、MinIO 的 CPU、RAM、磁盘与启动时间。
- collection rebuild、manifest reconcile 与 alias 切换耗时。
- embedding 调用次数、cache 命中率、输入规模和估算成本。
- 本地部署、升级、凭据、日志和故障诊断的实际操作成本。

小样本质量和延迟只形成基线。只有 ACL 零泄漏、严格 provenance、删除/撤权和确定性重建是首轮不可降低的通过条件。实验报告给出三种结论之一：继续采用 Milvus 本地实验默认路径；先修正已列问题再复验；停止 Milvus 路径并保留 Azure。

### 文档与决策生命周期

本 RFC 为 `draft` 时不改写已接受 ADR 的语义，也不把 Milvus 写成当前已实施能力。RFC 接受后应创建两份独立 superseding ADR：

1. 替代 ADR-005 的新 ADR 必须完整重述仍保留的决策：`doc`、`code`、`bdd`、`failure` 四个逻辑 family；BM25、vector、AST/symbol、轻量 code-test dependency 等适用多路召回；单 target/跨 family 的 RRF 与可选 reranker 边界；文档 Parent/Child 与 Document Summary/Section Summary/Leaf Chunk；代码不转 Markdown；查询前 ACL filter。它只把四个 Azure 物理索引改为 provider-neutral targets，并新增 Milvus 本地实验默认、Azure 可选、共享非生产另行批准。
2. 替代 ADR-012 的新 ADR 必须完整重述仍保留的决策：TAP 拥有 typed parsing/chunking、稳定 `logicalChunkId`、不可变 `chunkId`、ACL/provenance、embedding、删除传播和 push writer；每个 child 重复 parent/ACL/lineage，不做 query-time join；Writer 写 physical target，Reader 经 alias 查已发布 target；schema/chunker/model 不兼容升级使用新 physical target、同一 Golden Dataset、评估和 alias 切换；provider 的 index projection 不得直接写 active corpus。它只把 Azure 专名职责改成 Search Provider adapter。

本地 Docker 实验属于 ADR-002 已允许的本地 Lab 替代范围，因此本 RFC 不替代 ADR-002，也不改变 Azure 企业部署基线。若后续要让 Milvus 成为共享非生产或企业环境默认，必须基于实验 review 新建独立 RFC，评估 TLS、网络、secret、HA/SLO/RPO/RTO 与运维责任，并按治理流程替代或细化 ADR-002；不得把本 RFC 的本地批准外推到企业环境。

创建 superseding ADR 时必须同时更新旧 ADR 的 `status: superseded` 与 `superseded-by`、新 ADR 的 `supersedes`、本 RFC 的 `related-adrs` 与 decisions index，不能只写新文件或只引用旧 ADR 而漏掉上述保留语义。实现计划随后同步 README、[RAG Foundation](../architecture/rag/2026-08-21-foundation.md)、[Azure-specific index design](../architecture/rag/2026-08-21-ai-search-index.md)、[reference contracts](../reference/2026-08-20-contracts.md)、[RFC-003 基础设施表](2026-08-23-rfc-003-phase-1-application-structure.md#phase-1-运行时与基础设施依赖)、[Phase 1 roadmap](../plans/2026-08-20-roadmap.md) 与 [Task 3/4 gate](../plans/2026-08-23-phase-1-application-implementation.md#task-3-trusted-policy-context-and-knowledge-public-api)。真实 Milvus ACL gate 取代真实 Azure gate 作为本地/日常 CI 强制项；Azure gate 对普通提交为 opt-in，但 Azure 环境发布与 ADR-002 企业基线验证仍强制；Entra gate 不受影响。在完成 superseding ADR、修复 reference contract 漂移并更新和评审实施计划前，本实验不把 Task 3 标记 complete，也不把 Milvus 描述为已交付生产能力。

## 替代方案

- **一次性外部脚本**：最快，但不能证明 TAP 的 SearchPort、Policy、Citation 和 strict provenance 真正兼容，实验成果难以复用，因此不选。
- **立即实现完整四 family 双后端**：覆盖最全，但在 `doc` 真实门禁和成本数据出现前提前承担 Task 4、四种 schema 和发布系统复杂度，因此不选。
- **继续 Azure-only**：托管运维简单，但把领域路线绑定到一个 provider，且本地无法日常运行真实 ACL gate，不符合成本与可替换性目标。
- **Milvus Lite 作为共享后端**：适合单进程本地算法实验，但不符合 TAP API、ingestion 和 worker 共享服务及最小权限账号边界，因此只可用于独立开发探针，不作为本 RFC 集成目标。
- **Milvus Distributed/Kubernetes**：提供横向扩展和冗余，但首轮数据与负载不足以证明需要，成本和运维面明显更大，因此推迟。
- **请求级 Milvus→Azure 自动 failover**：可能提高表面可用性，却会改变 ACL、corpus、排序和成本语义，也难以证明撤权窗口，因此禁止。

## 风险与缓解

- **ACL DSL 语义不等价**：建立专用 expression builder、闭合字段映射、转义/边界 unit tests，并用真实 negative probes 验证每条检索路径。
- **先召回再过滤导致泄露或误召回**：filter 必须进入 BM25 与 dense request；post-result validation 只作纵深防御。
- **拥有 Milvus 凭据即可绕过行级 ACL**：只允许 TAP 后端连入，启用 authentication、最小 collection 权限、loopback/私网和共享环境 TLS；浏览器与模型永不持有凭据。
- **Milvus 与 Azure score 不可比**：单 target 只输出 local rank，跨 family 使用现有应用层 RRF，不依赖原始 score。
- **删除、ACL 收紧或 alias 切换存在可见性窗口**：使用 strong consistency negative read、`deleted=false` filter、manifest 对账和 active corpus 两阶段发布。
- **真实 embedding 产生不可控费用**：限制 fixture/query 数量，使用内容寻址 cache、有界 batch 和显式超限开关，报告实际调用；日常 CI 使用版本化脱敏预计算 vectors，不重复调用付费 API。
- **本地 Compose 消耗开发机资源**：独立 profile 按需启动，记录 host 配置与容器资源；不启动 Distributed。
- **自托管增加升级与恢复责任**：版本固定、行为健康检查、重启与删卷重建演练；Milvus 继续只保存可重建投影。
- **保留两种 provider 导致测试矩阵扩大**：公共 conformance suite 参数化复用；Milvus real 在日常 CI 必跑，Azure real 对普通提交 opt-in、对 Azure-backed 发布必跑，provider 特有 tests 只保护各自 adapter。
- **实验代码被误当生产能力**：RFC 以及实现同步后的 README/状态元数据明确本地实验范围；没有生产 SLO、真实 Entra 与后续 acceptance 前不关闭生命周期。

## 迁移或发布方式

1. 先验证所选 Milvus Server/PyMilvus 版本与仓库固定 Python 3.13、Docker host 和 LiteLLM embedding route 兼容，并把版本写入 lock/config；不使用浮动版本。
2. 先把 `SearchUnavailable`/`SearchBoundsExceeded` 提升为共享 port errors，补齐无 abstention 的受控 503 映射，并把实验 Project Policy 限定为只允许 `doc`。
3. 增加本地 Milvus Compose profile、三类最小权限身份、行为健康检查、脱敏 fixture manifest 与 embedding cache 边界。
4. 以 TDD 新增 Milvus filter compiler、alias-to-physical target binding、strict result mapping 和 `MilvusSearchAdapter`，保持 Knowledge application 的 provider-neutral 检索流程不分支。
5. 参数化 provider conformance suite，先取得 fake/controlled RED，再以预计算 vectors 取得真实 Milvus ACL GREEN；保留 Azure contract tests。
6. 完成 `doc` fixture loader、BM25+dense hybrid、撤权/delete、rebuild/alias/corpus；单独运行真实 LiteLLM embedding research profile 并完成资源/成本测量。
7. 修正 reference contract 中 `physicalIndex` 与当前生成 HTTP schema 的漂移，输出时间点实验 review；只有安全硬门禁全部通过且 review 建议继续，才接受本 RFC、创建 superseding ADR 并修改正式 Task 3/4 计划。
8. 后续按新计划增加 provider-neutral `IndexWriterPort`/`IndexAdminPort`，再逐步扩展 `code`、`bdd`、`failure`；不在本 RFC 首轮偷跑完整 Task 4。

实验提交必须可独立回退。回退范围包括 Milvus Compose/profile/volumes 声明、健康脚本、配置、SDK 依赖与 lockfile 变更、adapter、fixture loader、版本化 fixture vectors、ignored cache 约定、provider test 参数化和 Milvus CI job；不得删除 Azure adapter，也不得改变公共 HTTP/contract artifacts。命名持久卷中的实验数据由显式清理命令单独处理，普通代码回退不能隐式删除本地数据。

## 验收标准

- Knowledge application/domain 与公共契约中不存在 Milvus/Azure 分支或 provider SDK 类型。
- `SearchPort` 方法签名保持不变，Azure/Milvus 只抛共享 port errors；provider 不可用与执行边界错误分别形成受控 503，不能变成成功 abstention。
- 本地 Compose 能按需启动固定版本 Milvus Standalone，并通过 create/insert/filtered hybrid/delete 行为健康探针。
- 实验 Project Policy 精确允许 `doc`；默认空 source 请求成功且 QueryPlan 只有 `doc`，显式未配置 family 在 provider I/O 前失败。
- `doc` 纵向切片在完成实验报告前使用一次真实 LiteLLM embedding 和内容寻址 cache，可从脱敏 manifest 重建；日常 CI 使用绑定相同模型空间的版本化预计算 vectors。
- Milvus 与 Azure adapter 均通过同一核心 conformance suite；真实 Milvus 数据库 gate 在日常 CI 必跑，真实 Azure gate 对普通提交 opt-in、对 Azure-backed 发布必跑。
- 每条出站检索/补充请求都含精确可信 ACL filter；所有 ACL negative probes 在 provider rows、mapped hits 与当前 Search/Answer citations 层均为零，且撤权与删除后旧主体无法从当前 surface 读取。Task 7 再独立验证旧 citation/history/Trace 读取时重新授权。
- BM25、dense、hybrid 和 resource scope 使用同一可信 filter；公共调用者无法提交或覆盖 filter。
- 返回命中严格绑定 family、physical collection、schema、corpus、embedding model、revision、anchor 和 hashes。
- 重建前后 IDs、hashes、ACL、provenance 和 manifest 对账一致；alias/corpus 发布不混合版本。
- 现有 Task 1–3 测试、静态检查与 contract drift 门禁继续通过。
- reference contracts 的 `physicalIndex` 漂移已与当前生成 HTTP schema 同步；普通浏览器响应仍不暴露 physical target。
- 实验 review 记录质量、延迟、资源、恢复、embedding 调用与估算成本，并给出继续、修正或停止结论。
- 文档明确 Milvus 只是本地实验默认而非共享非生产/企业默认或已交付生产能力，Azure real gate 的 opt-in、ADR-002 企业基线与 Entra gate 的独立状态没有被混淆。

## 未决问题

- **Milvus/PyMilvus 精确版本（已固定，探针发现已记录）**：本地实验固定为 Milvus `2.6.22`、PyMilvus `2.6.17` 与 Python `3.13.12`，不放宽版本范围。2026-08-26 live probe 发现该组合的 BM25 `describe_index` 使用上述扁平 transport 形状；Task 5 必须先按闭合规则归一化并重跑测试，Task 8 才能重跑发布验收。该实现事实不改变本 RFC 的 `draft` 状态，也不解决 embedding route、共享部署或生产形态等其余未决项。
- **Embedding route**：从现有 LiteLLM 允许列表选择一个固定 embedding model，fixture manifest 保存 model ID 与 dimension；若没有可用的脱敏非生产 route，真实 embedding 子实验保持阻塞，不退化为未标注的 fake GREEN。
- **中文 analyzer**：由首轮脱敏 fixture 的实际语言分布选择并固化；若包含中文，必须对默认与中文 analyzer 输出做可复现对比后选择，选择结果进入 schema version。
- **共享非生产部署**：本 RFC 提议的首轮批准范围只有本地 Docker。是否把 Standalone 放到共享 VM/容器环境，在本地资源、恢复和安全 review 后另行批准，并强制 TLS 与 secret 注入。
- **生产形态**：Standalone、Distributed 或 Azure AI Search 的生产选择推迟到真实 corpus 容量、SLO/RPO/RTO 和运维成本具备后，由实施计划的 [Task 8 容量与部署门禁](../plans/2026-08-23-phase-1-application-implementation.md#task-8-deployment-observability-and-capacity-gates) 处理。
