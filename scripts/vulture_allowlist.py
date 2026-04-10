"""Companion module for `vulture` false positives (dynamic use, framework entrypoints).

Run from repo root:
  vulture dataloader flow_compiler db org models scripts/vulture_allowlist.py \\
    --exclude .venv --exclude venv --exclude __pycache__ --exclude tests

When vulture reports a symbol that is used only via reflection (e.g. FastAPI route
names, Alembic, string imports), either:
  - add a no-op reference here (import and mention the name), or
  - use a narrow `# noqa` on the defining line if your team allows it.

This file starts empty; extend as you triage audit output.
"""
