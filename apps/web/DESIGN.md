# TAP Design System

<!-- impeccable:design-schema 1 -->

Updated: 2026-09-05. Applies to the TAP product shell, Tapper, Library/Graph, Agent/Skills, Test Management, Automation. Product truth: [PRODUCT.md](PRODUCT.md). Approved visual brief: [TAP light design](../../docs/reference/2026-09-05-tap-fwd-light-design.md).

## Direction

A light, quiet working interface inspired by FWD's warm orange identity and the clarity of Codex / Manus. TAP is the platform; Tapper is its intelligent workspace. Preserve the existing ink SVG mark/wordmark. Technology is expressed through precise typography, graph relationships, code, evidence, and useful interaction states.

## Tokens

Authoritative CSS custom properties live at `src/app/styles.css` `:root`, including portal surfaces. Ant Design equivalents live in `src/app/theme.ts`.

| Token               | Value     | Use                                       |
| ------------------- | --------- | ----------------------------------------- |
| `--tap-canvas`      | `#faf9f6` | Warm page/sidebar ground                  |
| `--tap-surface`     | `#ffffff` | Chat, content, forms, dialogs             |
| `--tap-soft`        | `#f3f2ee` | Rail, secondary panels, code              |
| `--tap-ink`         | `#232b2b` | Primary text                              |
| `--tap-muted`       | `#626b68` | Secondary text                            |
| `--tap-line`        | `#e4e6e1` | Structure                                 |
| `--tap-line-strong` | `#c4cbc5` | Input boundary                            |
| `--tap-brand`       | `#e87722` | Main action background, TAP mark          |
| `--tap-accent`      | `#a9470b` | Links, selection, checked controls, focus |
| `--tap-accent-dark` | `#893807` | Hover / selected text                     |
| `--tap-accent-soft` | `#fff1e5` | Context chips / selected rows             |
| `--tap-accent-line` | `#efc7a7` | Citation / selection boundary             |

Orange buttons carry dark ink, including pressed state. Graph community hues remain semantic data colors. Success and error keep distinct green/red plus labels. The Codex minimap preserves its existing neutral proximity scale and geometry.

## Type and spacing

Use the system sans stack with PingFang SC / Microsoft YaHei fallbacks. Body 14px, supporting labels 12–13px; module headings 26–32px. Chat greeting 30–44px desktop, 28–32px narrow. Code and identifiers use monospace; tables use tabular numerals. No serif display headings. Retain compact existing data labels where required by dense editors.

Control radius 8–10px; content and floating surfaces 12–16px. Use neutral surfaces and fine rules for structure. Keep shadows to composer and floating overlays. Chat welcome has generous surrounding space; workbenches preserve their dense editing hierarchy.

## Components and responsive behavior

- Product rail 68px desktop / 64px mobile; Tapper sidebar 236px, 220px below 1100px.
- Knowledge sources are an optional right panel on wide screens; at 1100px and below they start closed and use a drawer with inert background, Escape dismissal and restored focus.
- Tapper sidebar uses its existing drawer at 640px and below. Never compress the composer between two persistent sidebars at tablet widths.
- Conversation remains the main surface. Source selection, catalog controls, model menus, asset links and Run controls share the same accent language.
- Graph, BDD, generated code and logs use light surfaces. No ornamental grid, glow or gradients.

## States and motion

Visible keyboard focus uses the darker orange token. Loading, empty, error, disabled and simulated states remain explicit. Existing panel movement uses approximately 200ms exponential ease-out; reduced-motion disables it and minimap scrolling becomes instant. Hover and selection use warm tint rather than large color blocks.

## Verification boundary

The 40 customer screenshots and desktop/mobile reduced-motion checks cover the prototype. The previously approved TAP product prototype is the sole visual baseline. The old standalone Knowledge screen is excluded; its temporary inspection entry was removed. Visual changes never imply RFC-009 platform capabilities are complete.
