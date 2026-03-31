# UI porting — what landed vs what is still open

This doc tracks the **mt-icon-set-and-porting-gaps** work after it was **decomposed** into the repo (not applied as a single raw patch). Use it with [`docs/PORTING-KIT.md`](PORTING-KIT.md).

## Landed in this pass

| Area | Status |
|------|--------|
| **Canonical icons** | `static/icons/mt/` (fill) and `static/icons/ui/` (stroke) added. |
| **Inline SVG → assets** | Sidebar, listener filters, and listed partials now reference `/static/icons/...` where the patch touched them. |
| **`icon.html` partial** | `templates/partials/icon.html` — optional wrapper for consistent sizing/alt text. |
| **Fund Flows case card** | `templates/partials/case_card.html`, `static/css/case-card.css`, included from `templates/flows.html`; `base.html` loads `case-card.css`. |
| **Token regen script** | `scripts/regen-tokens.js` resolves Mint config from `plan/resources/.../tailwind.config.js` or `../../monorepo/...`; writes **`tokens.regen-preview.css`** only (see below). |
| **Docs** | `docs/PORTING-KIT.md`, `docs/RESOURCES.md`. |
| **Shipped `tokens.css`** | **Unchanged schema** — merge conflict with patch output was resolved **in favor of the existing** `--gray-*` / semantic file; header documents regen limits. |

## Still outstanding / follow-ups

1. **Token pipeline** — Align `regen-tokens.js` output with shipped variable names **or** add a mapping layer so `tokens.css` can be regenerated safely. Until then, use `tokens.regen-preview.css` only for **diffing** against Mint.

2. **Icon sweep** — Templates **not** in the original patch still may use inline SVGs (e.g. `flows_view.html`, `setup.html`, `runs_page.html`, pagination, `status_indicator.html`, drawers). Continue migrating to `static/icons/mt/` or `ui/` + `icon.html`.

3. **CDN scripts** — `base.html` still loads HTMX and Monaco from CDNs; MINT rules prefer self-hosted assets for parity and CSP.

4. **Fund Flows flagship** — Case card is a **component**; list/detail **IA**, density, and scenario/Mermaid layout are still separate design work.

5. **App-wide alignment** — Non–Fund-Flows screens (setup, runs, execute, preview, cleanup) are not covered by this pass.

6. **Jinja fragments / BFF** — Not in scope here; see your local plan docs if you track that program separately.

7. **`plan/resources` on clone** — `plan/` is **gitignored** in this repo; contributors need a **local** Mint snapshot for regen input. Shipped CSS/icons keep the app usable without it.

8. **CI** — Optional: fail if `tokens.regen-preview.css` drifts when config is present; no workflow added yet.

## Reference

- Source patch (local only, not committed): `plan/resources/mt-icon-set-and-porting-gaps.patch`
