# 文档精简与 V1 总纲 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `docs/` 收敛为 `superpowers/{specs,plans}` + `decisions/` + `guides/` + `archive/`，并写出现状架构页与 V1 总纲。

**Architecture:** 纯文档变更。`git mv` 整目录平移到 `docs/archive/`；一次性脚本按"旧路径解析 → 迁移映射 → 新相对路径"重写受影响链接；一次性检查脚本比较迁移前后断链数。

**Tech Stack:** git、Python 3.13 标准库（一次性脚本放 `/tmp`，不提交）。

**Spec:** `docs/superpowers/specs/2026-09-29-docs-slimming-and-v1-roadmap-design.md`

## Global Constraints

- 只改文档与文档链接；不改产品代码、脚本、部署配置；不删除任何文档；`docs/assets/` 不动。
- 迁移一律 `git mv`。
- 不改写归档文档正文（链接路径重写除外），不修复归档文档内原有断链。
- 一次性脚本不提交、不加依赖、不进 CI。
- 检查范围：`docs/**/*.md`、`README.md`、`AGENTS.md`、`apps/tap-ai-frontend/PRODUCT.md`；排除 `node_modules/`、`.worktrees/`；锚点不校验。
- 验收：迁移后断链数 ≤ 迁移前基线。
- 规范术语：`TAP AI`（全大写）、`Tapper`、`Test IR`、`Knowledge Chat`、`Azure AI Search`。
- Markdown：UTF-8、ATX 标题、相对链接、带语言标记的代码块。
- 提交信息：小写祈使句 Conventional Commit，结尾 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 分支：`claude/v1-docs-slimming`。工作区里 `apps/tap-ai-backend/tests/**` 的 4 个未提交改动不得暂存或修改（`git add` 只用显式路径或 `docs/`，不用 `-A`）。

## Review Focus

- 代码块内的"链接"（```` ``` ```` 围栏中的 `[x](y)`）：不应计入、也不应被重写 —— Task 1 的检查脚本和 Task 2 的重写脚本都跳过围栏。
- 带锚点的链接 `a.md#节`：重写时保留 `#…` 原样，只改路径部分 —— Task 2 Step 4 断言。
- Markdown 中的 HTML `src="…"` / `href="…"` 相对路径（如 cocoon 图）：与 `[x](y)` 同等检查与重写 —— Task 1 脚本覆盖。
- 未移动的根文件（`README.md`、`AGENTS.md`、`PRODUCT.md`）指向已移动文档：同样被重写 —— Task 2 Step 4 断言 `PRODUCT.md`。
- 已移动文档指向未移动目标（`assets/`、`decisions/`、仓库根文件）：路径深度变化后仍可解析 —— Task 2 Step 4 断言一处 `../../assets/`。

---

### Task 1: 断链基线

**Files:**
- Create（不提交）：`/tmp/check_links.py`

**Interfaces:**
- Produces: `python3 /tmp/check_links.py` → 逐行打印 `源文件: 链接目标`，最后一行 `BROKEN <n>`；`/tmp/links-baseline.txt` 保存基线输出。

- [ ] **Step 1: 写检查脚本**

规则：遍历范围内 `.md`；跳过 ```` ``` ```` 围栏内的行；提取 `](target)` 与 `src="target"`/`href="target"`；忽略以 `http:`、`https:`、`mailto:`、`#` 开头的目标及 `<…>` 包裹符号；去掉 `#…` 与 `?…` 后 URL 解码，相对源文件目录解析；目标不存在即记为断链。

```python
import pathlib, re, sys, urllib.parse
ROOT = pathlib.Path.cwd()
FILES = [p for p in ROOT.glob("docs/**/*.md")] + [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "apps/tap-ai-frontend/PRODUCT.md"]
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|(?:src|href)=\"([^\"]+)\"")
def targets(text):
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            for m in LINK.finditer(line):
                yield (m.group(1) or m.group(2)).strip("<>")
broken = 0
for f in sorted(FILES):
    for t in targets(f.read_text(encoding="utf-8")):
        if re.match(r"^(https?:|mailto:|#)", t):
            continue
        path = urllib.parse.unquote(t.split("#")[0].split("?")[0])
        if path and not (f.parent / path).exists():
            broken += 1
            print(f"{f.relative_to(ROOT)}: {t}")
print(f"BROKEN {broken}")
```

