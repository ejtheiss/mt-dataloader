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

Roadmaps, initiative specs, and cycle backlogs are **not** kept under `docs/`; maintainers track those separately (e.g. a local **gitignored** tree or another repo).
