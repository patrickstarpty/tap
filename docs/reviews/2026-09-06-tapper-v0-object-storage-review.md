# Tapper V0 对象存储与真实上传验收

评审日期：2026-09-06。结论：**Task 5 实现与定向验收通过**，源码提交 `e1be27f`。独立审查发现的三项 Important 已修复并通过范围复审。V0 完整出口仍待隔离 Parser 和 Task 5B，RFC-009 保持 accepted，实施计划保持 active。

## 实现范围

- 平台 ObjectStorePort 提供有界 staging、摘要晋级、清单/内容校验、删除和 Project 范围清理。KnowledgeArtifactStore 组合该 Port；两种 Knowledge artifact 实现共享 conformance，保留 Azure 的 server-copy、取消与完整性状态机及 98 项既有契约测试。旧 Azure Adapter 不被称为实现了新的通用平台 Port。
- opaque ObjectRef 绑定 store、可信 Project namespace 与 manifest digest；内部 `art1` locator 另绑定 Revision/kind。清单与内容摘要分别核验，物理 endpoint/bucket/key 不进入公共 API。相同内容的不同 Revision 使用不同逻辑 slot；所有存活清单与删除目标校验完毕后才操作 Provider。缺 payload 不等于缺 manifest，确实缺 manifest 才保留幂等删除。
- 新模板明确选择独立 TAP MinIO；未设置 provider 的旧配置保留 Azure。旧 locator 原字节不变，仅在显式 legacy Azure 配置启用时读取、删除及恢复 reservation；新上传写入 MinIO，无隐式 SQL/数据迁移。混合删除不宣称跨 Provider 原子事务。Operator 实际 Scope 校验与既有 Milvus 全局锁、发布空档保护保持。
- 已确认的 FWD 浅色产品原型继续作为入口。Library 发送实际 File、显示服务端状态，缺可信 runtime Project 时禁用上传。浏览器验证上传/状态，正式 Project API 验证 Answer/Citation/digest/删除和重启；完整 Conversation/history UI 留在 Task 9。

## 构建与取消边界

SDK 已在本工作树独占环境构建安装：aiobotocore 3.9.1 固定官方 commit `c92e345814ad97e5ec0633dbd34be5d26ee90dd3`，botocore 1.43.75。明确配置 endpoint/region/credentials，禁止 ambient AWS/IMDS/proxy fallback。仅替换本工作树 `.venv` 链接，未改共享目标或 node_modules 链接。

