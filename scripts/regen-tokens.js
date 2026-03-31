#!/usr/bin/env node
/**
 * regen-tokens.js — Generate static/css/tokens.css from MT's tailwind.config.js
 * ==============================================================================
 *
 * This script reads the color palette and design tokens from the MT platform's
 * Tailwind config and outputs a plain CSS file of custom properties that the
 * dataloader can consume without needing Tailwind or PostCSS at build time.
 *
 * Usage:
 *   node scripts/regen-tokens.js
 *   node scripts/regen-tokens.js /path/to/tailwind.config.js
 *
 * Output: static/css/tokens.regen-preview.css (NOT tokens.css — the app uses a
 * hand-curated variable schema; compare previews before merging).
 *
 * Config resolution (first existing wins unless argv[2] overrides):
 *   1. plan/resources/mint-design-system-reference/config/tailwind.config.js
 *   2. ../../monorepo/platform/tailwind.config.js (repo beside monorepo)
 */

const fs = require("fs");
const path = require("path");

// ---------------------------------------------------------------------------
// 1. Resolve the Tailwind config path
// ---------------------------------------------------------------------------
const configCandidates = [
  path.resolve(
    __dirname,
    "../plan/resources/mint-design-system-reference/config/tailwind.config.js"
  ),
  path.resolve(__dirname, "../../monorepo/platform/tailwind.config.js"),
];
const argvPath = process.argv[2];
const configPath =
  argvPath ||
  configCandidates.find((p) => fs.existsSync(p)) ||
  null;

if (!configPath || !fs.existsSync(configPath)) {
  console.error("[regen-tokens] Tailwind config not found. Tried:");
  for (const p of configCandidates) console.error("  -", p);
  console.error("  Pass an explicit path: node scripts/regen-tokens.js /path/to/tailwind.config.js");
  process.exit(1);
}
console.log("[regen-tokens] Using config:", configPath);

// ---------------------------------------------------------------------------
// 2. Read and parse the config (lightweight eval — no Tailwind dependency)
// ---------------------------------------------------------------------------
let configSource = fs.readFileSync(configPath, "utf8");

// Strip ESM import/export so we can eval as CommonJS
configSource = configSource
  .replace(/^import .+$/gm, "")
  .replace(/^export default config;?$/gm, "");

// Provide stubs for the Tailwind helpers the config imports
const stubTheme = { screens: { sm: "640px", md: "768px", lg: "1024px", xl: "1280px", "2xl": "1536px" }, fontFamily: { mono: ["ui-monospace", "monospace"], sans: ["ui-sans-serif", "sans-serif"] } };
const sandbox = { plugin: () => {}, containerQueries: {}, theme: stubTheme, animation: {} };

let config;
try {
  const fn = new Function("plugin", "containerQueries", "theme", "animation", configSource + "\nreturn config;");
  config = fn(sandbox.plugin, sandbox.containerQueries, sandbox.theme, sandbox.animation);
} catch (e) {
  console.error("[regen-tokens] Failed to parse tailwind.config.js:", e.message);
  process.exit(1);
}

const colors = config.theme.colors || {};
const extend = config.theme.extend || {};

// ---------------------------------------------------------------------------
// 3. Flatten colors into CSS custom properties
// ---------------------------------------------------------------------------
function flattenColors(obj, prefix) {
  const result = [];
  for (const [key, value] of Object.entries(obj)) {
    const varName = prefix ? `${prefix}-${key}` : key;
    if (typeof value === "string") {
      // Skip CSS var references (semantic tokens handled separately)
      if (!value.startsWith("var(")) {
        result.push(`  --color-${varName}: ${value};`);
      }
    } else if (typeof value === "object" && value !== null) {
      result.push(...flattenColors(value, varName));
    }
  }
  return result;
}

const colorVars = flattenColors(colors, "");

// ---------------------------------------------------------------------------
// 4. Extract semantic tokens (themeLight)
// ---------------------------------------------------------------------------
let semanticVars = [];
if (typeof config.theme.themes === "function") {
  // Resolve the theme function with the color palette
  const themeAccessor = (section) => {
    if (section === "colors") return colors;
    return {};
  };
  try {
    const themes = config.theme.themes(themeAccessor);
    const rootVars = themes[":root"] || {};
    for (const [varName, value] of Object.entries(rootVars)) {
      semanticVars.push(`  ${varName}: ${value};`);
    }
  } catch (e) {
    console.warn("[regen-tokens] Could not resolve semantic tokens:", e.message);
  }
}

// ---------------------------------------------------------------------------
// 5. Extract spacing, font sizes, shadows, breakpoints
// ---------------------------------------------------------------------------
const spacingVars = [];
if (extend.spacing) {
  for (const [key, value] of Object.entries(extend.spacing)) {
    spacingVars.push(`  --spacing-${key}: ${value};`);
  }
}

const fontSizeVars = [];
if (extend.fontSize) {
  for (const [key, value] of Object.entries(extend.fontSize)) {
    const size = Array.isArray(value) ? value[0] : value;
    const lineHeight = Array.isArray(value)
      ? typeof value[1] === "string"
        ? value[1]
        : value[1]?.lineHeight || "normal"
      : "normal";
    fontSizeVars.push(`  --font-size-${key}: ${size};`);
    fontSizeVars.push(`  --line-height-${key}: ${lineHeight};`);
  }
}

const shadowVars = [];
if (extend.boxShadow) {
  for (const [key, value] of Object.entries(extend.boxShadow)) {
    shadowVars.push(`  --shadow-${key}: ${value};`);
  }
}

const screenVars = [];
if (config.theme.screens) {
  for (const [key, value] of Object.entries(config.theme.screens)) {
    screenVars.push(`  --screen-${key}: ${value};`);
  }
}

// ---------------------------------------------------------------------------
// 6. Write tokens.css
// ---------------------------------------------------------------------------
const timestamp = new Date().toISOString();
const output = `/*
 * tokens.css — Auto-generated from MT platform/tailwind.config.js
 * ================================================================
 * DO NOT HAND-EDIT. Regenerate with:
 *   node scripts/regen-tokens.js
 *
 * Source: ${path.basename(configPath)}
 * Generated: ${timestamp}
 */

:root {
  /* ---- Color Palette ---- */
${colorVars.join("\n")}

  /* ---- Semantic / MINT Product Tokens ---- */
${semanticVars.join("\n")}

  /* ---- Spacing ---- */
${spacingVars.join("\n")}

  /* ---- Font Sizes & Line Heights ---- */
${fontSizeVars.join("\n")}

  /* ---- Shadows ---- */
${shadowVars.join("\n")}

  /* ---- Breakpoints (for reference — use @media with px values) ---- */
${screenVars.join("\n")}

  /* ---- Font Stacks ---- */
  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  /* ---- Font Feature Settings (Inter) ---- */
  --font-features: "cv08", "cv10", "ss07", "ss08";
}
`;

const outPath = path.resolve(__dirname, "../static/css/tokens.regen-preview.css");
fs.writeFileSync(outPath, output, "utf8");
console.log(`[regen-tokens] Written ${outPath} (preview only — do not swap for tokens.css without schema alignment)`);
console.log(`  ${colorVars.length} color vars, ${semanticVars.length} semantic vars, ${spacingVars.length} spacing vars, ${fontSizeVars.length} font-size vars`);
