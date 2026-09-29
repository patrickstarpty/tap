---
status: active
date: 2026-09-27
---
# 知识审核工作台实施计划

## 目标与已确认设计

依据用户对 2026-09-27 差距分析的实施授权，保留自动切片、不可变来源与独立复核发布，补齐长文档的问题导航、位置标注和 Excel 结构化输入。保持现有产品壳与权限边界。原型展示与运行能力分别验证。

参考 Dify 的切片可见性和父子上下文、Unstructured 的按格式分区与工作表定位，以及 PDF.js 的原文阅读模式；不自建通用 Office 编辑器。审核对象是业务内容，技术 chunk 不作为用户必填对象。

## 实施顺序与验收

- [x] 长文档审核导航：清单按阻断/待核对/全部筛选，按位置搜索，显示已加载与总量，上一项/下一项；分页范围必须明确，不把已加载筛选当全库搜索。现有权限/并发版本校验保持。
- [x] 位置标注：从提取预览选取文字，将所选引文和位置加入核对意见；保存后可在历史中追踪；切换条目不得误带标记，不伪造原文精确坐标。保留人工排除和问题说明。
- [x] Excel：采用成熟库读取 XLSX，保留工作表与单元格范围，表头随行进入检索文本；不执行公式/宏/外链。公式无缓存、复杂合并、隐藏内容须有明确完整性处置；拒绝不支持的 XLS/宏格式。验证 ZIP/资源上限与来源锚点。
- [ ] PDF：复用原件授权读取提供页码定位阅读；扫描件处理明确 OCR 能力及配置边界，不能以提取文本冒充原件。OCR 若需外部服务，先完成可配置适配与契约测试，不擅自外发数据。
- [x] 同步完整原型的审核导航与标记交互，保留所有模块及跨模块旅程。
- [x] 更新契约、格式提示、开发/客户文档；窄测试后执行相关检查，采集前后桌面与移动截图，独立代码审查。

## 代码边界

解析：`apps/tap-ai-backend/src/tap/modules/knowledge/adapters/`、领域文档格式和相应解析测试。
审核：`apps/tap-ai-frontend/src/features/knowledge/components/KnowledgeReview.tsx` 及样式/测试。
原文接口：现有 knowledge HTTP routes/service/权限策略，遵循 `read_original`。
原型：`apps/web/src/widgets/tap/prototype/DocumentReview.tsx`。

## 证据与行业来源

- https://dify.ai/blog/introducing-parent-child-retrieval-for-enhanced-knowledge
- https://docs.unstructured.io/open-source/core-functionality/partitioning
- https://mozilla.github.io/pdf.js/getting_started/
- https://learn.microsoft.com/en-us/azure/search/search-how-to-semantic-chunking

## 执行记录

本计划采用 subagent-driven-development 技能，独立工作包分别实现并审查；主代理负责集成、原文与边界验证。现有 pytest/Allure 未提交改动保持，不回滚、不纳入本次功能证明。


2026-09-27 进度：审核导航、带定位引文保存、XLSX 解析及授权原件读取已实现；PDF 页码阅读与下载回退已实现，OCR 选型待用户回复，未外发资料。禁止把说明批注当作提取文本修改，PDF 框选、Excel 网格编辑和版本差异审核未交付。311 项后端知识/审核测试与实际禁网容器 XLSX 验证通过；独立审查发现并修复显示格式语义与未知 PDF 页码问题。完整原型 93 项回归通过。
