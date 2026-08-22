---
status: completed
date: 2026-08-22
---

# Document Information Architecture Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate TAP documentation into the approved six-directory information architecture without losing content or leaving broken repository links.

**Architecture:** Perform the change in three reviewable units: establish governance metadata, atomically migrate and split content with all link updates, then validate the final tree and close lifecycle states. Existing prose remains intact except for standalone heading levels, front matter, indexes, and relative links.

**Tech Stack:** UTF-8 Markdown, YAML front matter, Git, `rg`, shell, Python 3 standard library for read-only validation.

**Spec:** `docs/proposals/2026-08-22-rfc-002-document-information-architecture.md`

## Global Constraints

- Final `docs/` root contains only `index.md` plus `architecture/`, `proposals/`, `decisions/`, `plans/`, `reviews/`, and `reference/`.
- Every content filename uses its approved `YYYY-MM-DD-` prefix; only indexes and RFC/ADR templates are exempt.
- Do not add compatibility files, symlinks, duplicate bodies, a documentation site, CI, or dependencies.
- Preserve all text from the original 12 documents; splitting `decisions.md` may only promote standalone headings and add front matter.
- Update every repository Markdown link to its final path. Historical old paths in RFC-002's migration table remain literal text, not links.
- Modify files outside `docs/` only to repair `README.md` and `AGENTS.md` navigation and add the approved governance route.
- RFC states are `draft → in-review → accepted → implemented`; ADR states are `proposed → accepted → superseded`; Plan states are `planned → active → completed`.

## Final File Responsibilities

- `docs/architecture/`: the current baseline and Knowledge Chat design; `rag/` owns the four RAG designs.
- `docs/proposals/`: RFC-001, RFC-002, unresolved inputs, and the RFC template.
- `docs/decisions/`: one ADR per file, the historical superseded-options record, ADR template, and grouped index.
- `docs/plans/`: the active roadmap, this migration plan, and their index.
- `docs/reviews/`: the dated architecture review.
- `docs/reference/`: contracts, sources, and the normative documentation-governance rules.

---

### Task 1: Establish Governance and Lifecycle Records

**Files:**
- Modify: `docs/plans/2026-08-22-document-information-architecture-migration.md`
- Modify: `docs/proposals/2026-08-22-rfc-002-document-information-architecture.md`
- Create: `docs/reference/2026-08-22-document-governance.md`
- Create: `docs/proposals/rfc-template.md`
- Create: `docs/decisions/adr-template.md`
- Create: `docs/decisions/2026-08-22-adr-016-adopt-document-information-architecture.md`

**Interfaces:**
- Consumes: accepted RFC-002 and its naming, lifecycle, immutability, indexing, and discovery rules.
- Produces: the normative governance document, reusable RFC/ADR structures, and the accepted ADR linked to RFC-002.

- [x] **Step 1: Mark execution active**

Change this plan's `status` from `planned` to `active`. Keep RFC-002 `accepted` and change `related-adrs: []` to:

```yaml
related-adrs:
  - ADR-016
```

- [x] **Step 2: Create the normative governance document**

Write `docs/reference/2026-08-22-document-governance.md` with these exact sections and rules: directory responsibilities; dated lower-kebab filenames and date precedence; RFC, ADR, and Plan state machines; accepted-ADR immutability and bidirectional supersession; direct-child indexes; relative links; required link repair after moves; template usage; and the `AGENTS.md → governance → templates` discovery chain. State explicitly that indexes/templates have no date prefix and that status changes never move a file.

- [x] **Step 3: Create reusable templates**

The RFC template must contain front matter keys `id`, `status`, `date`, and `related-adrs`, followed by 摘要、背景、目标、非目标、方案、替代方案、风险与缓解、迁移或发布方式、验收标准、未决问题. Use `RFC-NNN`, `draft`, and `YYYY-MM-DD` as reusable markers.

The ADR template must contain `id`, `status`, `date`, `supersedes`, `superseded-by`, and `related-rfcs`, followed by 背景、决策、考虑过的方案、后果. Use `ADR-NNN`, `proposed`, and `YYYY-MM-DD` as reusable markers.

