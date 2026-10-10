# TAP 产品路线图

本文件是 TAP 全局产品路线：做什么、为什么、按什么顺序、做到哪了。实施步骤见各版本链接的 spec 与 plan；V1 的验收标准与子项目以 [V1 总纲](2026-09-29-v1-roadmap.md) 为准。

## 状态约定

| 状态 | 含义 |
| --- | --- |
| 已完成 | 已合入 `main`，附 PR 编号 |
| 进行中 | 有 plan 或已在实施，附分支或 PR |
| 已设计 | spec 已写，未进入实施 |
| 未设计 | 仅有方向，没有 spec |

每次 PR 合并、spec 定稿或范围调整时，同步更新本文件对应行。

## 总览

```mermaid
flowchart LR
  B[基础<br/>已完成] --> V1[V1 可信知识问答<br/>进行中]
  V1 --> V2[V2 主动 Tapper<br/>已设计]
  V2 --> V3[V3 测试闭环<br/>未设计]
  V3 --> V4[V4 产品化<br/>未设计]
```

| 版本 | 主题 | 用户价值 | 状态 |
| --- | --- | --- | --- |
| 基础 | 知识问答原型与完整产品原型 | 可演示的 Tapper 与全模块设计基准 | 已完成 |
| V1 | 可信知识问答 | 基于项目知识得到有依据、可追溯的回答 | 进行中 |
| V2 | 主动 Tapper | 不用提问，Tapper 主动发现问题并沉淀团队知识 | 已设计 |
| V3 | 测试闭环 | 知识 → 测试设计 → 自动化执行 → 结果回写 | 未设计 |
| V4 | 产品化 | 多人、多项目、可在企业环境安全部署 | 未设计 |

## 基础（已完成）

- 知识摄取与问答：PDF/DOCX/MD/TXT 与有界 XLSX 摄取、Milvus 混合检索、引用回答、Knowledge Graph。
- 流程图图片知识与路径回答（#27）；pytest/Allure 结果接入与知识切片生命周期（#28）；对话历史与输入控件（#29）；Tapper 猫头鹰品牌（#26、#35）。
- `/prototype` 完整产品原型：Tapper、Test Management、Test Insights、Low Code Automation、跨模块关联与悬浮助手；Tapper 交互同步（#33）。
- 文档精简与 V1 总纲（#30、#31）。

## V1：可信知识问答（进行中）

退出标准：[V1 总纲](2026-09-29-v1-roadmap.md) 中 5 项能力的“工程完成”全部勾选。

| 项 | 状态 |
| --- | --- |
| 子项目 0 文档精简与总纲 | 已完成（#30、#31） |
| 子项目 1 基础设施收敛 | 已完成（#32） |
| TAP AI 前端清理原型内容 | 已完成（#34） |
| 子项目 2 可观测性（能力 5） | 已完成（#36） |
| 推荐问题（能力 1 的一部分） | 已完成（#38、#39） |
| 子项目 3 可靠问答（能力 1） | 未设计（推荐问题部分见上一行） |
| 子项目 4 知识图谱脉络分析（能力 2、3） | 进行中（PR 1 抽取分批、PR 2 项目级图、PR 3 关系分析与边引用已合并到 develop；PR 5 门禁工具已合并，真实门禁待运行） |
| 子项目 5 Skills/Agents（能力 4） | 未设计 |

## V2：主动 Tapper（已设计）

设计：[Tapper 主动 Agent 设计](../specs/2026-09-30-tapper-proactive-agent-design.md)。前置：V1 能力 4、能力 5 与 worker 可靠性修复完成。

| 项 | 内容 | 状态 |
| --- | --- | --- |
| V2.1 | 主动框架、TAP AI 内部剧本（知识缺口、Skill 影响、纠正与 FAQ 沉淀、流程沉淀为 Skill）、动态入口与设置 | 已设计 |
| V2.2 | TAP AI 与平台事件契约 ADR、平台事件接入、Test Insights 失败与 flaky 分析、悬浮助手提示 | 已设计 |
| V2.3 | Test Management 与 Low Code Automation 事件和采纳动作 | 已设计，随 V3 后端就绪 |

退出标准：V2.1、V2.2 工程完成；在真实项目中度量提议采纳率。

## V3：测试闭环（未设计）

方向：Tapper 生成可审查的测试方案并导入 Test Management，经 Test IR 映射为 Low Code Automation 的 Web 自动化，由 Jenkins 执行并回写结果。

- 解冻 Test Management 后端，建设 Low Code Automation 后端。
- 重新评估已暂缓的 [ADR-021](../../decisions/2026-09-04-adr-021-knowledge-first-web-automation-delivery.md) 与 [ADR-025](../../decisions/2026-09-04-adr-025-jenkins-first-execution-provider.md)。
- 完成 V2.3。

## V4：产品化（未设计）

方向：身份与多项目、局域网与生产安全设计、企业级检索（Azure AI Search 四索引）与生产加固。当前 Tapper Demo 为回环绑定、无认证，在此之前不做局域网或生产承诺。
