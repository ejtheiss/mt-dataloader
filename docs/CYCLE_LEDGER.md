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
| — | Complexity §4 (1–16): Session deps, tests paths/conftest, Tenacity DRY, display module, discovery helper, `loads_str`, flows session param fixes | 2026-04-01 | ~+52 net on 24 `.py` vs `main` | Local `complexity_reduction_engine_fragility_plan.md` §4 done |
| #3 | Engineering hygiene: FastAPI Depends, Pydantic RunManifest, JSON/SSE helpers | 2026-04-01 | ~−5 net (339/344 on touched `.py`) | Local child plans per maintainer |

When this PR merges, replace **—** in the top row with the real **PR #** and **merged date**.

**Maintainer note:** Full convention + CI phase detail lives in local **`plan/…/00_repo_organization.md`** (gitignored tree). Tracked **`pyproject.toml`** + **`.github/workflows/ci.yml`** run **pytest**, **compileall**, **Ruff** (`check` + `format --check`); **import-linter** still per that plan §6 Phase 2.
