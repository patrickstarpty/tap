# Tapper 品牌与运行命名空间迁移评审

## 评审结论

**结论：`pass`。** RFC-010 的 clean cut 已在当前受控树、Web 产品壳、Backend/本地运行命名空间、治理文档、图表和客户截图中一致落地。两个规定视口的真实媒体仿真、minimap jump 观测和全部回归均通过。当前主线的 Plan 按 `planned → active → completed` 收口，RFC 从 `accepted` 进入 `implemented`。

没有 P0/P1 发布阻塞。新的 Playwright 回归让真实 Chromium 在 `1280x720` 与 `390x844` 下都报告 `matchMedia("(prefers-reduced-motion: reduce)").matches=true`，确认代表性元素禁用 motion，并观测到 minimap jump 精确调用 `scrollIntoView({ behavior: "auto", block: "start" })`。未为旧名称增加兼容 alias、dual-read、backfill 或进程 shim，也未删除旧外部资源。

## 范围与提交基线

- 命名归一化前基线：`0eab8013a82f5a2cbca7a862702a2d898b483d6d`。
- 评审证据树：从 `0eab801` 到包含本评审的最终验证提交，共 14 个提交、318 个变化路径。
- 收口前提交 `05534d9821b73271d9caf3978f6d8814479b7bc8` 将 Plan 从 `planned` 改为 `active`，RFC 保持 `accepted`；本评审所在提交纳入品牌门禁和 reduced-motion 测试，并将 Plan/RFC 分别关闭为 `completed`/`implemented`。
- 经用户明确授权，最后三次未推送提交整理为上述两次提交，以修正原来的状态跳转与终态重开。整理前历史保存在 `codex/tapper-before-history-fix`；`b7d18fc` 及更早提交保持不变。整理只调整提交顺序和四份文档中的历史说明，生产代码、测试与截图保持逐字节一致，因此下述既有验证证据仍适用于相同实现。
- 最终仓库共有 629 个 tracked path。`0eab801..HEAD` 下 Web assets 只有两个新增品牌资源：`tapper-mark-ink.svg` 与 `tapper-wordmark-ink.svg`。

评审范围内的 14 个提交依次为：

```text
a04870f docs: define tapper naming migration
07c90db docs: plan tapper namespace migration
17765bf test(brand): add tapper namespace guard
393f945 feat(web): rebrand workspace as tapper
29d0f2c refactor(backend): move runtime to tapper namespace
bfdae8f chore(runtime): switch local demo to tapper
faba449 fix(runtime): restore tapper e2e integration
1f223e8 docs: normalize tapper product terminology
aa12185 docs: fix tapper article grammar
63d60a3 docs: refresh tapper prototype visuals
de972f7 fix: correct prototype screenshot evidence
b7d18fc fix: restore readable graph evidence
05534d9 docs: activate tapper migration verification
(本评审所在提交) feat: complete tapper product migration
```

## 零残留与品牌守卫

`Makefile` 已声明 `.PHONY: brand-check`，`make check` 会调用 `make brand-check`，目标执行：

```text
uv run --project apps/backend python scripts/check_brand_namespace.py
```

守卫证据：

- `apps/backend/tests/contract/test_brand_namespace_contract.py`：2 passed。
- 在临时 Git 仓库中加入一个由 ASCII bytes 构造的 tracked 违规文件后，CLI 输出 `content: controlled.txt` 并精确退出 `1`。
- 当前真实仓库 `make brand-check`、uv scanner 与 `python3 scripts/check_brand_namespace.py` 均无输出、退出 `0`。
- `git ls-files apps/web/assets | sort` 只列出两个 approved ink SVG 与既有 `apps/web/assets/plates/paper-ground.png`；从基线新增的 Web asset 恰好是两个 ink SVG。
- 36 个 PNG 导出和 6 个未采用的 black/orange/white SVG 仍为 user-owned untracked files；42 项清单 digest 保持 `d33279ea83a0723623dbcacdded8b6485fafd1ab0a9394125c86a1c6669329a4`。根 `.gitignore` 的 `.DS_Store` 规则有效，仓库没有 tracked `.DS_Store`，也没有任何未采用导出进入 staged/tracked scope。

