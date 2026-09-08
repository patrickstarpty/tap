# Tapper V1 知识来源账本验收

评审日期：2026-09-06。结论：**Task 6 实现与验收通过**，源码提交 `56ee2b591fafd387d6dd36dc492a97efb0124d78`。两轮定向复审通过；最终 Backend 2880 passed、9 skipped，Web 294 passed。该结论仅覆盖内部 Source 账本与相关摄取、审计和查询脱敏，不代表 V1 质量出口通过。

## 实现范围

`0010_knowledge_sources` 增加 Source、legacy map、有序 Answer–Source 关联和 Search Audit 四表，当前 authoritative schema 为25表。Source 拥有一到多个 Document；现有上传同事务创建 Source 与 reservation，Project 内容去重保留。Document/Revision/Citation 通过复合约束保持归属；Answer 保存全部选中 Revision 的有序关联，包括多 Source 与未被引用的选中项。旧 ID、locator/digest 与 selected JSON 保留。

legacy Source ID 为 `src_` 加 `sha256("legacy-source-v1\0" + project_id + "\0" + document_id)` 的前32个小写hex。新上传不伪造 legacy map。迁移在 DDL 前拒绝孤儿、重复、非法 JSON 形状和跨 Project 关联；部分 DDL 状态明确拒绝，不能声称 MySQL DDL 自动事务回滚。仅未产生新业务事实时允许 migration-only 降级/重放。

真实 Revision ID 长68字符；仅 DocumentRevision envelope/对应资源字段上限扩为128，其他 aggregate 保持64。三份 Outbox 存储扩为128。accepted 使用 `revisionId:ingest`，ready 使用 `revisionId`；生命周期、闭合资源 Audit 和 Outbox 同 SQL 事务。历史维护 Audit 的 nullable resource_id 与旧摘要语义保留；新 Source/Revision 操作必须带资源身份。Knowledge 通过 composition 注入的公开 Audit Port factory 使用同一 connection。

发布校验完成后持久化逻辑 projection digest 和 chunk manifest digest；READY 要求有效 Source/Document、发布凭据和未过期 lease。锁等待及最终写入后重新读取数据库时间，过期回滚全部状态、Audit、Outbox 与新凭据；批量恢复在全部 Source 锁取得后分配租约。旧已完成 READY 不伪造历史凭据或事件；旧在途任务按现有租约重新核验发布。Source 在 activation 前删除时复用既有 artifact/staging 身份与清单校验，清理失败保留恢复事实，不创建虚假 Revision/accepted。

`tapper-pattern-egress-v1` 替换查询出口的 no-op，限制8000字符，对 PEM private key、明确 Bearer/secret assignment 和保守 email 模式脱敏；非法、未闭合或超限输入在 I/O 前拒绝。覆盖查询 embedding、BM25 与回答问题字段；完整模型上下文脱敏属于 Task7。Search Audit 持久化可信 Scope、摘要、版本、闭合原因和 provider/mapped candidate 数量；提交成功后才返回命中，不保存正文或 provider exception。它不是最终授权 evidence 或答案质量审计。

## 审查与保留的失败

首轮独立审查发现3项 Important：等待锁后的租约时间陈旧、新 Search Audit 跨模块私有导入、旧孤儿 Citation fixture 与新增约束冲突；另有1项 Minor 要求真正发生 Audit 写入后的 Outbox 失败回滚证明。fix1 修复六个文件并通过复审。五项租约行为实际 RED→GREEN；Audit FK 用例两次观察器错误后补充 GREEN，不冒称行为 RED。最终失败注入由临时数据库 CHECK 拒绝真实 Outbox INSERT，事务内先确认 Audit 和新凭据已存在，再验证整体回滚。

首次完整 Backend 为2862 passed、18 failed、9 skipped，exit1；该结果保留。两项旧测试问题分别是遗漏必需 Audit factory，以及旧任务已结束后仍用180ms压力租约执行最终清理。fix2 只改两个测试文件：保留180ms/30ms的全部取消、屏障、晚写入和结算断言，旧任务彻底结束后才恢复正常清理租约；没有放宽生产 lease 校验或删除断言。两份完整相关测试文件10 passed，定向复审通过。早期另一次67 passed/1 failed和诊断错误也保留，不把单次复测当作历史失败已通过。

