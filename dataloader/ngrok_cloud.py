"""Optional ngrok Cloud API (Bearer token) — list / stop remote agent sessions.

Separate from the **authtoken** used by the local agent. Create an API key at
https://dashboard.ngrok.com/api-keys and set ``DATALOADER_NGROK_API_KEY``.
See https://ngrok.com/docs/api-reference/tunnelsessions/list
"""

from __future__ import annotations

from typing import Any

import httpx

NGROK_API_BASE = "https://api.ngrok.com"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "ngrok-version": "2",
        "Content-Type": "application/json",
    }


async def list_tunnel_sessions(*, api_key: str, limit: str = "50") -> dict[str, Any]:
    """Return parsed JSON from GET ``/tunnel_sessions``."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{NGROK_API_BASE}/tunnel_sessions",
            headers=_headers(api_key),
            params={"limit": limit},
        )
        resp.raise_for_status()
        return resp.json()


async def stop_tunnel_session(*, api_key: str, session_id: str) -> None:
    """POST ``/tunnel_sessions/{id}/stop`` — instructs that agent to exit."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{NGROK_API_BASE}/tunnel_sessions/{session_id}/stop",
            headers=_headers(api_key),
            json={"id": session_id},
        )
        resp.raise_for_status()
