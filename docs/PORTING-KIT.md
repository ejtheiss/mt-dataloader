# MT Porting Kit — Canonical Location

**Design authority:** MINT (tokens + Platform-traced CSS + Jinja partials). Turbogrid in `static/turbogrid/` is vendored Flow-admin CSS only — see [`DESIGN_SYSTEM_AUTHORITY.md`](DESIGN_SYSTEM_AUTHORITY.md).

Runtime assets live under **`static/`** and **`templates/`**. Optional Mint **Tailwind** sources for token regen are resolved by **`scripts/regen-tokens.js`** (see that file and [`RESOURCES.md`](RESOURCES.md)); they are not required to run the app.

## Directory Map

```
mt-dataloader/
  static/
    css/
      tokens.css          # Shipped MINT-aligned tokens (curated; see header)
      tokens.regen-preview.css  # Optional output from scripts/regen-tokens.js (gitignored)
      buttons.css         # Button component CSS (from Button.tsx)
      forms.css           # Form/input CSS (from Form.tsx, Input.tsx)
      page-chrome.css     # Page layout, badges, cards, empty state
      chip.css            # Chip component
      pill.css            # Pill component
      filter-bar.css      # Filter bar / dropdown
      drawer.css          # Drawer slide-out
      toast.css           # Toast notifications
      toggle-switch.css   # Toggle switch
      tabs.css            # Tab navigation
      kv-table.css        # Key-value table
      index-table.css     # Index/data tables
      pagination.css      # Pagination controls
      json-view.css       # JSON viewer
      layout.css          # App shell layout
      status-indicator.css # Status dots/indicators
      case-card.css       # Fund Flows case card (composite)
    icons/
      mt/                 # 161 fill-based SVGs from icons.tsx (MT dashboard icon set)
      ui/                 # 23 stroke-based SVGs (Lucide/Feather style for UI controls)
    turbogrid/            # Vendored Turbogrid CSS (if present)
  templates/
    partials/
      icon.html           # Canonical icon rendering partial
      button.html         # Button component
      form.html           # Form fields
      chip.html           # Chip component
      pill.html           # Pill component
      badge.html          # Badge component
      filter_bar.html     # Filter bar
      mt_drawer.html      # Drawer slide-out
      mt_toast.html       # Toast notifications
      toggle_switch.html  # Toggle switch
      kv_table.html       # Key-value table
      resource_table.html # Resource/data table
      export_button.html  # Export dropdown
      json_view.html      # JSON viewer
      empty_state.html    # Empty state display
      status_indicator.html # Status indicators
      case_card.html      # Fund Flows case card (composite: Card + typography + dl)
      scenario_builder.html # Scenario builder (app-specific, no MT equivalent)
      mermaid_accordion.html # Mermaid diagrams (app-specific)
  scripts/
    regen-tokens.js       # Token preview generator (Mint tailwind → CSS vars)
  docs/
    PORTING-KIT.md        # This file
    RESOURCES.md          # Reference assets / local plan/ notes
```

Optional local mirror (often gitignored): `plan/resources/mint-design-system-reference/config/tailwind.config.js`

## Token regeneration (preview only)

```bash
node scripts/regen-tokens.js
node scripts/regen-tokens.js /path/to/tailwind.config.js
```

Writes **`static/css/tokens.regen-preview.css`** (not `tokens.css`). The preview uses a `--color-*` naming scheme; shipped **`tokens.css`** keeps `--gray-*`, semantic bridges, and MT pattern compatibility until the generator is aligned or mapped.

`make regen-tokens-preview` runs the same command.

## Icon Sets

- **`static/icons/mt/`** — 161 fill-based icons extracted from MT's `icons.tsx`.
  24x24 viewBox, `fill="currentColor"`. These are the dashboard's native icons.

- **`static/icons/ui/`** — 23 stroke-based icons (Lucide/Feather style).
  24x24 viewBox, `stroke="currentColor"`, `stroke-width="2"`.
  Used for UI controls (close, filter, chevron, etc.) where MT uses an icon font
  the dataloader doesn't have access to.

### Using icons in templates

```jinja2
{# Simple icon reference #}
<img src="/static/icons/ui/settings.svg" width="18" height="18" alt="" aria-hidden="true">

{# Or use the icon partial for more control #}
{% set icon_name = "settings" %}
{% set set = "ui" %}
{% set size = 18 %}
{% include "partials/icon.html" %}
```

## Adding New Components

1. Find the React source in the MT monorepo (e.g. `dashboard/components/ui-components/`)
2. Create a Jinja2 partial in `templates/partials/` with the same structure
3. Create matching CSS in `static/css/` using the component's Tailwind classes resolved to values
4. Reference the MT source file and line numbers in comments
5. Use tokens from `tokens.css` for colors, spacing, typography