## 静态、构建与测试证据

| 命令                                                 | 结果                                                                                                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `make contracts`                                     | exit 0；OpenAPI/SSE 与 Web client/types 重新生成，无额外 tracked diff。                                                                     |
| `make check`                                         | exit 0；Ruff、format、mypy、shell syntax、contracts、ESLint、Prettier、architecture、TypeScript、Vite build 和嵌套 `brand-check` 全部通过。 |
| `make test`                                          | exit 0；Backend `2253 passed, 26 skipped`，Web `249 passed`。                                                                               |
| `corepack pnpm --dir apps/web run check`             | exit 0；14 个 Web test file 之外的 lint/format/architecture/build 门禁全部通过。                                                            |
| `corepack pnpm --dir apps/web test -- --run`         | exit 0；`14 passed` test files，`249 passed` tests，0 failed。                                                                              |
| `corepack pnpm --dir apps/web run prototype:capture` | exit 0；20 个 Playwright test 全部通过，40 张 canonical JPEG 完整且 byte-distinct。                                                         |
| `git diff --check`                                   | exit 0。                                                                                                                                    |

以下时间来自提交整理前的验证记录：首次完整 Backend pytest 为 `2253 passed, 26 skipped, 6 warnings in 205.17s`；第二次重跑为相同计数、`161.22s`；补齐 reduced-motion 用例后的最后一次全量重跑为相同计数、`171.67s`，随后 Web 为 14 个 test file、249 个测试、0 failure、`21.40s`，完整 `make test` wall time `195.18s`。随后最后一次 `make contracts`、`make check`，以及显式 Web check 和完整 capture 的 wall time 分别为 `1.19s`、`11.62s`、`9.28s` 与 `18.80s`。26 个 skip 是明确 opt-in 的真实外部集成/模型路径，不是本迁移门禁的 flake 或漏跑。

## 隔离 Demo E2E 与外部资源边界

`make demo-e2e` 从专属 `tap-tapper-e2e` 零状态开始，创建专属 MySQL、Redis、Azurite、LiteLLM、Milvus/etcd/MinIO 资源，执行 Alembic `0001` 至 `0005_projection_lineage`、Milvus RBAC bootstrap、文档 ingestion、grounded answer/citation、应用与 Compose restart persistence，并只清理/重建该隔离项目资源。

最终结果：`12 passed, 2 warnings in 2.27s`、0 skipped、0 flake；命令 wall time `232.02s`，终端输出 `Tapper isolated E2E journey passed.`。

本次没有执行 `demo-reset`，没有删除旧 Compose project、volume、Blob container、Milvus collection/alias、Redis key 或 LiteLLM 配置资源。旧命名空间只因 clean cut 不再被新进程读取；其删除仍需另行人工授权和范围确认。

## 40 张客户截图证据

截图目录恰好包含 40 个 JPEG；40/40 均为 `1280x720`，40 个 SHA-256 均唯一。按下表路径、尺寸和完整 digest 生成的规范化 manifest SHA-256 为 `412282761872504aed01e640b08361eed8cda9040c9b5f1511290a6af2273d1d`。README 的 6 个引用和客户演示指南的 40 个引用合计 46 个，`prototype_jpeg_links=46 missing=0`。

