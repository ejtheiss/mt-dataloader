# MT reference assets and `plan/resources/`

Design-parity work often needs **Mint / Turbogrid / Tailwind** sources. In this repository, **`plan/` is gitignored** (internal notes and large reference trees stay local).

## What you might keep locally (optional)

| Asset | Typical path (local) | Purpose |
|-------|----------------------|---------|
| Mint Tailwind config | `plan/resources/mint-design-system-reference/config/tailwind.config.js` | Input to `scripts/regen-tokens.js` |
| Mint / MT exports | `plan/resources/mt-porting-kit/`, `mint-design-system-reference/` | Component and token reference |

## Token regeneration

- **Shipped:** `static/css/tokens.css` — curated for this app (variable names match `mt-patterns.css`, `page-chrome.css`, templates).
- **Preview:** `make regen-tokens-preview` or `node scripts/regen-tokens.js` writes **`static/css/tokens.regen-preview.css`** (gitignored). Compare against Mint; do not replace `tokens.css` wholesale until schemas are aligned (see [`docs/UI_GAPS_REMAINING.md`](UI_GAPS_REMAINING.md)).

Pass an explicit config path if your tree differs:

```bash
node scripts/regen-tokens.js /path/to/tailwind.config.js
```

## CI and contributors

Clones without `plan/resources` still run: **icons and CSS in `static/` are committed**. Regen and deep parity diffs need a local Mint snapshot or monorepo checkout (`../../monorepo/platform/tailwind.config.js` is tried after the `plan/resources/...` path).
