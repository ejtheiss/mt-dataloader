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
| — | Complexity §4 non-breaking hygiene (engine, cleanup, webhooks, models, tunnel, main) | — | ~−11 net on touched `.py` + `mt_doc_links.py` | Local complexity plan updated §4 checkboxes |
| #3 | Engineering hygiene: FastAPI Depends, Pydantic RunManifest, JSON/SSE helpers | 2026-04-01 | ~−5 net (339/344 on touched `.py`) | Local child plans per maintainer |

When this PR merges, replace **—** in the top row with the real **PR #** and **merged date**.