| 截图                                                                                                            | 尺寸     | SHA-256                                                            |
| --------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------ |
| [01-tapper-new-chat.jpg](../assets/prototype-demo/01-tapper-new-chat.jpg)                                       | 1280x720 | `ab3f58856ea7db5952c0d624bef2c33f6ae5f6a312876eb4918bf2223424b088` |
| [02-tapper-conversation-minimap.jpg](../assets/prototype-demo/02-tapper-conversation-minimap.jpg)               | 1280x720 | `c72f6fa42f25af7522a08f5eb41fce5c5e955b39db1e6bc21367b550a2c387da` |
| [03-tapper-model-selector.jpg](../assets/prototype-demo/03-tapper-model-selector.jpg)                           | 1280x720 | `fd6afa056b89da70cfa0ea15ea225750f82c59658cd28eb20fd3dba6e4df8e8c` |
| [04-tapper-context-menu.jpg](../assets/prototype-demo/04-tapper-context-menu.jpg)                               | 1280x720 | `43df67b9f1731b1ecf1c3638335078e01f1bfe75ac9186692d465e926a1cb010` |
| [05-tapper-source-picker.jpg](../assets/prototype-demo/05-tapper-source-picker.jpg)                             | 1280x720 | `988344503b1545dd385884bd59c350ce1c15a673e7dddb35dac3654a74177b1f` |
| [06-tapper-agent-picker.jpg](../assets/prototype-demo/06-tapper-agent-picker.jpg)                               | 1280x720 | `474d27c98f1c695f3de244329a97867f32172231c132160827df689281529d34` |
| [07-tapper-skill-picker.jpg](../assets/prototype-demo/07-tapper-skill-picker.jpg)                               | 1280x720 | `96df9461f163fb2f8d182eee7769996ace147e1cb0fda9786e0c8dd9a8601f1d` |
| [08-tapper-selected-context.jpg](../assets/prototype-demo/08-tapper-selected-context.jpg)                       | 1280x720 | `98ca947c0d9538a2bafc5e6aaa4a854378f16230d046ed49af3916bd5065dee5` |
| [09-tapper-agent-catalog.jpg](../assets/prototype-demo/09-tapper-agent-catalog.jpg)                             | 1280x720 | `3b4808e5f67c2b3f141a3fa789e01b5b3fc264c0cc771a27d9a3168924efd774` |
| [10-tapper-create-agent.jpg](../assets/prototype-demo/10-tapper-create-agent.jpg)                               | 1280x720 | `ca3b41acc44396aff934aa3902bf8cfa11c183cef0497bd9144edcbe5c205bfd` |
| [11-tapper-skill-catalog.jpg](../assets/prototype-demo/11-tapper-skill-catalog.jpg)                             | 1280x720 | `8e647f4ce18fd621832fe4f67fc6ce8e46960726dbd6df6b1bd08a4158a664d1` |
| [12-tapper-create-skill.jpg](../assets/prototype-demo/12-tapper-create-skill.jpg)                               | 1280x720 | `e1539344d6b312d48c7e9c771e7b708f7d9de79ac33dec77566a71f861a92b10` |
| [13-tapper-library-empty.jpg](../assets/prototype-demo/13-tapper-library-empty.jpg)                             | 1280x720 | `057535a6f6cd8c0493c777a65b30dc306f841d4f25cf575dc11c0101365521bb` |
| [14-tapper-library-all.jpg](../assets/prototype-demo/14-tapper-library-all.jpg)                                 | 1280x720 | `34da2566310d9e2f2e5d854a2556ac51674a79d91df5547e183f430a4aeafe02` |
| [15-tapper-library-filtered.jpg](../assets/prototype-demo/15-tapper-library-filtered.jpg)                       | 1280x720 | `4a690cc415749e7b90dd7ecec2fd2d65a35935df74b33e76c5fc07963c064d7c` |
| [16-tapper-add-source.jpg](../assets/prototype-demo/16-tapper-add-source.jpg)                                   | 1280x720 | `cac1358c0549308b54158f9e0348cca6827caa61099d2b47043d5c624cf2bb45` |
| [17-tapper-knowledge-graph.jpg](../assets/prototype-demo/17-tapper-knowledge-graph.jpg)                         | 1280x720 | `068d9b65e8e46682ec504cde3c5f061466f7e03da52c802f820e54deed3863f0` |
| [18-tapper-knowledge-graph-node.jpg](../assets/prototype-demo/18-tapper-knowledge-graph-node.jpg)               | 1280x720 | `5c4d0cd2a9790a41c10b64d97b36df1f8205ee32c52b2f33296d55f46a7f76e8` |
| [19-test-management-plans.jpg](../assets/prototype-demo/19-test-management-plans.jpg)                           | 1280x720 | `55d691b06b3e360401e0f12f1274deb721150df6d7aa2a88bab4f9effb06f256` |
| [20-test-plan-detail-linked.jpg](../assets/prototype-demo/20-test-plan-detail-linked.jpg)                       | 1280x720 | `4dcc5d97d35f701a0a29ac4bd71de25d9edc6f887b8b977e034b36ae442be90e` |
| [21-test-plan-run-config.jpg](../assets/prototype-demo/21-test-plan-run-config.jpg)                             | 1280x720 | `ae0899cd256c33a008dc3afb0182bb7bc0c20df6b0fe2b91eb0ab7295cba1250` |
| [22-test-plan-run-result.jpg](../assets/prototype-demo/22-test-plan-run-result.jpg)                             | 1280x720 | `dcdcfa5a1e59b11b3dbfab8047607c1e1932302e6a19f56e84708846d130f1f7` |
| [23-test-plan-detail-unlinked.jpg](../assets/prototype-demo/23-test-plan-detail-unlinked.jpg)                   | 1280x720 | `1a117938a9edd0dab52bd658a2840d07bf733b48ee6a15b6969cdb9ed18c79c2` |
| [24-test-management-test-data.jpg](../assets/prototype-demo/24-test-management-test-data.jpg)                   | 1280x720 | `25ec8917c066040542db49199d2fcfb78675f7e5c4f9b07c56e4cdadc27dd188` |
| [25-automation-library.jpg](../assets/prototype-demo/25-automation-library.jpg)                                 | 1280x720 | `7864afce98f87d952ff275eb91bfdaf5b2e0e549aece698696df3b08f78406fd` |
| [26-create-automation.jpg](../assets/prototype-demo/26-create-automation.jpg)                                   | 1280x720 | `70aa992b817718d114a6f8e4b6243862ecf336e6bfce61ac6e9472cae504349e` |
| [27-web-automation-bdd-mapping.jpg](../assets/prototype-demo/27-web-automation-bdd-mapping.jpg)                 | 1280x720 | `31b373d2b473e815a1b18fd726e446f916a6ab4de6a1982b0447268a7a970de4` |
| [28-web-automation-action-editor.jpg](../assets/prototype-demo/28-web-automation-action-editor.jpg)             | 1280x720 | `e6b9b8158f074cca74c84c63de0e6e9adc97023b2a95e3471c83ff5c31a7d877` |
| [29-web-automation-ai-agent.jpg](../assets/prototype-demo/29-web-automation-ai-agent.jpg)                       | 1280x720 | `ef95ebd073874c38532fa115158b60bf34101056da77ff9211fbe0f7d247f232` |
| [30-web-automation-run-history.jpg](../assets/prototype-demo/30-web-automation-run-history.jpg)                 | 1280x720 | `8fa28380dd23ccdbce7a1107eb9c1059f68fa110afbf0b8989baa25b8e6e77bf` |
| [31-mobile-automation-device.jpg](../assets/prototype-demo/31-mobile-automation-device.jpg)                     | 1280x720 | `0e4f7d976b49d1d2d94668dc50f81f8cd0bd294412a1ee5521f37dd021e121c9` |
| [32-mobile-automation-run-result.jpg](../assets/prototype-demo/32-mobile-automation-run-result.jpg)             | 1280x720 | `9d627ef1822a4137fe7347f10414d79cf1803661cd9c53dde702f7c665063d67` |
| [33-tapper-test-plan-first.jpg](../assets/prototype-demo/33-tapper-test-plan-first.jpg)                         | 1280x720 | `24783b924ddea1ec7772533fa806a26f498055a1c456ba8a4cdc7627b47ef5e1` |
| [34-tapper-test-plan-review.jpg](../assets/prototype-demo/34-tapper-test-plan-review.jpg)                       | 1280x720 | `4297bb9f44e1016ad1ae4b7edd404506a84a36722281febc1ada5ca743603dfb` |
| [34b-tapper-generate-linked-automation.jpg](../assets/prototype-demo/34b-tapper-generate-linked-automation.jpg) | 1280x720 | `ebc662c5817ba863ecb590fd6f3e72d818bcd7b988d2e0bd8ae670ad2ae53560` |
| [35-tapper-channel-choice.jpg](../assets/prototype-demo/35-tapper-channel-choice.jpg)                           | 1280x720 | `d6f40b0d8f2183c4478e3a417a8ad5dc617c21de127fa192181461071b4709cb` |
| [36-tapper-linked-artifacts.jpg](../assets/prototype-demo/36-tapper-linked-artifacts.jpg)                       | 1280x720 | `402d0fece6c7d120485e7339112116dbb735278279b67e36604e859cce8ae6e9` |
| [37-tapper-minimap-preview.jpg](../assets/prototype-demo/37-tapper-minimap-preview.jpg)                         | 1280x720 | `a937086041d2c7f197eba7b6929f5b560b778f221ddbb2348bb1c18bf888d374` |
| [38-tapper-sources-collapsed.jpg](../assets/prototype-demo/38-tapper-sources-collapsed.jpg)                     | 1280x720 | `47b93cbc55189fc889575fda1c95968c58c3dfd970774afe88fb1555ecfb5436` |
| [39-tapper-sidebar-collapsed.jpg](../assets/prototype-demo/39-tapper-sidebar-collapsed.jpg)                     | 1280x720 | `d2bcd3f9d715d959cf258a2be9ed02b3f130198d92756f1a611c17dde82a3560` |

