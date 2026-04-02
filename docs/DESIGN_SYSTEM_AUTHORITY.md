# Design system authority (MINT vs Turbogrid)

This doc fixes scope: **what each name means upstream**, **how MT actually wires them**, **what the dataloader should mirror**, and **what is canonical in-repo**.

## How MT splits the stacks (they do not share CSS)

**1. Platform (customer dashboard) = MINT only**

- React (`ui-components/Button`, `Card`, `Badge`, `Page`, `IndexTable`, …).
- Tailwind; **`platform/tailwind.config.js`** is the token source.
- **`platform/Gemfile` has no `turbogrid` gem** — zero Turbogrid in Platform.
- This is what customers see.

**2. Flow (internal admin) = its own admin CSS + Turbogrid scoped narrowly**

- Flow’s **`admin.css`** pulls in **Flow-written** globals, reset, typography, buttons, badges, cards, tables, forms, layout (~order of **hundreds of LoC** total across imports — not MINT, not Turbogrid’s design system).
- **Turbogrid CSS is imported last**, then Flow’s **`admin/elements/turbogrid.css`** **overrides** Turbogrid **inside `.turbogrid` only**.
- **`<main>` in Flow’s layout has no `class="turbogrid"`**. The gem wraps **grid output** in `<div class="turbogrid">` — Turbogrid is a **table/grid rendering stylesheet**, not an app-wide shell.
- Flow’s **own** tokens live in globals (e.g. a small set like `--color-accent`, `--color-bg`) — **not** Turbogrid’s 100+ scoped variables for the whole app, and **not** MINT’s Tailwind scale.

**Key insight (upstream):** Flow does **not** use Turbogrid as a design system. It uses Turbogrid as a **grid/table engine** and contains its styling to **scoped markup**.

## What the dataloader should mirror

| Target | Role |
|--------|------|
| **`tokens.css` + `static/css/*` + `mt-patterns.css` + Jinja partials** | **Platform / MINT parity** — this is the real product design system for the dataloader (static port of dashboard patterns). |
| **`static/turbogrid/`** | **Optional Flow-style grid CSS** — load **only** if some subtree still needs Turbogrid’s grid/field/popover rules; apply **only** inside an explicit `<div class="turbogrid">`, same idea as Flow’s `render_grid` wrapper. |

**The anomaly today:** **`base.html` puts `class="turbogrid"` on `<main>`**, so Turbogrid reset, variables, and components apply to the **entire** app. That **does not** match Flow or Platform. It causes unnecessary **specificity fights** (e.g. `.btn.btn-*` in `style.css`) that Flow never needs because Turbogrid stays off `<main>`.

## In this repo (artifacts)

| Artifact | What it actually is |
|----------|---------------------|
| **`static/css/tokens.css`** | MINT-aligned CSS variables (curated). **`scripts/regen-tokens.js`** writes **`static/css/tokens.regen-preview.css`** for diffing against Mint; merge into `tokens.css` only after schema alignment (`docs/UI_GAPS_REMAINING.md`). |
| **`static/css/*.css`** (component layer) | Traced-from-React / app CSS. Shipped files include: `buttons`, `case-card`, `chip`, `drawer`, `filter-bar`, `forms`, `index-table`, `json-view`, `kv-table`, `layout`, `page-chrome`, `pagination`, `pill`, `status-indicator`, `tabs`, `toast`, `toggle-switch`, `tokens` — plus optional **`tokens.regen-preview.css`** (local regen; often gitignored). |
| **`static/mt-patterns.css`** | Card, modal, accordion, etc., from **Platform** sources; loaded alongside `static/css/*`. First-class MINT port. |
| **`templates/partials/*.html`** | Jinja mirroring MINT components (e.g. `index_table.html` ← `TableUI.tsx`). **Not** from the Turbogrid gem. |
| **`static/turbogrid/`** | **Vendored Turbogrid CSS only** (~781 lines). **`base.html`** currently links it **and** sets **`class="turbogrid"` on `<main>`** (~lines 12 and ~107) — the part to **fix** first. |

**Turbogrid is not a templating language here.** No “Turbogrid → Jinja codegen.”

## Decision

- **Design system authority:** **MINT** (tokens + `static/css/*` + `mt-patterns.css` + partials). Local `plan/` docs may add IA/density guidance when present.
- **Turbogrid:** **Grid/table stylesheet, scoped** — mirror Flow: **no** Turbogrid on `<main>`; wrap only what still needs Turbogrid rules, or **drop the link and folder** if nothing does (max LoC win).

## Summary

| Question | Answer |
|----------|--------|
| Does Platform use Turbogrid? | **No.** MINT + Tailwind only. |
| Does Flow use Turbogrid as the admin design system? | **No.** Admin chrome is Flow’s own CSS; Turbogrid is **scoped to grid output**. |
| What should the dataloader fix first? | **Remove `class="turbogrid"` from `<main>`**; treat Turbogrid like Flow — optional scoped wrapper or remove entirely. |
| Should we “codegen” Turbogrid? | **No.** Invest in MINT ports + tokens; Turbogrid CSS is vendored optional scope. |

See also: [`PORTING-KIT.md`](PORTING-KIT.md), [`.cursor/rules/mint-mt-ui.mdc`](../.cursor/rules/mint-mt-ui.mdc).

**Migration plan:** [`plan/mint_turbogrid_reduction_plan.md`](../plan/mint_turbogrid_reduction_plan.md) when tracked (unwrap `<main>` = Flow parity; optional delete ~781 LoC if no scoped grid needs Turbogrid).