MinIO 固定官方 release commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`、Go 1.24.8 与基础镜像平台摘要。实际 Linux arm64 image 为 `sha256:ba4eb93b9afde7d57c6cb98f50786791e20b1eb764ef5a3f1c96bdfad1947d75`，binary SHA-256 为 `84720a66d98704ee72c666b47f3bbf7197bf78869bdb769944f6555c93a423f5`。本地 ignored receipt 核对输入、实际镜像/平台/非 root 用户和二进制；dev/E2E 启动前验证，并核对 container.Image。TAP 存储使用独立服务、卷和凭据，不复用 Milvus MinIO。未发布 registry，也不声明 amd64 执行或逐位一致重建。

S3 操作 deadline 与最多 800 ms 的取消清理预算分开：先协作取消，必要时关闭 owning client transport 并禁用复用，接管迟到 GET Body，重复取消不能中断清理。真实 native SDK 的 partial-body deadline 和重复 caller cancellation 均观察到连接释放、零 pending operation 和零 acquired connector。迟到结果、强制关闭、初始化/关闭竞态及异常保留使用确定性边界 double；不声称能强制终止任意不合作的 Python 协程，也不把本地 TLS 初始化线程称为完全无线程。

## 验证记录

| 验证 | 实际结果与范围 |
| --- | --- |
| 初始 literal RED | S3/MinIO 两项缺模块收集错误；另外有 composition、runtime、逻辑 slot 与 legacy Scope 的独立 RED |
| 初始定向契约 | 244 passed；构建/拥有权、S3/Knowledge/Azure、Operator 等受影响范围 |
| 修复后完整受影响契约 | 162 passed，1.52 s；包括全部 98 项旧 Azure 契约，以及清单/迟到 Body/重复取消/两个 Provider 拥有权检查 |
| 初始真实存储 | 24 passed、零 skip；真实 MinIO/Azurite、共享 artifact conformance、restart 与混合兼容 |
| 修复后真实存储 | 17 passed、零 skip，49.77 s；MinIO 8（含两项 native SDK 取消）、Azure 9；混合测试先验证真实独占 Azurite receipt |
| 完整确定性 E2E | journey 82.286 s、app-restart 3.079 s、Compose-restart 3.050 s；每阶段 expected 1，unexpected/flaky/skipped 均 0 |
| E2E 最终后端持久性 | 28 passed、零 skip、2 条既有 Alembic 警告；真实 SQL/MinIO/Milvus 与明确的 fake model |
| 原型与 Web | 原型交互/请求审计 78 passed；22 项截图检查、40 张图；全 Web 294 passed/17 文件、35.00 s，Root 目视核对 Library/上传/mobile unavailable |
| 原始完整 Backend | 2667 passed、9 skipped、6 条既有 Alembic 警告、1004.15 s，exit 0；开始于 fix1 前，不代表最终修复代码的完整重跑 |
| 最终静态验证 | 全部生产/测试修正后的 make check、额外新 helper mypy、git diff --check 通过 |
| 独立审查 | 初审 Needs fixes；三项修正后范围复审 Approved，未重复全任务审查 |

所有数据测试使用独占临时服务；未运行会指向默认数据库的 plain `make test`。原始完整 Backend 的九项跳过为真实 Milvus 四项、Entra、企业 Azure ACL、单独隔离持久性阶段、Codex capability 与真实模型 opt-in。单独 E2E 已实际验证持久性，但不把不同命令的结果拼成原始全套 zero-skip。

## 原始失败与修正

1. 首轮真实存储为 21 passed、1 failed、1 error：Docker restart 改变动态端口，严格 receipt 拒绝旧 endpoint。改为固定发布一个随机空闲 loopback 端口，未放宽验证；随后 24 项通过。
2. 第一轮完整 E2E 在 Origin 测试断言失败；实际上传为 202 且携带精确 Origin，Playwright `headers()` 省略该字段。改用 `allHeaders()`，保留断言。第二轮业务断言完成后，末尾审计拒绝六次重载时取消的 runtime-mode GET；新增精确 same-Origin/no-query/no-hash GET + ERR_ABORTED 规则及反例，未放行 HTTP 错误或其他路径。一次沙箱启动在任何修改前因 bind 禁止结束；随后完整三阶段通过。
3. 初审确认缺 payload 可绕过存活清单的 Revision 校验、10 ms 后取消脱离且迟到 Body 未关闭、新混合测试未验证 Azure 所有权。增加 metadata-only descriptor、明确清理归属和独占 Azurite receipt，修正后分别验证。真实 receipt 第一轮在 Azure 构造前因 tmpfs inspect 表示差异安全拒绝；按真实 Mounts/HostConfig.Tmpfs 修正后 17 项通过。
4. 最后取消异常用例采用独立 pytest 进程重放紧邻修正前的方法，得到 ObjectUnavailable 而非 CancelledError 的 1 failed，再由当前方法 1 passed；这是明确的 prior-method regression replay，不伪称测试先于代码。其更早的 plugin setup 错误及一次选错测试文件的 no-tests 输出均不计为 RED 或通过。

完整 E2E、全 Web 与原始全 Backend 均早于这次存储边界修复；最终 162 项契约、17 项真实存储和最后 make check 覆盖修正代码，不重复未受影响的 UI 截图/旅程或把早期结果冒称最终源运行。所有成功与失败 owned run 均完成清理；Root 又按最后两个 run 的精确 Project/owner 标签确认容器、卷、网络为空。本地已构建镜像及 ignored receipt 保留供后续工作使用。

## 后续门禁

隔离 Parser 尚未实现；其无网络单次容器、私有监督进程、multipart 前置限制和资源预算已写入[实施计划](../plans/2026-09-04-tapper-knowledge-web-automation-platform.md)，下一项按该边界实施。Task 5 不证明真实模型质量、真实 Milvus rebuild、企业 Azure 四索引、Source ledger、持久 Conversation、Recorder/Jenkins 或 LAN/生产就绪；V0 完整门禁继续待 Task 5A/5B。