- [ ] **Step 2: 记录基线**

Run: `python3 /tmp/check_links.py | tee /tmp/links-baseline.txt | tail -1`
Expected: 打印 `BROKEN <n>`（n 任意，记下该数）。无提交。

---

### Task 2: 迁移与链接重写

**Files:**
- Move: `docs/{proposals,plans,reviews,architecture}/` → `docs/archive/{同名}/`
- Move: `docs/reference/` 中 8 个文件 → `docs/guides/`；其余 → `docs/archive/reference/`
- Create（不提交）：`/tmp/rewrite_links.py`

**Interfaces:**
- Consumes: Task 1 的 `/tmp/check_links.py`、`/tmp/links-baseline.txt`。
- Produces: 新目录结构；`docs/guides/2026-09-22-product-prototype-baseline.md`（Task 4 链接用）；`docs/archive/reviews/2026-09-29-architecture-review-and-v1-scope.md`（Task 5、6 链接用）。

- [ ] **Step 1: 移动**

```bash
mkdir -p docs/archive docs/guides
for d in proposals plans reviews architecture; do git mv docs/$d docs/archive/$d; done
for f in 2026-09-22-product-prototype-baseline 2026-09-27-frontend-developer-onboarding \
  2026-09-27-backend-developer-onboarding 2026-09-13-tapper-developer-guide \
  2026-09-05-tap-light-design 2026-09-06-file-type-icons \
  2026-09-04-customer-prototype-demo-guide 2026-09-27-knowledge-review-workbench; do
  git mv docs/reference/$f.md docs/guides/$f.md; done
git mv docs/reference docs/archive/reference
```

Expected: `git status --short docs | grep -c '^R'` > 0，且 `ls docs` 为 `archive assets decisions guides index.md superpowers`。

- [ ] **Step 2: 写重写脚本**

算法：从 `git diff --cached --name-status -M HEAD -- docs` 取 `R` 行，得到 `old→new` 映射 `M` 及反向映射 `R`。对 Task 1 范围内每个 `.md`（围栏外），令 `old_src = R.get(src, src)`；对每个相对目标 `t = path + suffix`（suffix 为 `#…`/`?…`）：`old_target = normpath(dirname(old_src)/unquote(path))`，`new_target = M.get(old_target, old_target)`；若 `new_target` 存在且 `relpath(new_target, dirname(src))` 与原 `path` 不同，则替换为新相对路径 + suffix（使用 POSIX 分隔符）。原本就不存在的旧目标不动。只替换匹配到的链接片段，不动其余文本。

- [ ] **Step 3: 运行重写并复查断链**

Run: `python3 /tmp/rewrite_links.py && python3 /tmp/check_links.py | tail -1`
Expected: `BROKEN <m>`，m ≤ 基线 n。若 m > n：`diff <(sort /tmp/links-baseline.txt) <(python3 /tmp/check_links.py | sort)` 定位新增断链，修正脚本后重跑（先 `git checkout -- docs README.md AGENTS.md apps/tap-ai-frontend/PRODUCT.md` 撤销文本改动，保留暂存的移动）。

- [ ] **Step 4: 抽查 Review Focus 项**

Run:
```bash
grep -n "docs/archive/" apps/tap-ai-frontend/PRODUCT.md | wc -l      # 期望 4
grep -rn "](\.\./\.\./assets/" docs/archive | head -1                # 期望至少 1 行
grep -rnE "\]\([^)]*archive[^)]*#" docs README.md | head -1           # 带锚点链接保留 #
git diff --check
```

- [ ] **Step 5: 提交**

