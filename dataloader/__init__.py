"""MT Dataloader application package (02a Phase E).

The ASGI application instance is ``dataloader.main.app``. Run with::

    uvicorn dataloader.main:app

Repo-root ``main.py`` re-exports ``app`` so ``uvicorn main:app`` keeps working
during migration. Per-request loader state lives in ``dataloader.session``.
"""

from __future__ import annotations

__all__: list[str] = []
