# 文件类型图标与示例文件

本规范记录 TAP 原型当前使用的文件图标、格式归类和扩展入口。

## 图标来源

图标来自项目已安装的 `@ant-design/icons`（当前依赖版本为 6.3.2），统一使用 Filled 实心风格。图标本身由 Ant Design 提供；按文件类别选择颜色是 TAP 的设计约定，不是 Codex 或 Manus 的图标规范。

所有展示位置调用 [FileTypeIcon](../../apps/web/src/widgets/tap/prototype/FileTypeIcon.tsx)，包括 Library 列表、卡片、Knowledge sources 面板、输入框已选来源和来源选择器。不要在各页面自行添加扩展名判断或直接挑选文件图标。

## 格式与图标映射

扩展名注册在 [fileTypes.ts](../../apps/web/src/widgets/tap/prototype/fileTypes.ts)。匹配忽略大小写，接受扩展名、带点扩展名和完整文件名。

| 文件类别   | 扩展名                   | Ant Design 图标    | 颜色             |
| ---------- | ------------------------ | ------------------ | ---------------- |
| PDF        | pdf                      | FilePdfFilled      | 红色 `#b64a43`   |
| 文档       | doc、docx、odt、rtf      | FileWordFilled     | 蓝色 `#356cba`   |
| 表格       | xls、xlsx、ods、csv、tsv | FileExcelFilled    | 绿色 `#23764b`   |
| 演示文稿   | ppt、pptx、odp           | FilePptFilled      | 橙色 `#ba542c`   |
| Markdown   | md、markdown             | FileMarkdownFilled | 深绿色 `#344941` |
| 纯文本     | txt、log                 | FileTextFilled     | 灰色 `#626966`   |
| 结构化文本 | json、yaml、yml、xml     | CodeFilled         | 紫色 `#735599`   |
| 网页       | html、htm                | Html5Filled        | 蓝绿色 `#317a86` |
| 未知格式   | 其他扩展名               | FileFilled         | 灰色 `#626966`   |

颜色集中在 [TapProductPrototype.css](../../apps/web/src/widgets/tap/TapProductPrototype.css) 的 `.tap-file-type[data-family]` 规则中。布局保持 32 × 36 px 容器、29 px 图标；紧凑位置通过容器样式调整尺寸。文件名保留扩展名，不能只靠颜色区分类型。

## 后续如何添加

1. 同类别的新扩展名：只追加到 `FILE_TYPE_EXTENSIONS` 对应数组，不新增颜色或页面判断。
2. 新类别：在注册表增加类别，在 `FileTypeIcon.tsx` 的 `FAMILY_ICONS` 中选择同套 Filled 图标，再增加一条类别颜色规则。未知类别保留通用文件兜底。
3. 新示例：把有效原文件放到 [public/prototype-files](../../apps/web/public/prototype-files/)，在 [sampleFiles.ts](../../apps/web/src/widgets/tap/prototype/sampleFiles.ts) 注册唯一 ID、文件名、类型、描述、下载路径和预览内容。
4. 图片预览使用 `preview.imageUrl`；文本使用 `preview.text`。Markdown 才按 Markdown 渲染，其他文本按原文显示。HTML 示例使用预先生成的静态截图，不直接执行来源文件中的脚本。
5. 文件转换得到的 DOC、ODT、RTF、XLS、ODS、PPT、ODP 示例共用同内容的代表性预览图；预览不是在线 Office 编辑器。下载链接提供对应格式的实际文件。
6. 运行文件类别与 Library 测试、前端构建，检查预览图片加载和下载文件完整性。

```sh
corepack pnpm --dir apps/web test run src/widgets/tap/prototype/fileTypes.test.ts src/widgets/tap/prototype/LibraryWorkspace.test.tsx
corepack pnpm --dir apps/web run build
git diff --check
```

## 文件点击行为

文件名和卡片内容为静态展示，点击不打开预览、不移动焦点、不滚动页面。原文件通过每项独立的下载按钮获取；没有下载地址的来源不显示下载按钮。卡片保留内容缩略图，不提供侧边文件预览。

## 原型范围

Library 的“加载示例文件”提供 18 种格式的演示样本，加载状态仅保留在当前页面会话中。例子均为虚构测试资料，并可用于原型的搜索、筛选、卡片缩略图和来源选择。

图标识别、示例预览与后端解析是独立能力。添加图标或示例不会扩展上传接口；当前真实知识入库仍只支持可提取文本的 PDF、DOCX、MD、TXT，不包含 OCR。本次没有改动后端格式白名单。