- [x] **Step 4: Record ADR-016**

Create ADR-016 with `status: accepted`, `date: 2026-08-22`, empty supersession lists, and this exact relation:

```yaml
related-rfcs:
  - RFC-002
```

Its decision is to adopt the six stable responsibility directories, dated content filenames, type-specific lifecycle metadata, per-directory indexes, and root-AGENTS governance routing. Record rejected alternatives: retaining the flat tree, lifecycle directories, and introducing a documentation site/CI in this change.

- [x] **Step 5: Verify and commit governance**

Run:

```sh
rg -n '^status:|^id:|^date:|^related-' docs/proposals docs/decisions docs/plans
git diff --check
git diff -- docs/proposals docs/decisions docs/plans docs/reference
```

Expected: RFC-002 is `accepted` and lists ADR-016; ADR-016 is `accepted` and lists RFC-002; the plan is `active`; no whitespace errors.

```sh
git add docs/proposals docs/decisions docs/plans docs/reference
git commit -m "docs: add documentation governance"
```

### Task 2: Atomically Migrate Content, Split Decisions, and Repair Links

**Files:**
- Move: the 11 non-decision documents listed below.
- Delete after split: `docs/decisions.md`
- Create: 15 dated ADR files, `docs/decisions/index.md`, `docs/decisions/2026-08-20-superseded-options.md`, and `docs/proposals/2026-08-20-open-questions.md`
- Create: `docs/index.md` and indexes under every category plus `docs/architecture/rag/index.md`
- Modify: all moved documents containing repository links, `README.md`, and `AGENTS.md`

**Interfaces:**
- Consumes: governance/templates from Task 1 and every original document body.
- Produces: the final navigable tree with no old-path files and no broken internal link or anchor.

- [x] **Step 1: Capture the pre-migration inventory**

Run and retain the output for comparison:

```sh
rg --files docs | sort
rg -n '\]\([^)]*\.md(?:#[^)]*)?\)' README.md AGENTS.md docs
rg -n '^#{1,6} ' docs/decisions.md
```

Expected: 12 original top-level documents, RFC-002, this plan, and the Task 1 governance files are visible; `decisions.md` contains ADR-001 through ADR-015.

- [x] **Step 2: Move the 11 intact documents with Git**

Create destination parents first:

```sh
mkdir -p docs/architecture/rag docs/reviews docs/reference
```

Then use exactly this mapping:

```text
docs/architecture.md                    → docs/architecture/2026-08-20-overview.md
docs/rag-phase-1.md                     → docs/architecture/rag/2026-08-21-foundation.md
docs/chunking-and-provenance.md         → docs/architecture/rag/2026-08-21-chunking-and-provenance.md
docs/ai-search-index-design.md          → docs/architecture/rag/2026-08-21-ai-search-index.md
docs/retrieval-tuning.md                → docs/architecture/rag/2026-08-21-retrieval-tuning.md
docs/knowledge-chat-ui.md               → docs/architecture/2026-08-21-knowledge-chat-ui.md
docs/codex-agent-runtime.md             → docs/proposals/2026-08-21-rfc-001-codex-agent-runtime.md
docs/roadmap.md                         → docs/plans/2026-08-20-roadmap.md
docs/architecture-review.md             → docs/reviews/2026-08-21-architecture-review.md
docs/contracts.md                       → docs/reference/2026-08-20-contracts.md
docs/source-notes.md                    → docs/reference/2026-08-20-source-notes.md
```

Use `git mv` for each source. Add RFC-001 front matter with `id: RFC-001`, `status: in-review`, `date: 2026-08-21`, and:

```yaml
related-adrs:
  - ADR-014
```

Add roadmap front matter with `status: active` and `date: 2026-08-20`. Do not otherwise rewrite their prose.

- [x] **Step 3: Split `decisions.md` without losing text**

Promote each original `### ADR-NNN` heading to `# ADR-NNN`; preserve every following paragraph through the next heading. Use dates 2026-08-20 for ADR-001–ADR-011 and 2026-08-21 for ADR-012–ADR-015. Set ADR-001–008, ADR-011–013, and ADR-015 to `accepted`; set ADR-009, ADR-010, and ADR-014 to `proposed`. All use empty supersession lists; only ADR-014 lists `RFC-001` under `related-rfcs`.

