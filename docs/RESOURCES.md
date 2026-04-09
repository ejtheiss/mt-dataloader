# Optional reference inputs (design tokens)

Shipped UI uses committed **`static/css/tokens.css`**, **`static/mt-patterns.css`**, and related CSS under **`static/css/`**. You do not need any extra files to run the app or tests.

## Token regeneration (preview only)

`scripts/regen-tokens.js` can emit **`static/css/tokens.regen-preview.css`** so you can diff against Mint’s Tailwind palette. It does **not** overwrite **`tokens.css`**; merge manually only after verifying variable naming and consumers.

```bash
make regen-tokens-preview
# or
node scripts/regen-tokens.js
# or pass an explicit config:
node scripts/regen-tokens.js /path/to/tailwind.config.js
```

Default config search order and paths are documented in the **script header** (`scripts/regen-tokens.js`). The first existing candidate wins unless you pass a path on the command line.

Clones without a local Mint/monorepo checkout can still develop: committed CSS and icons are sufficient. Regen is optional parity tooling.
