# Visual layer — tokens and conventions

This app ships **Jinja + static CSS** (no Tailwind in templates, no CSS bundler). The product today is a **single light theme**: primitives and semantics live in [`static/css/tokens.css`](../static/css/tokens.css). Component CSS should consume **semantic variables** so a second theme (for example a `[data-theme="dark"]` overlay) can be added later **without** rewriting every rule.

## Primitives vs semantics

| Layer | Role | Examples |
|-------|------|----------|
| **Primitives** | Fixed palette scales; rarely referenced directly in new UI | `--gray-*`, `--blue-*`, `--green-*` |
| **Semantics** | What users perceive (surface, text role, border strength) | `--background-default`, `--text-default`, `--text-muted`, `--border-default` |

**Rule:** For text, backgrounds, borders, and focus rings on app chrome, **use semantic variables**. Use primitives only for charts, one-off brand locks, or when no semantic exists yet—then add a semantic in `tokens.css` instead of spreading hex.

**Exception policy:** If a literal hex/rgb is unavoidable, add `/* literal: <reason> */` on the same or previous line and note it in PR review.

## Cascade order (current)

1. [`tokens.css`](../static/css/tokens.css) — `:root` primitives + semantic defaults  
2. Component CSS under `static/css/*.css` — layout and components consume semantics  
3. [`static/style.css`](../static/style.css) — app-specific overrides last  

A future dark (or high-contrast) pass would typically insert **one** additional stylesheet after `tokens.css` that only reassigns semantic custom properties; it is **not** shipped in the app today.

## Literal inventory (maintenance)

Approximate **`#rrggbb` / `rgb(`** counts under `static/css/` (excluding generated [`tokens.regen-preview.css`](../static/css/tokens.regen-preview.css))—use `rg '#[0-9a-fA-F]{3,8}' static/css` to refresh:

| File | Notes |
|------|--------|
| `tokens.css` | Expected: full primitive scale + semantics |
| `page-chrome.css` | Breadcrumbs, badges, cards — semantic vars only (CI-guarded) |
| `buttons.css`, `forms.css` | Higher literal load; migrate incrementally |
| `layout.css` | Layout-only; typically no raw colors |

## Third-party widgets

| Widget | Behavior |
|--------|----------|
| **Monaco** | [`static/editor.js`](../static/editor.js) uses the existing `--bg` luminance heuristic (`vs` vs `vs-dark`). |
| **Mermaid** | [`templates/partials/mermaid_scripts.html`](../templates/partials/mermaid_scripts.html) uses `theme: 'neutral'` (pinned CDN build in the partial). |
| **TurboGrid** | Vendor stylesheet under `/static/turbogrid/`; prefer app classes that use **semantic** tokens. Avoid new grid-specific hex in `static/css/` when a semantic exists. |
| **`mt-patterns.css`** | Uses `--mt-*` aliases from `tokens.css`; keep aliases on semantics so future theme sheets stay small. |

## CI guard

[`scripts/check_css_literals.py`](../scripts/check_css_literals.py) fails if **new** bare hex literals appear in strict-listed chrome files (currently `page-chrome.css`). Extend `STRICT_CSS_FILES` as more modules go semantic-only.

## Related

- Run state / BFF: [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) (orthogonal).
