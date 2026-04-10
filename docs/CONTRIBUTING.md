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

### Snapshot tests (**syrupy**)

**Plan 08** (compiler refactor / Mermaid) uses **[syrupy](https://github.com/syrupy-org/syrupy)** for golden snapshots. It is listed in **`requirements.txt`** so CI installs it with `pip install -r requirements.txt`.

| Tests | What they pin |
| ----- | ------------- |
| `tests/test_compile_flows_snapshots.py` | `compile_flows` → **FlowIR** (step shape, depends_on, optional groups) |
| `tests/test_emit_dataloader_config_snapshots.py` | `compile_flows` then **`emit_dataloader_config`** → emitted **`DataLoaderConfig`** (resources, LTs) |

Compiler / generation layout vs plan filenames: **`docs/FLOW_COMPILER_CORE_MODULES.md`** (Track A `core_*`, Track B `generation_pipeline`, deferred Track C).

- Update snapshots intentionally: `pytest --snapshot-update` (or the path to the test file).
- Do not commit snapshot changes without reviewing the diff (silent IR / emit drift is the failure mode we are guarding against).

Normative plan detail: **`plan/…/08_compiler_mermaid_scope.md`** § *Golden snapshots (normative)*.

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