## 浏览器视觉与可访问性检查

使用专属 `127.0.0.1:15175` Vite server 和 browser-client/in-app browser，在 `1280x720` 与 `390x844` 两档执行交互检查。检查结束后恢复 viewport 并关闭临时 tab/server。

### 1280×720

- TAP 平台 badge、Tapper mark/wordmark 均清晰；产品 rail、二级 sidebar、主会话区和 Knowledge sources 维持既定层级，无拥挤、遮挡或水平滚动。
- 键盘聚焦 Tapper 控件时，computed focus indicator 为 `2px solid rgb(79, 70, 229)`、offset `2px`。
- Tapper sidebar 的命名按钮在 collapsed/expanded 状态分别报告 `aria-expanded=false/true`；heading 可见性随状态同步。
- Knowledge sources 的命名按钮同样报告 `aria-expanded=false/true`；收起时 search 隐藏，展开后恢复。
- 模型按钮报告 `aria-expanded=true`，`Models` menu 包含 5 个 `menuitemradio`；选择 `GPT-5.6 Luna` 后 trigger 名称更新且菜单关闭。
- Composer 在填入文本后 Send 由 disabled 变为 enabled；连续提交两个问题后出现两个 minimap tick。hover 第二个 tick 显示对应问题 tooltip。

### 390×844