其余16项首次完整回归失败来自 Parser 构建凭据过期及随后的启动/清理失败：Task6 改动了声明的构建输入 `ports/documents.py`。重新执行固定版本 `make parser-build` 并校验凭据后，`make parser-security` 的16项全部通过；没有修改 Parser 生产实现、预算、隔离或版本依赖。最终完整回归前增加构建预检，并绑定凭据前后摘要。

## 最终验证

| 检查 | 实际结果 |
| --- | --- |
| `make migration-check MIGRATION=0010_knowledge_sources` | exit0；原14表非空记录逐字段保留，Source backfill 与 guarded downgrade/replay通过 |
| `make schema-drift` | exit0；0010、精确25表、differences=[] |
| `make contracts` 与生成契约比较 | 通过 |
| `make check` | exit0；随后两处测试设置修正的 Ruff/format/diff 窄检查通过 |
| Web完整测试 | 17文件、294 passed，exit0 |
| 重建后的 `make parser-security` | 16 passed、无skip，277.08秒，exit0，源码一致 |
| 最终完整 Backend | 2880 passed、9 skipped、6条已有警告，1756.21秒，exit0 |
| 源码与构建凭据 | 完整回归前后源码一致；48项冻结清单逐一匹配，Parser receipt摘要一致 |
| 清理 | 50个原生MySQL生命周期均complete，外层MySQL/Redis/Azurite/MinIO已回收；最终10项只读资源盘点均exit0且为空 |
| 历史V0报告兼容 | 原872399d5…报告经更新的standalone validator通过；原报告未改写，未重复完整V0门禁 |

九项跳过与首次运行一致：四个真实 Milvus 模块、Entra/Azure 外部门禁、独立 E2E 持久化阶段、Codex capability 与真实模型 smoke，均需其独立配置/opt-in；它们不是本轮通过证据。六条警告为已有 Alembic path_separator 弃用提示。

## 可追溯证据与边界

最终运行 ID 为 `eb0fc6e62204`，使用严格隔离 wrapper，不执行连接默认资源的普通 `make test`。测试时 HEAD 为 `17b49c5` 且存在已冻结源码改动；随后源码提交为 `56ee2b5`，不能把提交后 HEAD 冒充测试时 HEAD。本地未跟踪日志、原生 XML、结果和清理盘点保存在 `/private/tmp/tap-task6-backend-*eb0fc6e62204*` 及本计划的 `.superpowers/sdd/` 目录。

- 原生 XML SHA-256：`0876691a5eb728766c4504b048de2e496649603004f5faeee5f6d2591d866d19`。
- 完整日志 SHA-256：`993f66cf2fa5772dcac1f87d5177844ab3bbdbadda1c87dd10475c7aa0db3aa9`。
- 48文件冻结清单 SHA-256：`40cdccfea82494f71c5f553ad6959ebcce80e9c1e460e5b54aee4cf9ed44a70a`。
- Parser image：`sha256:29144e212a4eb6dbe5dc43187a259e03df7bcd375974b58de064e899af35f555`；receipt SHA-256：`65d17427b56e853b66cf5711100070bbc6fff9d062f2375ac0cc5037ec292455`。

失败启动留下的 lock-only state/空 socket 目录保留为失败记录，没有 association 或遗留容器，不伪造为成功执行。历史 V0 的 `mysql_operations.py` 对 Governance adapter 的直接依赖仍作为最终 whole-branch review 的已知项，本任务不宣称全模块边界已经修复。

Source 公共 API/Picker、canonical Milvus 物理迁移属于 Task6A；统一 Gateway/全上下文脱敏属于 Task7；持久 Conversation 与真实界面接入属于后续任务。未执行真实模型、共享 Milvus operator/cutover 或新V1质量门禁。当前已批准的 FWD 产品原型保持为后续接入基线。
