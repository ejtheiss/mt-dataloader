# Visual layer — tokens, themes, and conventions

This app ships **Jinja + static CSS** (no Tailwind in templates, no CSS bundler). Theming is **CSS custom properties**: primitives and semantics live in [`static/css/tokens.css`](../static/css/tokens.css); dark mode **re-maps semantics** in [`static/css/theme-dark.css`](../static/css/theme-dark.css) when `document.documentElement` has `data-theme="dark"`.

## Primitives vs semantics

| Layer | Role | Examples |
|-------|------|----------|
| **Primitives** | Fixed palette scales; rarely referenced directly in new UI | `--gray-*`, `--blue-*`, `--green-*` |
| **Semantics** | What users perceive (surface, text role, border strength) | `--background-default`, `--text-default`, `--text-muted`, `--border-default` |

**Rule:** For text, backgrounds, borders, and focus rings on app chrome, **use semantic variables**. Use primitives only for charts, one-off brand locks, or when no semantic exists yet—then add a semantic in `tokens.css` instead of spreading hex.

**Exception policy:** If a literal hex/rgb is unavoidable, add `/* literal: <reason> */` on the same or previous line and note it in PR review.

## Cascade order

1. [`tokens.css`](../static/css/tokens.css) — `:root` primitives + light semantic defaults  
2. [`theme-dark.css`](../static/css/theme-dark.css) — `[data-theme="dark"]` semantic overrides (+ light alpha bridges where needed)  
3. Component CSS under `static/css/*.css` — layout and components consume semantics  
4. [`static/style.css`](../static/style.css) — app-specific overrides last  

`data-theme` is set **before first paint** (inline boot snippet in [`templates/base.html`](../templates/base.html)); [`static/js/theme.js`](../static/js/theme.js) (deferred) re-syncs on `prefers-color-scheme` changes and exposes `window.applyDataloaderTheme('light'|'dark'|'system')` for a future settings toggle.

## Literal inventory (maintenance)

Approximate **`#rrggbb` / `rgb(`** counts under `static/css/` (excluding generated [`tokens.regen-preview.css`](../static/css/tokens.regen-preview.css))—use `rg '#[0-9a-fA-F]{3,8}' static/css` to refresh:

| File | Notes |
|------|--------|
| `tokens.css` | Expected: full primitive scale + light semantics |
| `page-chrome.css` | Breadcrumbs, badges, cards — migrated to semantics where listed in that file |
| `buttons.css`, `forms.css` | Higher literal load; chip away over time |
| `layout.css` | Layout-only; typically no raw colors |

## Third-party widgets

| Widget | Behavior |
|--------|----------|
| **Monaco** | [`static/editor.js`](../static/editor.js) uses `data-theme` first (`dark` → `vs-dark`), then falls back to `--bg` heuristic. Listens for `dataloader-theme-changed` to update open editors. |
| **Mermaid** | [`templates/partials/mermaid_scripts.html`](../templates/partials/mermaid_scripts.html) picks `theme: 'dark' \| 'neutral'` from `data-theme` at init. |
| **TurboGrid** | Vendor stylesheet in `/static/turbogrid/`; prefer wrapping cells with classes that use **semantic** tokens from app CSS. Avoid new grid-specific hex in `static/css/` when a semantic exists. |
| **`mt-patterns.css`** | Uses `--mt-*` aliases defined in `tokens.css`; keep aliases mapped to semantics so dark overrides propagate. |

## CI guard

[`scripts/check_css_literals.py`](../scripts/check_css_literals.py) fails if **new** bare hex literals appear in strict-listed chrome files (currently `page-chrome.css`). Extend the allowlist as more files go semantic-only.

## Related

- Run state / BFF docs: [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) (orthogonal to theming).