- 实际 `innerWidth=390`、`innerHeight=844`、`scrollWidth=390`、`scrollHeight=844`，无水平溢出。
- 键盘聚焦 `Expand sidebar` 时保持同一 `2px` indigo focus ring 与 `2px` offset。
- Tapper 和 Knowledge sources 以互斥 drawer 呈现，命名的 collapse control、Tapper heading、source heading/search 都可见，打开时仍保持 `scrollWidth=390`。
- 模型菜单保持 5 个 radio choice 且在 viewport 内可读；Composer 保持 focus treatment，填入文本后 Send enabled。
- 两个 minimap tick 保留；hover 第二个 tick 显示精确文本 `Create BDD test cases for life insurance underwriting`。
- Browser console 的 warning/error 列表为空。视觉检查未发现需修改的 UI 缺陷。

### Reduced motion

两档 viewport 的 live CSSOM 都包含 `@media (prefers-reduced-motion: reduce)`，并对 `.tap-product-shell`、左右 panel/layout、navigation item、quick prompt 和 minimap marker 设置 `animation: none` / `transition: none`。Minimap jump 的运行代码还会根据同一 media query 在 `auto` 与 `smooth` scrolling 间选择。

in-app browser 的宿主系统偏好为 `reduce=false`，browser-client 未暴露 media emulation capability；该会话因此只作为正常 motion 的交互/视觉证据，不再被误报为 reduced-motion 执行证据。独立 Playwright Chromium spec 使用类型化 `test.use({ reducedMotion: "reduce" })`，通过 Playwright `page.emulateMedia` 在测试开始前实际应用偏好。Playwright 1.62 已不再提供同名内建一级 option，因此这层 fixture 是对精确测试 API 的显式、可观测适配，不覆盖页面的 `matchMedia` 实现。

