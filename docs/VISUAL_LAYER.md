# Visual layer — tokens and conventions

This app ships **Jinja + static CSS** (no Tailwind, no CSS bundler). It supports **Light / Dark / System** themes. All color values originate from a single source of truth: [`static/css/theme-tokens.json`](../static/css/theme-tokens.json). Two CSS files are **generated** from that JSON; component CSS consumes only **semantic variables**.

## Single source of truth

[`static/css/theme-tokens.json`](../static/css/theme-tokens.json) owns **both** light and dark color values for every token. The generator script [`scripts/generate_theme_css.py`](../scripts/generate_theme_css.py) reads the JSON and emits:

- [`static/css/tokens.css`](../static/css/tokens.css) — `:root` block with Mint palette, MT palette, semantic defaults (light), badges, app tokens, bridges, legacy aliases, non-color tokens.
- [`static/css/theme-dark.css`](../static/css/theme-dark.css) — `html[data-theme="dark"]` overrides for tokens that have a dark value, plus a TurboGrid scoped block.

**Do not hand-edit `tokens.css` or `theme-dark.css`.** Update the JSON, then run:

```bash
python scripts/generate_theme_css.py            # write both files
python scripts/generate_theme_css.py --check    # CI mode: fail if stale
```

## Primitives vs semantics

| Layer | Role | Examples |
|-------|------|----------|
| **Primitives** | Fixed palette scales; rarely referenced directly in new UI | `--gray-*`, `--blue-*`, `--green-*` |
| **MT Palette** | Approved brand inventory for dark mode assignments | `--mt-palette-black`, `--mt-palette-green-2` |
| **Semantics** | What users perceive (surface, text role, border strength) | `--background-default`, `--text-default`, `--text-muted`, `--border-default` |

**Rule:** For text, backgrounds, borders, and focus rings on app chrome, **use semantic variables**. Use primitives only for charts, one-off brand locks, or when no semantic exists yet — then add a semantic in `theme-tokens.json` and regenerate.

**Adding a new color:** add it to `theme-tokens.json` with `{ "light": "...", "dark": "..." }` values, run the generator, use `var(--your-token)` in CSS. Never add raw hex to a component file.

**Exception policy:** If a literal hex/rgb is unavoidable (e.g. sidebar dark chrome), add `/* literal: <reason> */` on the same or previous line and note it in PR review.

## Cascade order

1. **Inline boot** (`<script>` in `<head>` before any stylesheet) — reads `localStorage("dl_theme")`, resolves `system` via `matchMedia`, sets `data-theme` + `color-scheme` on `<html>` to prevent FOUC.
2. [`tokens.css`](../static/css/tokens.css) — **generated**: `:root` primitives, MT palette, semantic defaults, badges, app tokens, bridges.
3. [`theme-dark.css`](../static/css/theme-dark.css) — **generated**: `html[data-theme="dark"]` semantic overrides + TurboGrid scoped block.
4. Component CSS under `static/css/*.css` — consume semantics only; no color literals.
5. [`static/style.css`](../static/style.css) — layout, sidebar chrome (persistent dark, no theme overrides), non-color overrides.

## Theme runtime

- **Storage key:** `dl_theme` in `localStorage` (values: `light` | `dark` | `system`). Default: `system`.
- **DOM contract:** `html[data-theme]` is always `light` or `dark` (resolved). CSS `color-scheme` set on `<html>` in sync.
- **JS API:** `window.applyDataloaderTheme("light"|"dark"|"system")` — persists, resolves, applies, dispatches event.
- **Event:** `dataloader-theme-changed` on `document` — `detail: { preference, effective }`.
- **Toggle:** `<select id="theme-select">` in sidebar footer; wired via [`static/js/theme.js`](../static/js/theme.js).
- **Sidebar:** persistent dark chrome in both themes. `--sidebar-*` tokens have `"dark": null` in the JSON (no override).

## Normative design references

- [`DARK_MODE_RESEARCH.md`](DARK_MODE_RESEARCH.md) — resolved starting map, semantic token table, badge pairs, contrast evidence, color theory rationale.
- [`theme-tokens.json`](../static/css/theme-tokens.json) — machine-readable source that **supersedes** the research doc if they diverge.

## Third-party widgets

| Widget | Behavior |
|--------|----------|
| **Monaco** | [`static/editor.js`](../static/editor.js) reads `data-theme`; listens to `dataloader-theme-changed` to update live editors (`vs` / `vs-dark`). |
| **Mermaid** | [`templates/partials/mermaid_scripts.html`](../templates/partials/mermaid_scripts.html) initializes with theme-aware config; re-renders on `dataloader-theme-changed`. |
| **TurboGrid** | Vendor stylesheet under `/static/turbogrid/`; dark overrides scoped via `html[data-theme="dark"] .turbogrid` in generated `theme-dark.css`. |
| **`mt-patterns.css`** | Uses `--mt-*` legacy aliases from `tokens.css`; aliases resolve through semantics in both themes. |

## CI guards

- [`scripts/generate_theme_css.py --check`](../scripts/generate_theme_css.py) — fails if `tokens.css` or `theme-dark.css` are stale vs JSON.
- [`scripts/check_css_literals.py`](../scripts/check_css_literals.py) — fails if bare hex literals appear in strict-listed CSS files.
- [`scripts/check_contrast.py`](../scripts/check_contrast.py) — validates WCAG AA contrast for critical dark-mode foreground/background pairs.

## Locked: theme CSS generation (BFF- and Rails-in-spirit)

**Decision (normative):** generate **both** `tokens.css` and `theme-dark.css` with a **single Python build script** (`scripts/generate_theme_css.py`), driven by a **checked-in JSON** token map, run in **CI with `--check`** so committed output matches the map.

- **BFF:** The browser stays dumb; the **server-owned repo** is the source of truth. **One language (Python)** owns compile-style steps.
- **Rails-like:** Same idea as Rake tasks / generators / `assets:precompile`: conventional script + data file, deterministic output, CI enforcement.

## Related

- Run state / BFF: [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) (orthogonal).
