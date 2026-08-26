# Milvus 本地检索实验评审

| 字段 | 结论 |
| --- | --- |
| 评审对象 | PLAN-MILVUS-LOCAL-SEARCH-EXPERIMENT 的本地 `doc` 纵向实验 |
| 评审日期 | 2026-08-27 |
| 代码证据基线 | `18995dc`（最终 health cleanup 修复） |
| 实验范围 | 单机 Docker、脱敏 fixture、预计算向量、provider-neutral adapter 与三角色 RBAC |
| 生命周期建议 | **continue** |
| 暂不批准 | RFC-004 接受、ADR-002/005/012 变更、共享非生产或生产部署 |

## 执行摘要

本地实验在同一代码基线上通过了常规回归、真实 Milvus 持久化门禁和从空卷重建门禁。授权过滤同时约束 BM25、dense、hybrid 与补充读取；撤权、删除、重启、alias 并发切换和 manifest 重建均由真实数据库断言，而不是 fake transport 推断。一次受控百炼研究运行生成的 1536 维向量快照已提交，后续日常门禁只消费该快照，不调用付费 API。

证据支持继续推进下一阶段治理与共享环境设计，但不代表已选定企业默认检索后端。Azure AI Search、Entra ID 和 Project Policy 的既有外部门禁保持不变。

## 决策证据

| Gate | Result | Evidence |
| --- | --- | --- |
| ACL negative matrix | pass | `persistent.log`; SHA-256 `d20293e619125b807a152d789befaeb649cb9c2d3ee745e84141ffadcd3dfd67`; wrong group/project/tenant/corpus、超分类与错误环境均为 0 row、0 hit、0 citation。 |
| 8-case hybrid top 10 | pass | `persistent.log`; SHA-256 `d20293e619125b807a152d789befaeb649cb9c2d3ee745e84141ffadcd3dfd67`; 8 个 fixture case 覆盖两个正例、五个负例与 subtree，所有返回上限均为 10，另有 wrong-corpus 负向探针。 |
| restart persistence | pass | `persistent.log`; SHA-256 `d20293e619125b807a152d789befaeb649cb9c2d3ee745e84141ffadcd3dfd67`; 普通容器重启后以强一致查询确认 committed canary，并保持 manifest digest 一致。 |
| rebuild parity | pass | `rebuild-empty.log`; SHA-256 `181ad4c3b0a5d364fec9898edaee6ddd85113f97d6f4680b5889b48f4b7c45fb`; 仅重置经校验的 `tap-milvus-local-experiment` volumes，从 fixture/snapshot 重建后精确恢复 canonical digest。 |
| alias single-version binding | pass | `persistent.log`; SHA-256 `d20293e619125b807a152d789befaeb649cb9c2d3ee745e84141ffadcd3dfd67`; 并发 alias 切换期间每次请求只观察一个 physical/corpus version，无混读。 |
| embedding budget | pass | `report.json`; SHA-256 `8a2637c8b35a0d345cad719ab5f6a5e90fe131b6de21baff9b8d39d81ad17a1f`; `research-embedding-v1` 处理 12 chunks、8 queries、18 次 cache miss、203 input tokens，1536 维，计算成本 `0.0001015 CNY`。 |

## 测量与复现

- `check.log`：`make check` 全部通过；SHA-256 `c6c55f2f076f74cc6ae3393f6a5b0f6c7fdd78a09b3b962e5c4afe2aaca0489f`。
- `test.log`：`1106 passed, 4 skipped`；SHA-256 `ecc92f60258c4697bb1a4b4c794205d9eba50a83ca84deadcdb6a0bd9c45331c`。4 个 skip 是默认不选择的真实 Milvus、Azure ACL 与 current-policy opt-in suites；两次真实 Milvus 门禁自身均为 `19 passed, 0 skipped`。
- 提交的 `vectors-research-embedding-v1.json`：SHA-256 `50a373057d529388b37389a9eb00fae1662988676d3be1840d97713c8e063ef0`；日常门禁使用该固定快照，不重新请求 provider。
- embedding 金额按报告内 token 数与单价计算，不是供应商账单。原始日志和研究报告位于 ignored `.local/`；本评审未复制凭据、endpoint、provider request ID、原始请求正文或向量。

从仓库根目录复现无付费门禁：

```sh
make check
make test
make milvus-preflight

# 仅首次创建全新 volume；完成 root 轮换后不再设置该开关
TAP_ALLOW_INITIAL_MILVUS_ROOT=1 make test-milvus

# 已完成 root 轮换的既有 volume
make test-milvus

TAP_ALLOW_INITIAL_MILVUS_ROOT=1 \
  TAP_ALLOW_MILVUS_VOLUME_RESET=1 \
  make test-milvus-rebuild-empty
```

真实 embedding 研究只允许在显式注入未跟踪 provider 配置、批准费用并设置 `TAP_RUN_PAID_EMBEDDING_RESEARCH=1` 后单独运行；它不是常规 CI 的组成部分。

## 结论边界

该唯一建议只授权进入后续治理与设计工作，不自动接受 RFC-004，不修改 ADR-002、ADR-005 或 ADR-012，不批准共享非生产或生产使用，也不关闭 Azure/Entra 外部验收。若要改变默认后端，必须按文档治理状态机单独评审 RFC、创建替代 ADR，并完成 TLS、secret、监控、备份、SLO、容量与真实企业授权门禁。