两个视口分别执行并通过同一组断言：页面导航前后 `matchMedia` 均为 `true`；实际 viewport 精确为 `1280x720` / `390x844`；`.tap-product-shell`、`.tap-navigation-item`、`.tap-question-marker` 的 computed `animationName="none"`、`animationDuration="0s"`、`transitionProperty="none"`、`transitionDuration="0s"`；连续发送两个问题后，第二个 minimap tick 初始 `aria-current=true`，点击第一个 tick 后 active state 切换；拦截 `Element.prototype.scrollIntoView` 只记录到 `[{ behavior: "auto", block: "start" }]`。最终 focused 结果为 `2 passed (4.0s)`；完整 capture 的 Playwright runner 结果为 `20 passed (18.2s)`，对应整命令 wall time `18.80s`。首轮测试曾在两个视口都以 `Expected: true / Received: false` 失败，定位到测试 fixture 未应用媒体仿真；修正 fixture 后无需改动 `TapperChat.tsx` 或其他生产代码。

## Impeccable detector 分类

最终 detector 使用 brief 规定的三个目标精确执行一次，返回两个 finding 并退出 `2`；没有隐藏或 ignore finding：

| Finding                                                   | 等级     | 分类与结论                                                                                                                                                     |
| --------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `overused-font` at `TapProductPrototype.css:179`          | warning  | 非迁移缺陷。`Inter` 只作为紧凑 TAP badge 的 fallback，保留既有品牌排印；主体显示字体、正文和数据字体仍各自承担明确层级。Task 7 不扩大为 typography redesign。  |
| `codex-grid-background` at `TapProductPrototype.css:1764` | advisory | Contextual false positive。目标正是可缩放、可拖动、可选节点的 Knowledge Graph canvas；craft floor 明确允许 canvas/map/blueprint/measurement surface 使用网格。 |

Detector exit `2` 表示 finding 被报告，不等于评审失败。经上下文核验后两项均不造成用户影响、WCAG 违规、品牌残留或迁移回退，因此没有 UI 修复。

## 技术质量摘要

Implementation Integrity verdict：**pass**。实现表达了明确的 TAP 平台/Tapper 工作区双层品牌系统，mark/wordmark、ARIA 名称、响应式 drawers、模型/Composer/minimap 状态和 clean-cut 运行标识相互一致。

| #        | 维度                     | 分数                  | 关键证据                                                                                   |
| -------- | ------------------------ | --------------------- | ------------------------------------------------------------------------------------------ |
| 1        | Accessibility            | 4/4                   | 语义 landmarks/roles、ARIA state、focus indicator 与两档真实 reduced-motion 执行均已通过。 |
| 2        | Performance              | 3/4                   | Vite build 通过且无交互卡顿；本轮未做独立 runtime profile。                                |
| 3        | Responsive Design        | 4/4                   | 1280×720 与 390×844 交互通过，移动宽度无 overflow，drawers/composer/minimap 保持可用。     |
| 4        | Theming                  | 3/4                   | 当前单一 light product surface 一致；本 RFC 未承诺 dark-mode 切换。                        |
| 5        | Implementation Integrity | 4/4                   | 品牌守卫、资源范围与产品特定行为一致；detector 两项均已验证和分类。                        |
| **总分** |                          | **18/20 — Excellent** | **无 P0/P1；motion-sensitive path 在桌面与移动 Chromium 均有实际执行证据。**               |

未发现需要修改生产 UI 的问题；正向证据是桌面/移动状态一致、产品特定语义明确、焦点与 motion-sensitive path 均保留。Detector 的 warning/advisory 均已在上下文中核验，不构成 P0-P3 用户影响。若未来扩大到 dark theme 或性能预算，应由对应 RFC/计划另行设定验收，而不在本迁移评审中推断完成。

## 最终决定

零残留、品牌层级、运行 clean cut、全量回归、隔离生命周期、40/40 截图一致性、桌面/移动常规交互、真实 reduced-motion 执行与旧资源非删除约束均已满足。评审结论为 `pass`，允许 RFC-010 从 `accepted` 进入 `implemented`，实施计划从 `active` 进入 `completed`。