```bash
git add docs README.md AGENTS.md apps/tap-ai-frontend/PRODUCT.md
git commit -m "docs: archive legacy docs and move guides"
```

---

### Task 3: 索引

**Files:**
- Modify（重写）：`docs/index.md`
- Create：`docs/archive/index.md`
- Modify：`docs/decisions/index.md`（仅在顶部标题下加一行）

- [ ] **Step 1: 写 `docs/index.md`**

内容限于：一句话说明；链接 `architecture.md`、`superpowers/plans/2026-09-29-v1-roadmap.md`、`superpowers/specs/`、`superpowers/plans/`、`decisions/index.md`、`guides/` 下 8 个文件（每个一行标题链接）、`archive/index.md`。约定一节三行：spec/plan 位置与命名 `YYYY-MM-DD-<topic>[-design].md`；ADR 仅用于跨模块决策；`archive/` 只读。

- [ ] **Step 2: 写 `docs/archive/index.md`**

首段：2026-09-29 起冻结，不再维护状态与链接，来源评审链接 `reviews/2026-09-29-architecture-review-and-v1-scope.md`。之后按子目录链接到各原 `index.md`（`architecture/index.md`、`proposals/index.md`、`plans/index.md`、`reviews/index.md`、`reference/index.md`）。

- [ ] **Step 3: `docs/decisions/index.md` 标题下插入**

`> 2026-09-29 起不再维护 ADR 生命周期状态；新 ADR 仅用于跨模块决策，见 [文档索引](../index.md)。`

- [ ] **Step 4: 验证并提交**

Run: `python3 /tmp/check_links.py | tail -1` → `BROKEN` ≤ n（Task 3 待建的 `architecture.md`、`v1-roadmap.md` 两个链接暂时断开，允许 m ≤ n+2，Task 6 结束时回到 ≤ n）。

```bash
git add docs/index.md docs/archive/index.md docs/decisions/index.md
git commit -m "docs: add slim docs and archive indexes"
```

---

### Task 4: AGENTS.md 与 README.md

**Files:**
- Modify：`AGENTS.md`（"Project Structure"、"Documentation Governance"、"Product Prototype Baseline" 中的链接）
- Modify：`README.md`

- [ ] **Step 1: 核对现状事实**

Run:
```bash
grep -nE "^  [a-z0-9-]+:$" compose.yaml
grep -rlnE "azurite|BlobService|minio|S3" apps/tap-ai-backend/src/tap | head
grep -rn "StateGraph\|add_node" apps/tap-ai-backend/src/tap | head
```
记录：TAP AI 实际使用的对象存储（Azurite 与/或 `tap-minio`）、LangGraph 图的节点。后续文字只写这些事实。

- [ ] **Step 2: 改 `AGENTS.md`**

- Project Structure：文档句改为 "Place specs in `docs/superpowers/specs/` and plans in `docs/superpowers/plans/`; guides in `docs/guides/`; cross-module decisions in `docs/decisions/`; `docs/archive/` is read-only. Use `YYYY-MM-DD-<topic>[-design].md` filenames." 本地服务清单改为 Step 1 核对到的事实。
- Documentation Governance：替换为三行——spec/plan 走 superpowers brainstorming/writing-plans 流程并按上面路径存放；ADR 仅用于跨模块决策、单个 `status` 字段；不再维护 RFC/Plan 生命周期状态。
- Product Prototype Baseline：链接改为 `docs/guides/2026-09-22-product-prototype-baseline.md`（其余规则原文不动）。
- Coding Style 中 "Architecture changes must synchronize applicable README, baseline, contracts, decisions, roadmap and source notes…" 改为 "Architecture changes must update `docs/architecture.md` and, when scope changes, the V1 roadmap."

- [ ] **Step 3: 改 `README.md`**

