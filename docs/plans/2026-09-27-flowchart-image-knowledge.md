---
status: active
date: 2026-09-27
---

# 流程图图片知识实施计划

**Goal:** 上传 PNG/JPEG 流程图，经视觉解析、人工复核和发布后，Tapper 能基于图片中的节点、方向和条件开展有引用的多轮对话。

**Architecture:** 沿用 Source/Document Revision、隔离解析、Review/Publication、Milvus 文字投影、Conversation/Evidence；增加视觉模型请求。图片语义由模型提出、服务端验证、人确认，确认后的节点与连线进入现有文字向量空间。

**Tech Stack:** Python 3.13/FastAPI、ModelGateway/LiteLLM、MySQL、对象存储、Milvus、React/TypeScript/Vite。

**Spec:** [流程图图片知识设计](../reference/2026-09-27-flowchart-image-knowledge-design.md)、[RFC-011 AI 与知识专题](../reference/2026-09-22-rfc-011-ai-knowledge-design.md)。

## 全局约束

- 首批只接收单张 PNG/JPEG，原件最多 25 MiB；不宣称扫描 PDF、Office 图片和一般图片已经支持。
- 图像视觉模型沿用项目授权及 ModelGateway 审计；解析容器不携带模型凭证。
- 模型生成的关系在人工确认前不得进入正式回答；不确定连接明确待确认。
- 保留原件、位置、模型版本、Revision、复核/发布清单及撤回语义；本次不建立图像向量索引。
- 先写失败测试，再做最小实现。使用合成脱敏样本验证链路；真实模型和真实流程图准确率单独评估。

## Task 1：有界图片输入与隔离解码

**Files:** `apps/tap-ai-backend/src/tap/modules/knowledge/domain/documents.py`、`adapters/document_parsers.py`、`interfaces/http/routes/knowledge_documents.py`、`tests/unit/knowledge/test_document_parsers.py`、`tests/contract/test_knowledge_http.py`。

- [ ] 增加 PNG/JPEG 的扩展名、MIME、签名和像素/帧数验证测试；逐个运行，确认因格式尚未受支持而失败。
- [ ] 实现有界隔离解码和图片 Inventory；损坏、伪装或超限图片使用闭合错误码。复跑单元及上传契约测试。
- [ ] 验证重复上传、重试和现有四种文本格式不回退。

## Task 2：受控视觉模型和流程图结构

**Files:** `apps/tap-ai-backend/src/tap/modules/ai/domain/models.py`、`adapters/litellm.py`、`modules/knowledge/domain/flowcharts.py`、`modules/knowledge/adapters/flowchart_vision.py`、对应 gateway/flowchart 测试。

- [ ] 测试图片请求的项目、模型路由、图片大小、MIME、摘要、幂等和脱敏元数据边界，并确认视觉请求缺失时失败。
- [ ] 测试固定 JSON 的节点、连线端点、方向、条件、泳道和区域坐标校验；交叉线与不确定关系只能产生待确认项。
- [ ] 扩展 ModelGateway 视觉请求，接入获准视觉别名；将结构输出验证成不可变、带 Revision 与原件摘要的值。跑 gateway 契约和结构单测。

## Task 3：可恢复解析与人工复核

**Files:** `apps/tap-ai-backend/src/tap/modules/knowledge/application/ingestion.py`、`application/review.py`、`ports/documents.py`、相关持久化/迁移与 Review Web 文件、对应单元/集成测试。

- [ ] 测试视觉调用失败/重试、进程重启、重复提交与租约丢失均不发布半成品。
- [ ] 测试每个节点、条件和连线可指向原图区域；未确认连线阻断严格发布，排除范围后仅发布确认事实。
- [ ] 接入视觉步骤、结构化产物、可更正审核与发布检查。以同一 Revision 生成检索文本块并走现有文字 Embedding。

## 后续可选：独立图像向量索引与检索

当需要以图搜图而不仅是以文字询问流程含义时，再单独实施这一扩展；不计入本次语义文字向量问答验收。

**Files:** `apps/tap-ai-backend/src/tap/modules/knowledge/ports/search.py`、`adapters/milvus/`、`application/retrieve.py`、模型配置及对应索引/检索测试。

- [ ] 测试图像模型 2048 维输出与文字空间隔离，索引读回绑定 Source/Document/Revision/区域和模型版本。
- [ ] 测试跨项目、未发布、撤回、删除的结果均不能用于回答；检索故障显式返回能力缺失。
- [ ] 实现独立图像投影、发布切换和文字查询的跨模态检索融合；完成重建与删除恢复测试。

## Task 5：产品入口与完整问答验收

**Files:** `apps/tap-ai-frontend/src/features/knowledge/`、`apps/web/src/widgets/tap/`（仅需要的原型增量）、生成契约、README 与相关测试。

- [ ] 测试上传、原图与结构并排核对、纠错、复核/发布和来源选择；实现对应界面及文案。
- [ ] 测试 Tapper 对方向、条件、回路的连续追问与图片区域引用，以及不确定关系的保守回答。
- [ ] 在相同 1280×720、2× fixture 下比较 `/prototype` 前后截图并回归模块及跨模块导航；运行相关检查、完整测试和 `git diff --check`。
- [ ] 记录合成链路与真实模型质量结论；若无真实样本/模型，把准确率与真实业务 Gate 标为待验证。

## UAT 阻断修复（2026-09-27）

承接[业务语义 UAT](../reviews/2026-09-27-flowchart-business-meaning-uat.md)，在 `feat/trusted-knowledge-insights` 增量实施：

- [x] 视觉契约：以失败回归验证实际图片请求携带完整 Schema、发送图片尺寸与像素坐标约定；修复 Gateway 和视觉适配器后复跑真实合成样本，分别记录结构可接收性和语义准确性。
- [x] 人工纠错：节点与连接可编辑；更改重新生成可审核事实、坐标及摘要，旧审核结果不能授权新事实，已发布修订不可原地改写。验证无效端点、过期版本和未授权请求被拒绝。
- [x] 问答：已发布有向连接进入有界路径遍历，分支和回路保留条件及引用；不完整路径显式说明，不将单图推断成高风险定义、批准权限或时限。关联制度仍受已选来源、发布与撤回权限限制。
- [ ] 验证：窄范围测试先红后绿，运行受影响后端/前端及契约检查；真实模型复验与真实上传、审核、发布、追问、区域引用的完整 UAT 分开记录，未执行部分继续标记未验收。
