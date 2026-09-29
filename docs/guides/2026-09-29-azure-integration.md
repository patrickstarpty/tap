# Azure 接入扩展点

创建日期：2026-09-29。V1 只保留 Milvus（`SearchPort`）与 S3/MinIO（`ArtifactStore`）两个实现；本指南记录日后接入 Azure AI Search 与 Azure Blob 所需的扩展点，不代表当前已实现或计划中的工作项。背景见[基础设施收敛设计](../superpowers/specs/2026-09-29-infra-convergence-design.md)。

## 1. 扩展点

- `SearchPort`（`apps/tap-ai-backend/src/tap/modules/knowledge/ports/search.py`）：provider-neutral 检索端口，docstring 规定实现必须在每个 channel 上应用强制的 tenant、project、group、classification、environment、corpus 与 resource-scope 前置过滤，只返回通过过滤的 `SearchHit`，并只抛出中立的 `SearchUnavailable`/`SearchBoundsExceeded`。
- `ArtifactStore`（`apps/tap-ai-backend/src/tap/modules/knowledge/ports/documents.py`）：原件与产物存储端口，docstring 要求实现把每个 locator 绑定到其 revision 与 kind、读取时校验内容摘要、对非法或外来引用抛 `ArtifactIntegrityFailure`、对未知引用与 provider 故障统一抛 `ArtifactUnavailable`、批量删除前先整体校验、并对 staging 回收做有界处理。
- `CitationArtifactStore`（`apps/tap-ai-backend/src/tap/modules/knowledge/ports/citations.py`）：`ArtifactStore` 的只读子集，专供引用解析读取已校验、绑定 revision 的 normalized 与 chunk 产物。

## 2. 验收：一致性测试

- `tests/contract/search_provider_conformance.py`：新 `SearchPort` 实现需要参数化跑通这个模块里的一致性用例；跑法见 `tests/contract/test_search_provider_conformance.py`（当前只对 Milvus 参数化）。
- `tests/contract/artifact_store_conformance.py`：新 `ArtifactStore` 实现需要通过其中的 `exercise_artifact_round_trip`；跑法见 `tests/contract/test_object_artifacts.py` 与 `tests/integration/test_minio_artifacts.py`（当前只对 S3/MinIO 参数化）。新 provider 按同样方式把参数化目标加入这两个 runner。

## 3. 接线

- `apps/tap-ai-backend/src/tap/entrypoints/tapper_runtime.py` 的 `_create_search`（构造 `MilvusSearchAdapter`/`PyMilvusReader`/`MilvusIndexTarget`）与 `_create_blob`（构造 `KnowledgeArtifactStore` + `S3ObjectStore`）是唯一允许选择 provider 实现的位置。新 provider 需要在这两个函数旁新增等价的 `_create_search`/`_create_blob` 分支，并新增对应的配置项（当前 S3 路径读取 `TAPPER_S3_ENDPOINT`、`TAPPER_S3_BUCKET`、`TAPPER_S3_REGION`、`TAPPER_S3_ACCESS_KEY`、`TAPPER_S3_SECRET_KEY`、`TAPPER_S3_STORE_ID`，六项均必填）。

## 4. 参考实现

V1 删除前的 Azure Blob 与 Azure AI Search 实现保留在本地 tag `azure-reference-2026-09-29`（指向 `main` `bc1155c`），用只读方式查看：

```sh
git show azure-reference-2026-09-29:apps/tap-ai-backend/src/tap/modules/knowledge/adapters/blob_artifacts.py
git show azure-reference-2026-09-29:apps/tap-ai-backend/src/tap/modules/knowledge/adapters/azure_ai_search.py
```

对应的旧测试同样只在该 tag 下可读：

- `apps/tap-ai-backend/tests/contract/test_blob_artifact_contract.py`
- `apps/tap-ai-backend/tests/contract/test_azure_search_strict.py`
- `apps/tap-ai-backend/tests/integration/test_azurite_artifacts.py`
- `apps/tap-ai-backend/tests/integration/test_search_acl.py`

## 5. 可借鉴设计

- **SAS copy**：`blob_artifacts.py` 的 `commit_original` 用 `start_copy_from_url` 做服务端复制，复制源 URL 由 `generate_blob_sas` 现场签发一个 5 分钟内过期、只读的 Blob SAS（`_source_copy_url`），不落盘、不外传账户密钥；复制以 `copyowner` metadata token 做幂等标记，取消或异常时按 `ETag`/`copy_id` 探测并恢复已提交的结果，避免重复复制或半提交状态。
- **ACL 过滤**：`azure_ai_search.py` 的 `_security_filter` 在每次查询时拼出服务端 OData 过滤子句，强制匹配 `tenantId`、`projectId`、`allowedGroupIds`（`search.in` 白名单）、`classification`、`environment`、`corpusVersion`，并按需追加按 `sourceId`/`sourceRevision`/`sourceContentHash`（可再限定 `rootId`/`parentId`/`logicalChunkId` 子树）的资源域过滤；过滤子句始终由服务端拼装，调用方不能传入自定义 filter。
- **四索引 schema**：`AzureSearchConfig.indexes` 是 `SourceFamily → AzureIndexTarget`（`query_index`、`physical_index`）的映射，最多允许 4 个 family 各配一个索引（`len(self.indexes) > 4` 即拒绝），查询按选中的 family 集合逐索引发起有界 fan-out（`per_index_candidates` 限每索引候选数），再按 `vector_filter_mode: preFilter` 让向量检索在过滤后的子集里执行。
