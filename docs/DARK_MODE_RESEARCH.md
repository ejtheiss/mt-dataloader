# Dark Mode Research and Resolved Token Map

This document resolves the initial dark-mode "starting map" into a concrete, MT-palette-only semantic mapping for this app's foundational UI layer.

It is intentionally implementation-adjacent (token-level and file-level), but remains a research/design authority until implementation PRs are approved.

## Scope and constraints

- Dark mode is an **optional** supplement to light mode (`light | dark | system`), not a replacement.
- Any new literal color must come from the approved MT inventory in the active plan.
- No Material, Mint, or ad hoc hex values outside that list.
- Token-first architecture: component CSS consumes semantics, not raw literals.

Primary companion plan: `.cursor/plans/dark_mode_research_1ade637b.plan.md`.

## Locked tooling (BFF- and Rails-in-spirit)

**Normative approach:** ship dark (and other derived) theme CSS using a **small Python build script** (e.g. `scripts/generate_theme_dark_css.py`) plus a **checked-in JSON or YAML** token map. **CI** runs the script with `**--check`** so committed `theme-dark.css` (or equivalent) always matches the map; developers may run the same script locally like a Rake task.

**BFF:** The browser stays dumb; the **server-owned repo** is the source of truth. **One language (Python)** owns compile steps—no parallel Node asset pipeline or design-token microservice.

**Rails-like:** Same idea as Rake tasks / generators / `assets:precompile`: a **conventional script + data file**, **deterministic output**, and **CI** that enforces the map. No import-map vs webpack debate; no extra runtime for delivering tokens.

See also `[VISUAL_LAYER.md](VISUAL_LAYER.md)` § *Locked: theme CSS generation*.

## Sources used

### Standards and platform guidance

