# Cycle PR log

**Plans stay local.** Initiative specs, decomposition checklists, and the full “plan diff” ledger live in a **gitignored** `plan/` tree on maintainer machines — they are **not** in this repository.

This file is the **public, versioned** slice: **which PRs landed**, **when**, and a rough **LoC** hint so history stays greppable without publishing internal docs.

## Conventions

- Prefer **net LoC reduction** on product code when it stays clear.
- After merge, add a row here (newest first). In the PR description you can note `Plan diff: N/A (local)` when no tracked doc changed.
- **LoC:** `git diff main...branch --stat` on touched `.py` paths, or `wc -l` on touched files — approximate is fine.

## PR log (append newest at top)

| PR | Title | Merged | LoC (product, approx.) | Notes |
|----|-------|--------|-------------------------|-------|
| — | 02a Phase **E** (E1–E5): packaged app under **`dataloader/`** — `main`, `routers`, `webhooks`, `engine`, `handlers`, `session`; README **Application wiring**; **`lint-imports`** in CI | — | ~+27 net on 29 `.py` (since merge-base before E1–E5 commits) | Local **`plan/…/02a_rails_layout_alignment.md`** Phase E complete. Replace **—** with PR # and merged date when this lands. Plan diff: **N/A** (plan tree gitignored). |
| — | Complexity §4 (1–16): Session deps, tests paths/conftest, Tenacity DRY, display module, discovery helper, `loads_str`, flows session param fixes | 2026-04-01 | ~+52 net on 24 `.py` vs `main` | Local `90_archive_complexity_engine_stdlib.md` §4 done |
| #3 | Engineering hygiene: FastAPI Depends, Pydantic RunManifest, JSON/SSE helpers | 2026-04-01 | ~−5 net (339/344 on touched `.py`) | Local child plans per maintainer |

When a **—** PR merges, replace **—** in that row with the real **PR #** and **merged date**.

**Maintainer note:** Conventions + CI phases: local **`plan/3.31.26 plans-data loader/00_repo_conventions.md`**. **What to do next:** same folder **`02_backlog_priority.md`**. **Index + PR log:** **`01_cycle_ledger.md`**. Tracked **`pyproject.toml`** + **`.github/workflows/ci.yml`** run **Ruff**, **`lint-imports`**, **compileall**, **pytest**.
