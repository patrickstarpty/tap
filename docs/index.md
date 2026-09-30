# TAP 文档索引

当前有效文档只有下列几类；历史文档已冻结在 [archive](archive/index.md)。

- [现状架构](architecture.md)：代码中可验证的系统结构与已知差距。
- [产品路线图](superpowers/plans/2026-10-01-product-roadmap.md)：全局版本顺序、已完成与规划中的工作。
- [V1 总纲](superpowers/plans/2026-09-29-v1-roadmap.md)：V1 能力、验收标准与子项目顺序。
- [设计 spec](superpowers/specs/)与[实施计划](superpowers/plans/)：按 superpowers brainstorming / writing-plans 流程产出。
- [架构决策](decisions/index.md)：跨模块决策记录。

## 指南

- [TAP 产品原型基准规范](guides/2026-09-22-product-prototype-baseline.md)
- [前端开发上手指引](guides/2026-09-27-frontend-developer-onboarding.md)
- [后端开发上手指引](guides/2026-09-27-backend-developer-onboarding.md)
- [Tapper 开发者指南](guides/2026-09-13-tapper-developer-guide.md)
- [TAP 浅色视觉规范](guides/2026-09-05-tap-light-design.md)
- [文件类型图标与示例文件](guides/2026-09-06-file-type-icons.md)
- [TAP 客户演示指南](guides/2026-09-04-customer-prototype-demo-guide.md)
- [知识审核工作台](guides/2026-09-27-knowledge-review-workbench.md)
- [Azure 接入扩展点](guides/2026-09-29-azure-integration.md)
- [LiteLLM 模型目录指南](guides/2026-09-29-litellm-models.md)

## 约定

- 设计 spec 放 `superpowers/specs/`，实施计划放 `superpowers/plans/`，文件名为 `YYYY-MM-DD-<topic>[-design].md`。
- ADR 仅用于跨模块决策，只保留一个 `status` 字段。
- `archive/` 只读，不再维护状态与链接。