- [Material Design M2: Dark theme usage](https://m2.material.io/design/color/dark-theme.html#usage)
- [WCAG 2.2: Understanding SC 1.4.3 Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
- [MDN: prefers-color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme)
- [MDN: color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/color-scheme)
- [Apple HIG: Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)

### Research and practitioner synthesis

- [NN/g: Dark Mode vs. Light Mode](https://www.nngroup.com/articles/dark-mode/)
- [NN/g: How users think about dark mode and issues to avoid](https://www.nngroup.com/articles/dark-mode-users-issues/)
- [A guide to dark mode design (James Robinson)](https://www.jamesrobinson.io/post/a-guide-to-dark-mode-design)
- [Dark Mode Design: More Than Just a Color Swap](https://medium.com/design-bootcamp/dark-mode-design-more-than-just-a-color-swap-191d5cf8f65d)
- [r/webdev discussion: palette strategy in dark/light mode](https://www.reddit.com/r/webdev/comments/1lq11kn/question_about_colour_palettes_and_darklight_mode/)

## Key findings translated to this codebase

1. **Two mappings, not inversion:** dark needs a dedicated semantic remap, not a global invert/filter.
2. **Dark surfaces need hierarchy:** use a stepped surface ladder where **navigation is darkest** and content surfaces step progressively lighter. Material M2 elevation model: "higher elevation = lighter surface" ([M2 dark theme](https://m2.material.io/design/color/dark-theme.html#properties)). Robinson: "use lighter shades of the background color to indicate elevation and depth" — the content area is the primary working surface and must read as "elevated" relative to the persistent nav chrome.
3. **Left nav is the darkest element.** The sidebar is persistent dark chrome in both themes. In dark mode, all content surfaces must be visibly lighter than the nav to maintain spatial hierarchy. This avoids the "flat dark wall" antipattern where nav and content merge ([NN/g: issues to avoid](https://www.nngroup.com/articles/dark-mode-users-issues/); [Robinson: contrast is different in the dark](https://www.jamesrobinson.io/post/a-guide-to-dark-mode-design)).
4. **Limit high-chroma accents:** on dark backgrounds, saturated accents should be used sparingly and often shifted to lighter/desaturated tones for legibility.
5. **Avoid harsh extremes for body text:** prefer off-white defaults over pure white for dense reading surfaces.
6. **Respect user/system intent:** support `system` and apply before first paint.
7. **Contrast is enforced by role:** normal text >= 4.5:1, large text >= 3:1, and UI boundaries/focus indicators must be visibly distinct.

## Resolved starting map (MT palette only)

This resolves the plan's initial high-level map into concrete choices.


| Role                                  | Resolved MT swatch | Hex       | Notes                                                  |
| ------------------------------------- | ------------------ | --------- | ------------------------------------------------------ |
| Left nav / sidebar (darkest)          | Black              | `#151515` | Persistent dark chrome; darkest element in both themes |
| Content area background               | Off-Black          | `#222220` | Main working surface; visibly lighter than nav         |
| Base surface (cards/panels)           | Gray 4             | `#30302E` | Elevated containers on content background              |
| Primary text                          | Off-White          | `#F4F4F2` | Better reading ergonomics than pure white default      |
| Strong text / on-dark critical labels | White              | `#FFFFFF` | Reserved for high-emphasis + on-accent text            |
| Secondary text                        | Gray 2             | `#A9A9A7` | Passes AA on dark surfaces                             |
| Disabled text                         | Gray 3             | `#737371` | Use for disabled/low-emphasis only                     |
| Borders / separators                  | Gray 3             | `#737371` | Chosen for visible boundary contrast on dark surfaces  |
| Muted decorative lines                | Gray 4             | `#30302E` | Decorative only; not for critical boundaries           |
| Success accent/content                | Green 2            | `#69B894` | Preferred readable success text/accent on dark         |
| Primary action fill                   | Green 3 (MT Green) | `#008060` | Brand primary fill, with white label                   |
| Info/link accent                      | Blue 2             | `#54AEFF` | Readable link/info tone on dark                        |
| Warning accent                        | Gold 2             | `#D49C66` | Readable warning content on dark                       |
| Critical accent                       | Orange 2           | `#FF8266` | Readable critical content on dark                      |


## Semantic token mapping (resolved)

The following mapping is the recommended dark assignment for existing semantics in `static/css/tokens.css`.


| Semantic token                  | Dark mapping | MT name                                        |
| ------------------------------- | ------------ | ---------------------------------------------- |
| `--app-background`              | `#222220`    | Off-Black (content area; lighter than nav)     |
| `--background-default`          | `#30302E`    | Gray 4 (cards/panels; elevated above content)  |
| `--background-light`            | `#30302E`    | Gray 4 (same as default; use for subtle tints) |
| `--background-dark`             | `#151515`    | Black (deepest; matches nav chrome)            |
| `--text-default`                | `#F4F4F2`    | Off-White                                      |
| `--text-muted`                  | `#A9A9A7`    | Gray 2                                         |
| `--text-disabled`               | `#737371`    | Gray 3                                         |
| `--text-on-primary`             | `#FFFFFF`    | White                                          |
| `--text-success`                | `#69B894`    | Green 2                                        |
| `--text-info`                   | `#54AEFF`    | Blue 2                                         |
| `--text-critical`               | `#FF8266`    | Orange 2                                       |
| `--border-default`              | `#737371`    | Gray 3                                         |
| `--border-strong`               | `#A9A9A7`    | Gray 2                                         |
| `--border-muted`                | `#30302E`    | Gray 4                                         |
| `--action-primary`              | `#008060`    | Green 3                                        |
| `--action-primary-hover`        | `#14362B`    | Green 4                                        |
| `--action-primary-pressed`      | `#12362E`    | Dark Green                                     |
| `--primary-interactive`         | `#54AEFF`    | Blue 2                                         |
| `--primary-interactive-hover`   | `#0071E3`    | Blue 3                                         |
| `--primary-interactive-pressed` | `#1A2B54`    | Blue 4                                         |


### Badge pair mapping (dark)

Keep paired foreground/background swatches so badges remain legible without ad hoc overrides.


| Badge semantic pair | Foreground           | Background           |
| ------------------- | -------------------- | -------------------- |
| Neutral             | Gray 1 (`#DEDEDC`)   | Gray 4 (`#30302E`)   |
| Success             | Green 1 (`#C4E5D1`)  | Green 4 (`#14362B`)  |
| Critical            | Orange 1 (`#FFCCB5`) | Orange 4 (`#591A08`) |
| Warning             | Gold 1 (`#EDDBB0`)   | Gold 4 (`#4A1F0D`)   |
| Cool / Info         | Blue 1 (`#C4E0FA`)   | Blue 4 (`#1A2B54`)   |
| Violet              | Violet 1 (`#F0D6E5`) | Violet 4 (`#4A1C33`) |
| Purple              | Purple 1 (`#E3D6FF`) | Purple 4 (`#2E1F69`) |


## Contrast evidence (selected token pairs)

Calculated with WCAG relative luminance formula (no rounding up pass thresholds).


| Pair                   | Ratio   | Status                                                   |
| ---------------------- | ------- | -------------------------------------------------------- |
| Off-White on Off-Black | 14.47:1 | Pass AA/AAA — primary text on content area               |
| Off-White on Gray 4    | 12.01:1 | Pass AA/AAA — primary text on cards/panels               |
| Gray 2 on Off-Black    | 6.77:1  | Pass AA normal text — muted text on content              |
| Gray 2 on Gray 4       | 5.62:1  | Pass AA normal text — muted text on cards                |
| Gray 3 on Off-Black    | 3.35:1  | Pass large text / UI boundary only                       |
| Blue 2 on Off-Black    | 6.74:1  | Pass AA normal text                                      |
| Blue 2 on Gray 4       | 5.47:1  | Pass AA normal text — links on cards                     |
| Green 2 on Off-Black   | 6.74:1  | Pass AA normal text                                      |
| Orange 2 on Off-Black  | 6.55:1  | Pass AA normal text                                      |
| White on Green 3       | 4.93:1  | Pass AA normal text                                      |
| White on Blue 3        | 4.70:1  | Pass AA normal text                                      |
| Off-White on Blue 3    | 4.26:1  | Fail AA normal text (do not use for body text on Blue 3) |
| Green 1 on Green 4     | 9.72:1  | Pass AA/AAA                                              |
| Orange 1 on Orange 4   | 9.24:1  | Pass AA/AAA                                              |
| Gold 1 on Gold 4       | 10.29:1 | Pass AA/AAA                                              |
| Blue 1 on Blue 4       | 10.14:1 | Pass AA/AAA                                              |


## Foundational static/component implications

These are required before broad dark roll-out:

1. **Token authority in `static/css/tokens.css`**
  - Keep literals only in palette definitions.
  - Route all semantic roles through those palette aliases.
2. **Literal cleanup in shared CSS**
  - Prioritize `static/style.css`, then shared component CSS modules.
  - Remove fallback hex/rgba where semantic tokens exist.
3. **Template/theme contract in `templates/base.html`**
  - Resolve and apply `data-theme` before paint.
  - Insert theme overlay stylesheet immediately after `tokens.css`.
4. **Runtime third-party sync**
  - Monaco: theme from resolved mode (`data-theme`), not only `--bg` heuristic.
  - Mermaid: theme variables aligned to semantics; rerender on theme change.
  - TurboGrid / `mt-patterns.css`: consume semantic/alias tokens only.
5. **CI guardrails**
  - Expand strict literal checks progressively beyond `page-chrome.css`.
  - Add validation that new literals come only from approved MT inventory.

## Token migration checklist by file

Use this as the execution checklist for implementation PRs. Migrate in order.

### Phase 0: token authority and theme boot


| File                              | Migration tasks                                                                                                                                                           | Done criteria                                                                                     |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `static/css/tokens.css`           | Add dark-ready semantic roles (surface ladder, overlays, focus ring), keep MT palette aliases canonical, preserve light defaults.                                         | No component-level literals added; semantics cover all shared roles needed by migrated files.     |
| `templates/base.html`             | Add early theme boot (`light` / `dark` / `system`) before CSS paint; set `data-theme` on `html` and `color-scheme`; link `theme-dark.css` immediately after `tokens.css`. | No flash of wrong theme on initial load; system mode tracks OS preference.                        |
| `static/css/theme-dark.css` (new) | Add only semantic remaps under `[data-theme="dark"]`; no structural/component selectors except documented exceptions.                                                     | Every literal maps to approved MT inventory; no direct component styling beyond token assignment. |


### Phase 1: shared chrome and high-impact shells


| File                         | Migration tasks                                                                                                                                     | Done criteria                                                                                                        |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `static/style.css`           | Replace hardcoded `rgba(...)`, fallback hexes, and status literal fallbacks with semantic tokens; extract missing semantics back into `tokens.css`. | No raw color literals outside approved exception comments; sidebar/org-switcher/readability verified in both themes. |
| `static/css/layout.css`      | Confirm containers/shell backgrounds and separators use semantics only.                                                                             | Background and border roles switch correctly with `data-theme`; no new literals.                                     |
| `static/css/page-chrome.css` | Keep semantic-only contract; verify mapped semantics are sufficient in dark.                                                                        | Existing literal guard still passes; page header/body/cards/badges render correctly in dark.                         |
| `static/mt-patterns.css`     | Route modal/alert/chip states through semantic or `--mt-`* aliases that resolve via semantics in both themes.                                       | No stranded hardcoded tones for shared components; modal depth is legible in dark.                                   |


### Phase 2: foundational component modules


| File                                                                          | Migration tasks                                                                                       | Done criteria                                                                  |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `static/css/buttons.css`                                                      | Ensure primary/secondary/critical/focus/disabled states reference semantic action/text/border tokens. | Button text contrast and focus states pass AA/non-text contrast checks.        |
| `static/css/forms.css`                                                        | Migrate inputs, labels, help/error text, borders, placeholders, and focus rings to semantic tokens.   | Form controls maintain readable defaults and clear focus/error states in dark. |
| `static/css/status-indicator.css`                                             | Replace per-status literal fallbacks with semantic status tokens mapped to MT swatches.               | All status chips remain distinguishable and contrast-compliant in dark.        |
| `static/css/case-card.css`                                                    | Remove fallback literals in borders/backgrounds/text; rely on semantic card roles.                    | Card content and metadata remain legible across both themes.                   |
| `static/css/index-table.css`                                                  | Migrate table row states, hover, active, and metadata text colors to semantics.                       | Dense table views maintain hierarchy and hover affordance in dark.             |
| `static/css/kv-table.css`                                                     | Migrate key/value emphasis, separators, and muted labels to semantic roles.                           | Key/value contrast and scanability pass dark-mode checks.                      |
| `static/css/tabs.css`                                                         | Ensure active/inactive/tab-border/focus indicators use semantic interactive tokens.                   | Tab state is obvious in both themes without color-only ambiguity.              |
| `static/css/filter-bar.css`                                                   | Migrate token usage for controls/chips/input surfaces and separators.                                 | Filter controls remain visually grouped and readable in dark.                  |
| `static/css/pill.css` / `static/css/chip.css`                                 | Map chip/pill variants to semantic badge/status pairs.                                                | Variant colors remain consistent with resolved badge mapping.                  |
| `static/css/drawer.css` / `static/css/toast.css` / `static/css/json-view.css` | Migrate overlays/surfaces/borders/text to semantic roles and check elevated-surface ladder.           | Overlays and stacked surfaces preserve depth in dark.                          |


### Phase 3: third-party/static runtime alignment


| File                                                              | Migration tasks                                                                                                  | Done criteria                                                                  |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `static/editor.js`                                                | Switch Monaco theme selection to resolved `data-theme`; subscribe to theme-change event and update live editors. | Existing editors switch theme without reload; no mismatch after toggle.        |
| `templates/partials/mermaid_scripts.html`                         | Use theme variables derived from semantic roles; rerender safely on theme change.                                | Mermaid diagrams remain legible and consistent with app surfaces.              |
| `static/turbogrid/variables.css` and app-side TurboGrid overrides | Bridge TurboGrid variables from semantics/`--mt-`* aliases only.                                                 | Grid field/status/error states remain consistent with resolved dark token map. |


### CI/lint rollout checklist


| Guardrail                           | Migration tasks                                                                                                 | Done criteria                                              |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `scripts/check_css_literals.py`     | Add migrated files incrementally to `STRICT_CSS_FILES` as they reach semantic-only status.                      | CI fails on new bare literals in migrated files.           |
| MT-inventory provenance check (new) | Add validation for any new literal: allowed only in token definition zones and must match approved MT swatches. | CI catches non-MT literals and out-of-zone color literals. |
| Contrast verification step          | Add script/checklist to validate required semantic pairs used by components.                                    | Required text/surface and badge pairs meet AA thresholds.  |


### Final acceptance gate

- All files in phases 1-3 are migrated or explicitly deferred with rationale.
- No new color literals outside token definition zones.
- Light mode visual parity is maintained.
- Dark mode token behavior matches this document's resolved map.
- Monaco, Mermaid, and TurboGrid follow theme changes without stale rendering.

## Explicit decisions from this research

- **Left nav is the darkest element** in dark mode (Black `#151515`); content area steps to Off-Black; cards to Gray 4.
- Use **Off-White**, not White, as default body text in dark.
- Use **Gray 2** for muted text; reserve Gray 3 for disabled/low-emphasis.
- Use **Blue 2** as default link/info color in dark surfaces.
- Keep **Green 3** as primary action fill with **White** label.
- Dark surface ladder: **Nav (Black) -> Content (Off-Black) -> Cards (Gray 4)**.
- Keep badge semantics as explicit fg/bg pairs (no runtime mixing heuristics).

## Open questions to confirm in implementation PR

- ~~Whether any long-form content surfaces should switch to `Gray 4` base for reduced contrast fatigue.~~ **Resolved:** content area uses Off-Black; cards/panels use Gray 4. Nav stays Black as darkest element.
- Whether focus ring should be Blue 2 globally or context-sensitive (Blue 2 on neutrals, White on chroma fills).
- Whether we need a dedicated high-contrast mode later (separate from dark).