Use the exact target filenames from RFC-002's migration table. Move the old-options table to `2026-08-20-superseded-options.md` and the 14 numbered unresolved inputs to `docs/proposals/2026-08-20-open-questions.md`. Delete `docs/decisions.md` only after confirming all 15 IDs, the table, and all 14 inputs exist in their targets.

- [x] **Step 4: Repair every repository-local link**

Apply the exact final destinations from RFC-002. In particular:

```text
README document list          → all final dated paths
README next decision          → docs/proposals/2026-08-20-open-questions.md
architecture overview         → rag/*, Knowledge Chat, proposals/RFC-001, reference/contracts and source notes
RAG foundation                → sibling RAG files, ../Knowledge Chat, ../../proposals/RFC-001, ../../reference/contracts, ../overview
contracts                     → ../proposals/RFC-001, ../architecture/rag/chunking, ../architecture/Knowledge Chat
roadmap                       → ../architecture/rag/foundation and ../proposals/RFC-001
source notes                  → ../proposals/open-questions and ../proposals/RFC-001
ADR-014                       → ../proposals/RFC-001
Knowledge Chat               → ../reference/contracts
RFC-001                       → ../reference/contracts
```

Preserve every existing fragment, including `#agentruntime`, `#8-retrieval-contract`, `#9-knowledge-chat-contract`, and `#11-可靠性性能与容量`.

- [x] **Step 5: Create direct-child indexes**

Create `docs/index.md` linking the six category indexes. Create category indexes that list only direct children with one-line descriptions. `architecture/index.md` links overview, Knowledge Chat, and `rag/index.md`; the RAG index links its four designs. Proposals lists open questions, RFC-001 (`in-review`), RFC-002 (`accepted`), and the template. Plans lists roadmap (`active`) and this plan (`active`). Reviews and Reference list their dated files. Decisions preserves the original title and introduction, retains the original “已确认的方向” and “本次整理补充的工程基线” headings with ADR links, adds ADR-016 under “文档治理决策”, and links the superseded-options record and template.

- [x] **Step 6: Update the repository guide**

Rewrite only AGENTS' stale structure/naming guidance: point to `docs/index.md`, describe the six directories, require dated content names, and add a short “Documentation Governance” section requiring agents to read `docs/reference/2026-08-22-document-governance.md` and use the RFC/ADR templates before creating or materially changing those record types. Preserve all unrelated build, style, test, commit, PR, and security guidance.

- [x] **Step 7: Verify the migrated unit and commit**

Run:

```sh
find docs -mindepth 1 -maxdepth 1 -print | sort
rg -n '\]\((?:docs/)?(?:architecture|rag-phase-1|chunking-and-provenance|ai-search-index-design|retrieval-tuning|knowledge-chat-ui|codex-agent-runtime|decisions|roadmap|architecture-review|contracts|source-notes)\.md(?:#[^)]*)?\)' README.md AGENTS.md docs || true
git diff --check
git status --short
```

Expected: the `find` output is exactly the six directories plus `docs/index.md`; the old-link search is empty; whitespace validation passes. Review rename detection with `git diff --summary` and confirm all original decision content in the working-tree diff.

```sh
git add README.md AGENTS.md docs
git commit -m "docs: reorganize documentation by lifecycle"
```

### Task 3: Validate the Final Repository and Close Lifecycle States

**Files:**
- Modify: `docs/proposals/2026-08-22-rfc-002-document-information-architecture.md`
- Modify: `docs/plans/2026-08-22-document-information-architecture-migration.md`

**Interfaces:**
- Consumes: the complete migrated tree from Task 2.
- Produces: evidence that RFC-002 acceptance criteria pass and final `implemented`/`completed` states.

- [x] **Step 1: Validate inventory, names, IDs, states, links, and anchors**

Run the standard-library validator below from the repository root. It must exit 0 and print `documentation validation passed`:

