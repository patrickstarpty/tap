# Tapper V1 Source API 与 projection 验收

评审日期：2026-09-08。结论：**Task 6A 实现与验收通过**。主实现提交 `6f30aeba5559dd53edde962aacf7cbffb851a3c8`，后续修正为 `37727c4`、`fa42609`、`1051a17`、`7217997`。五份独立审查/复审最终为 Critical 0、Important 0、Minor 0。该结论只覆盖 Source 公共接口、当前产品壳 Source Picker、持久命令账本与 canonical Milvus projection，不代表 V1 质量出口、真实模型或生产就绪。

## 实现范围

`0010a_source_commands` 在不修改 `0010_knowledge_sources` 的前提下增加 Knowledge-owned command ledger，authoritative schema 为精确26表。128字符二进制/NO PAD 幂等键按 Enterprise、Project、操作与 canonical request 绑定；上传、重试、删除的原始结果、Actor、correlation、Audit 与 Outbox 保持事务边界，重放不因后续 tombstone 复活 Source。旧 Document POST 保留为 deprecated facade，但与新 Source route 共用同一命令管线并要求 `Idempotency-Key`。

`/api/v1/projects/{project_id}/knowledge/sources` 已提供上传创建、分页列表、详情、删除和定向重试；DTO、OpenAPI 与 Web generated client 同步。Source Picker 接入当前 Tapper 壳与 Library，覆盖键盘、loading/error/empty、选择、查看、上传、删除和 Project 切换清空。冻结选择只展开 active Source 下 current READY Revision；保留 Source/Document/Revision/hash 顺序和20项上限，回答和 Citation 继续使用原 Document chunk/anchor 身份。

Milvus `doc-schema-v2` 使用闭合 `enterprise_id/project_id/source_id/document_id/revision_id/chunk/anchor/digest` 投影。迁移从独立 generation 构建，先核对 authoritative SQL manifest、对象 artifacts 与 Milvus readback，再原子切换 alias；显式 v1 rollback 只把可信冻结 Source tuple 翻译为唯一 legacy Document filter，并重新映射回 canonical Source。缺字段、wrong owner/project/source/revision/hash、歧义 legacy map 或 profile 不匹配均 fail closed；启动面对已有 v1 target 报 `migration-required`，不静默 fallback。

## 审查与保留的失败

首轮独立审查发现4项 Important：v2 answer 被固定 v1 validator 拒绝、迁移未核对 SQL 与 artifact manifest、原型 capture 仍是 Document-only fixture、缺少 `0010a` predecessor upgrade 覆盖。fix1 全部修正并复审通过。随后三轮分别修正 Parser payload 的 type-only import、rollback retained collection 的静态类型收窄，以及28项完整回归暴露的旧 HTTP/授权/Origin/竞态测试夹具；每轮复审均无新发现。六条 Alembic `path_separator` 警告早于本任务，本轮如实保留，未扩大范围清理。

首次完整 Backend 为2940 passed、28 failed、26 skipped；28项均来自 Source API 已改变后的旧测试组合或竞态 pause seam，生产行为未被放宽。修正后首个完整运行是2967 passed、1 failed、26 skipped；唯一失败是实际 Parser 冷启动用例在一次瞬时 `ps` 观察中未看到已 attach 的 Docker CLI child，该用例在首次完整运行和随后单测复跑均通过。最终第二次完整运行获得2968 passed、26 skipped、0 failed。两次完整运行及单测复跑的源码和 Parser receipt 前后一致，历史失败没有被抹去。

最终真实 Milvus 配方的首次启动因临时 Compose 同时预声明未使用的 Redis/Azurite 卷，9个具名卷超过正式 CLI 的 bounded inventory 而在配置阶段 fail closed；7容器、9具名卷、1匿名卷和网络随后按精确 ID 清空。配方裁掉两个无关声明后，以7容器、7具名卷、1匿名卷和1网络重跑，SDK 与正式 CLI 的 v1→v2 cutover、错误归属/Revision拒绝及 v2→v1 rollback 全部通过；成功运行同样按回执精确清理。未执行默认或共享 Milvus 操作。

## 最终验证

| 检查                                                   | 实际结果                                                                               |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `make migration-check MIGRATION=0010a_source_commands` | exit0；非空0010前驱保持，空command ledger降级/重放通过                                 |
| `make schema-drift`                                    | exit0；revision `0010a_source_commands`、精确26表、differences=[]                      |
| `make contracts` / `make check`                        | exit0；Ruff、format、mypy、契约、架构、Web build与品牌检查通过                         |
| Web完整测试                                            | 18文件、306 passed，exit0                                                              |
| Parser安全门禁                                         | 16 passed、无skip，209.11秒；image与receipt固定                                        |
| 最终完整Backend                                        | 2968 passed、26 skipped、6条既有警告，1696.24秒，exit0                                 |
| 真实Source projection                                  | 独占MySQL/Knowledge MinIO/Milvus，SDK与正式CLI cutover/rollback，1 passed/63.24秒      |
| 清理与源码                                             | 完整回归 `sourceEqual=true`、`parserReceiptEqual=true`；最终owned资源 residual全部为空 |

26项跳过包括四个由独立 Milvus gate 管理的旧模块、17个由 Task6A owned MySQL wrapper 单独执行的命令账本用例、Entra/Azure外部门禁、隔离E2E持久阶段、Codex capability 和真实模型 smoke。命令账本17项在独占真实MySQL中通过；旧Milvus ACL14项和本次最终Source projection1项分别在其独占配方中零skip通过。跳过项没有被计作本轮通过证据。

## 可追溯证据与边界

最终完整 Backend run ID 为 `9d7a21391bd5`；日志 SHA-256 为 `4ae894f17c0b99c65e8a32b2e5e8659f78d275aa58bfdfe33a83bb85b46915e5`，原生 XML SHA-256 为 `35c3fa58e58272a032fc8c41543824f8fadb241ba2ff838183278fb79e2f7a8a`。Parser image 为 `sha256:92d0342139f2609fa77440f5f8dabed99fcefa6b19dfc9d45580695c7e361b13`，receipt SHA-256 为 `6459ecc851a4b2d999bb4aaf0c05ec26954780c3a5e7cb2d8d1611440c58286c`。真实Milvus成功配方项目为 `tap-task6a-5b3478232227`，命令回执 SHA-256 为 `b1ab63101e75e1d058079500fa5b894436a730773bbe4980f4022493d13a583b`，清理回执 SHA-256 为 `b6dd68aa7b0909350ed349d72b7c45ba48779cd91b13c8c75152d561118528e8`。

本轮没有调用真实模型、共享/default Milvus 或付费 embedding，不声称企业 Azure 四索引、生产 TLS/备份/容量、多 Project 生产认证或 V1 质量门禁已经完成。Task 7 承接唯一 ModelGateway/catalog 与完整模型上下文治理；Task 8–10 承接可信检索、持久 Conversation 和 V1 质量出口。
