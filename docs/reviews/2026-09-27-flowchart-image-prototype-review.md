# 流程图图片审核原型对照

日期：2026-09-27。范围：`apps/web` 的同一 `/prototype` 产品壳。基准来自本功能分支改动前的 `HEAD` 快照；变更后来自当前工作树。截图均为英文界面、1280×720 布局视口、2× 像素密度（2560×1440 PNG）。

| 视图 | 改动前 | 改动后 | 对照结论 |
| --- | --- | --- | --- |
| 既有资料审核 | [原有资料审核](../assets/flowchart-image/2026-09-27-shared-review-before.png) | [变更后的资料审核](../assets/flowchart-image/2026-09-27-shared-review-after.png) | 原有审核内容与操作仍可达；背景 Library 增加一项流程图来源。 |
| 新增流程图审核 | 无对应来源 | [流程图审核](../assets/flowchart-image/2026-09-27-flowchart-review-after.png) | 在原有审核弹窗中并排显示图像、方向和条件，复用现有提交、复核与发布操作。 |

组件测试覆盖完整产品壳、既有模块可达、原有资料审核和新增流程图审核。原型中的固定图示用于审核界面设计，不代表真实视觉模型或生产数据已经验证；真实能力由 TAP AI 独立应用的测试和配置判定。

## 人工纠错增量

本轮在同一 `/prototype`、1280×720、2×、干净浏览器存储下对照[更正前](../assets/flowchart-image/2026-09-27-flowchart-correction-before.png)、[更正后](../assets/flowchart-image/2026-09-27-flowchart-correction-after.png)及[编辑状态](../assets/flowchart-image/2026-09-27-flowchart-correction-editor.png)。保留原图和原有审核检查项，新增起点、终点、方向与条件更正。保存增加审核修订并清空旧检查；关闭重开保留更正内容。截图人工对照通过，编辑控件与保存/取消均可见。该两节点原型继续仅证明交互，不代表 14 节点真实模型链路已经验收。

生产编辑器另以实际 `FlowchartEditor`、应用样式和主题加载上述真实调用的 14 节点/16 连线产物，截取[桌面节点](../assets/flowchart-image/2026-09-27-flowchart-production-editor-desktop-nodes.png)、[桌面连线](../assets/flowchart-image/2026-09-27-flowchart-production-editor-desktop-edges.png)、[移动节点](../assets/flowchart-image/2026-09-27-flowchart-production-editor-mobile-nodes.png)及[移动连线](../assets/flowchart-image/2026-09-27-flowchart-production-editor-mobile-edges.png)。桌面 1280×720、移动 390×844，均为 2×；无页面错误和横向溢出，控件可读。临时组件测试入口已删除；这些截图不证明真实 API 保存旅程。

浏览器导航实际检查通过：Agents、Skills、Library、Knowledge Graph、New chat、Test Management、Test Analytics、Low Code Automation；各主模块可返回 Tapper。完整 `apps/web` 90 项测试通过；跨模块与悬浮助手既有交互由对应组件回归覆盖。