保留：顶部最新原型入口段落与截图行、启动/命令相关章节。删除：Task 14 Gate/`PENDING` 段、ADR-029 目标叙述段、RFC-011 主动 Agent 与交付范围两段、历史文档链接清单（第 145–162 行一带）。在原型入口之后加"文档"小节：链接 `docs/architecture.md`、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`、`docs/index.md`、`docs/archive/index.md`。删除段落后若有截图仍引用 `docs/assets/…`，保持原样。

- [ ] **Step 4: 验证并提交**

Run: `python3 /tmp/check_links.py | tail -1`（允许同 Task 3 的 +2）；`grep -n "document-governance\|docs/reference/\|docs/proposals/" AGENTS.md README.md` → 无输出；`git diff --check`。

```bash
git add AGENTS.md README.md
git commit -m "docs: simplify docs governance in agents and readme"
```

---

### Task 5: 现状架构页

**Files:**
- Create：`docs/architecture.md`

- [ ] **Step 1: 收集事实**

Run（只读）：`ls apps/tap-ai-backend/src/tap/modules/*/`、`ls apps/tap-ai-backend/src/tap/interfaces`、`grep -nE "^  [a-z0-9-]+:$" compose.yaml`、`grep -n "entry\|__main__\|def main" -r apps/tap-ai-backend/src/tap/interfaces | head`、`cat apps/tap-ai-backend/tests/architecture*/* 2>/dev/null | head -50`。

- [ ] **Step 2: 写 `docs/architecture.md`（≤ 2 页）**

章节固定为：
1. 应用边界：`apps/tap-ai-backend`、`apps/tap-ai-frontend`、`apps/backend`、`apps/web`，各一行；`/prototype` 为设计基准。
2. 后端分层：`modules/<name>/{domain,application,ports,adapters}`，列出 7 个模块各一行职责；进程入口 API / Relay / worker。
3. 基础设施：`compose.yaml` 中的服务，各一行用途；MySQL 为唯一权威状态，Milvus/Redis 为可重建投影或唤醒信号，模型统一经 LiteLLM。
4. 主要数据流：摄取、问答、图谱，各 3–5 步编号列表（可选一个 Mermaid `flowchart`）。
5. 与 V1 目标的已知差距：逐条链接 V1 总纲对应能力（数据来源为评审中的"当前状态/主要缺口"列）。

只写 Step 1 可验证的事实；目标态只出现在第 5 节。

- [ ] **Step 3: 提交**

```bash
git add docs/architecture.md
git commit -m "docs: add current state architecture overview"
```

---

### Task 6: V1 总纲

**Files:**
- Create：`docs/superpowers/plans/2026-09-29-v1-roadmap.md`

- [ ] **Step 1: 写总纲**

结构：
- 标题 + 一句目标 + 来源（评审链接 `../../archive/reviews/2026-09-29-architecture-review-and-v1-scope.md`）。
- "状态约定"：工程完成 / 业务验证 两个勾选框的定义（spec 原文）。
- "能力与验收"：5 个 `###` 小节，每节两个 `- [ ]` 列表项，文本逐字取自 spec 验收表对应单元格。
- "子项目"：0–6 编号列表（spec 原文），每项后附 `spec:` / `plan:` 占位行写"未开始"，子项目 0 填本 spec 与本 plan 的链接并标"进行中"。
- "不在 V1"：spec 原文 5 条。

- [ ] **Step 2: 最终验证**

Run:
```bash
python3 /tmp/check_links.py | tail -1     # BROKEN ≤ 基线 n
git diff --check 6847d54
git status --short | grep -v '^ M apps/tap-ai-backend/tests/' | grep -v '^?? node_modules'   # 期望仅剩本任务文件
```

- [ ] **Step 3: 提交**

```bash
git add docs/superpowers/plans/2026-09-29-v1-roadmap.md
git commit -m "docs: add v1 roadmap"
```

- [ ] **Step 4: 人工预览**

在编辑器预览 `README.md`、`AGENTS.md`、`docs/index.md`、`docs/architecture.md`、V1 总纲；Mermaid 若有需确认可渲染。