```python
from pathlib import Path
import re
from urllib.parse import unquote

files = [Path("README.md"), Path("AGENTS.md"), *Path("docs").rglob("*.md")]
errors = []
ids = {}
allowed = {"RFC": {"draft", "in-review", "accepted", "implemented", "rejected"},
           "ADR": {"proposed", "accepted", "superseded"},
           "PLAN": {"planned", "active", "completed"}}

def body_without_fences(text):
    return re.sub(r"```.*?```", "", text, flags=re.S)

def anchors(text):
    found, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", body_without_fences(text), re.M):
        value = re.sub(r"[`*_~]", "", heading).lower().strip()
        value = re.sub(r"[^\w\-\u4e00-\u9fff ]", "", value)
        value = re.sub(r"\s+", "-", value)
        count = counts.get(value, 0)
        counts[value] = count + 1
        found.add(value if count == 0 else f"{value}-{count}")
    return found

for path in files:
    text = path.read_text(encoding="utf-8")
    for destination in re.findall(r"(?<!!)\[[^]]+\]\(([^ )]+)(?:\s+[^)]*)?\)", body_without_fences(text)):
        if destination.startswith(("http://", "https://", "mailto:")):
            continue
        target_text, _, fragment = destination.partition("#")
        target = path if not target_text else (path.parent / unquote(target_text)).resolve()
        if not target.exists():
            errors.append(f"{path}: missing {destination}")
        elif fragment and unquote(fragment) not in anchors(target.read_text(encoding="utf-8")):
            errors.append(f"{path}: missing anchor {destination}")

for kind, pattern, required in (
    ("RFC", "docs/proposals/*-rfc-*.md", {"id", "status", "date", "related-adrs"}),
    ("ADR", "docs/decisions/*-adr-*.md", {"id", "status", "date", "supersedes", "superseded-by", "related-rfcs"}),
    ("PLAN", "docs/plans/20*.md", {"status", "date"}),
):
    for path in map(Path, sorted(Path().glob(pattern))):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if not match:
            errors.append(f"{path}: missing front matter")
            continue
        fields = dict(re.findall(r"^([a-z-]+):\s*(.*?)\s*$", match.group(1), re.M))
        if missing := required - fields.keys():
            errors.append(f"{path}: missing {sorted(missing)}")
        if fields.get("status") not in allowed[kind]:
            errors.append(f"{path}: invalid status {fields.get('status')}")
        if record_id := fields.get("id"):
            if record_id in ids:
                errors.append(f"duplicate {record_id}: {ids[record_id]} and {path}")
            ids[record_id] = path

for path in Path("docs").rglob("*.md"):
    if path.name not in {"index.md", "rfc-template.md", "adr-template.md"} and not re.match(r"\d{4}-\d{2}-\d{2}-", path.name):
        errors.append(f"{path}: missing date prefix")

if errors:
    raise SystemExit("\n".join(errors))
print("documentation validation passed")
```

Execute it as a Python heredoc or paste it into an interactive `python3` session; do not add a validator file to the repository.

- [x] **Step 2: Run repository-level acceptance checks**

```sh
test "$(find docs -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" = 7
test "$(rg -l '^id: ADR-[0-9]{3}$' docs/decisions/20*.md | wc -l | tr -d ' ')" = 16
test "$(rg -l '^id: RFC-[0-9]{3}$' docs/proposals/20*.md | wc -l | tr -d ' ')" = 2
git diff --check
git status --short
```

Expected counts: seven root entries, 16 ADRs, two RFCs. Preview changed Markdown tables and the existing Mermaid blocks in a compatible renderer; only paths and metadata may differ in moved bodies.

- [x] **Step 3: Close the lifecycle records**

After every preceding command passes, change RFC-002 from `accepted` to `implemented` and this plan from `active` to `completed`. Rerun the validator and `git diff --check`.

- [x] **Step 4: Commit final status and verify clean state**

```sh
git add docs/proposals/2026-08-22-rfc-002-document-information-architecture.md docs/plans/2026-08-22-document-information-architecture-migration.md
git commit -m "docs: complete documentation migration"
git status --short
git log -3 --oneline
```

Expected: clean status and three narrowly scoped documentation commits ending with the lifecycle completion commit.
