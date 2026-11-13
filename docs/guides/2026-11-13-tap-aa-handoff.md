# TAP AA 交接：真实 AIA 尚未通过

## 结论与验收边界

- 基线为 `develop` 的 `cc98ad6231e5a5695f7b82d73f81faa9cb995808`；本交接提交在其上增加脱敏诊断和证据，**没有修复真实 AA 报价成功率**。
- 目标是 TAP 的 LCA 页面内使用 Tapper，自主完成 FWD/AIA 询价，验证每个输入，保存为可编辑 BDD/LCA 动作，编辑后独立回放并录屏。不是外部原型或人工代点演示。
- 历史 LCA 编辑后回放有成功证据；真实 Qwen AA 仍未通过。合成 provider 的全链成功不能证明真实模型自主完成。
- 最新批准的一轮已停止。余量不是下一轮许可，不应自动再发起付费调用。

## 最后一次真实运行

| 项目 | 已核实事实 |
| --- | --- |
| 基线 | `cc98ad62` |
| 入口 | 正式 LCA 内嵌 Tapper，真实 Qwen Max / OpenCode |
| 任务 | `329923c38ec1495aa787924509036936` |
| 结果 | `recovering`，首个 navigate 为 unknown；未验证输入、未得到报价 |
| 模型 | 配置别名 `qwen-max`，映射 `dashscope/qwen-max` |
| 消耗 | 2 次均结算；11,389 输入 / 139 输出 tokens；实际 USD 0.019112，预算记账 USD 0.019113 |
| 停止 | 浏览器 stop 已确认，无运行进程；保留隔离数据库，不改写 unknown |

源证据：[筛选后的运行与对照数据](assets/2026-11-13-aia-navigation-unknown.json)。
完整历史：[正式入口验收记录](2026-11-11-lca-inline-tapper-validation.md)、[复盘](2026-11-11-aa-lca-lessons-learned.md)。

## 已知问题与尚未知原因

1. **最新可复现边界是导航，不是点击。** 同一站点、同一现有上游代理：直接 controller 新会话约 2.965 秒完成；实际 LocalBrowserRuntime 加生产 site-proxy 的新会话等待 30.013 秒后 `navigation_wait_timeout`，空标题、零字段。两次都只导航/观察，没有模型、输入或报价。差异尚不能直接归因于代理策略中的某一条规则；还需对比该边界的网络结果。
2. **历史证据不能倒推。** 最新付费运行只留下 generic unknown；新增固定分类只适用于之后运行。再前一轮为 7 次调用后 no_progress、零业务写，不能把它解释为点击问题。
3. **另有独立动作差距。** controlled `node.click()` 不等于原生指针事件；自有页面的 pointerdown 下拉可复现。当前已按实际执行能力缩小模型工具声明，不再虚称 controlled press/contenteditable/select 支持。
4. **原生切换有明确安全差异。** 既有 epoch/串行/no-retry 外壳可兼容原生输入，但 focus 时动态变为敏感字段的反例证明原子目标保护不等价。不能通过关闭 control_enabled 或删检查来包装成功。

## 本交接新增内容

- `deploy/browser/live-control.mjs`：仅给 navigation wait 的 timeout/其他失败换成固定错误分类，不增加重试或改变截止时间。
- `deploy/browser/inspector.mjs`：输出 allowlist 分类，不泄露原生错误、URL/query 或堆栈。
- `deploy/browser/test/navigation-diagnostics.test.mjs`：固定分类、隐私、一次派发及失败后保持锁定。
- `deploy/browser/test/inspector-lifecycle.test.mjs`、`live-control.browser.mjs`：分类投影和真实 Chromium 截断导航回归。

验证：40/40 focused unit，157/157 全部 browser unit，77/77 owned Chromium control；静态独立审阅无阻断项。并未运行新的真实模型报价验收。

## 下一位工程师先做什么

1. 从最新 `develop` 开始，不修改原工作目录未提交内容。先读仓库 `AGENTS.md` 和相关 `.agents/skills`。
2. 保持模型关闭，用单次只读导航复现实际 LocalBrowserRuntime + site-proxy 的 30 秒等待。固定目标为 `https://www.aia.com.hk/en/products/health/cancer-care-essence`；新私有会话，现有显式 loopback 上游，`network_mode='public_web'`、`control_enabled=True`；control_sync 到 agent epoch 1 后只 dispatch navigate，finally cancel。
3. 对照当前代理的主文档/重定向/资源处理与导航等待，新增固定错误分类或受限计数即可，不保存任意 header、正文、个人数据或凭据。不要先加长超时或改安全策略。
4. 只有免费复现得到明确原因并修复后，再讨论新的有限付费验收。通过必须含所有合成字段读回、正数月保费、生成可编辑脚本及新会话回放；不允许人工 selector 救场冒充自主完成。
5. 如果决定采用原生 Playwright 作为独立模式，先明确适用数据/会话和与现有保障的差异并完成审阅；不能偷偷改默认模式。

免费回归命令（本地浏览器测试需允许 Chromium/loopback）：

```sh
node --test deploy/browser/test/*.test.mjs
node --test deploy/browser/test/live-control.browser.mjs
node --test deploy/browser/test/native-playwright-baseline.browser.mjs
node --test deploy/browser/test/owned-native-shell.browser.mjs
git diff --check
```

真实任务的既有脚本入口是 `scripts/run-browser-core-e2e.py --mode real --site public --execution-mode local --lca-inline-real`，需明确单轮授权、审阅后的业务 contract、Qwen 配置、双预算上限。此文档不是再次付费运行的授权。不要读取或输出本地 `.env` 密钥。
