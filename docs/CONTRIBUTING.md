# Contributing

## Environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

After pulling changes that add dependencies, run `pip install -r requirements.txt` again.

## Tests

```bash
python -m pytest tests/ -q
python -m pytest tests/ -x -q   # stop on first failure
```

## Run the app

```bash
python -m uvicorn dataloader.main:app --host 127.0.0.1 --port 8000
```

## UI / design assets

- **[`DESIGN_SYSTEM_AUTHORITY.md`](DESIGN_SYSTEM_AUTHORITY.md)** — MINT vs Turbogrid scope in this repo.
- **[`PORTING-KIT.md`](PORTING-KIT.md)** — Where static templates, CSS, and icons live; how they map to upstream MT patterns.
- **[`RESOURCES.md`](RESOURCES.md)** — Optional inputs for token regeneration (`scripts/regen-tokens.js`).

## Naming, preview rows, and display strings

- **[`ARCHITECTURE_NAMING_AND_DISPLAY.md`](ARCHITECTURE_NAMING_AND_DISPLAY.md)** — Vocabulary, locked display rules (`preview_items` vs MT-shaped `config`), file map, NW-* work IDs, and **deletion register** for duplicate naming helpers. Use this when changing `build_preview`, Mermaid labels, fund-flow columns, or grouped preview actors.

Roadmaps, **cycle** ledgers, and **which PR ships which NW item** are **not** in `docs/`; maintainers track those in a local **gitignored** `plan/` tree (or equivalent).
