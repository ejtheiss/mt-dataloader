"""Modern Treasury webhook endpoint CRUD via HTTP.

The ``modern-treasury`` Python SDK exposes ``client.webhooks`` only for
signature helpers — not for listing/creating webhook endpoints — so we
call ``/api/webhook_endpoints`` directly with the same Basic auth scheme
as :class:`modern_treasury.AsyncModernTreasury`.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

_DEFAULT_BASE = "https://app.moderntreasury.com"


def normalize_webhook_url(url: str) -> str:
    """Strip whitespace and trailing slashes for listener URL comparison."""
    return (url or "").strip().rstrip("/")


def analyze_org_webhook_listeners(
    endpoints: list[dict[str, Any]],
    expected_full_url: str,
    webhook_path: str = "/webhooks/mt",
) -> dict[str, Any]:
    """Compare MT webhook endpoints to the tunnel listener URL.

    Returns keys: ``match`` (bool), ``endpoint_id`` (str | None),
    ``stale_url`` (str | None) if an endpoint uses ``webhook_path`` but
    a different full URL than ``expected_full_url``.
    """
    expected = normalize_webhook_url(expected_full_url)
    stale: str | None = None
    for ep in endpoints:
        u_raw = ep.get("url") or ""
        u = normalize_webhook_url(u_raw)
        if not u:
            continue
        if u == expected:
            eid = ep.get("id")
            return {
                "match": True,
                "endpoint_id": str(eid) if eid is not None else None,
                "stale_url": None,
            }
        if webhook_path in u_raw:
            stale = u_raw.strip() or u
    return {"match": False, "endpoint_id": None, "stale_url": stale}


def _basic_auth_header(organization_id: str, api_key: str) -> str:
    raw = f"{organization_id}:{api_key}".encode("ascii")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _auth_headers(organization_id: str, api_key: str) -> dict[str, str]:
    return {
        "Authorization": _basic_auth_header(organization_id, api_key),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _normalize_list_payload(data: object) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "webhook_endpoints"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


async def list_webhook_endpoints(
    *,
    api_key: str,
    organization_id: str,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Return webhook endpoint objects from GET ``/api/webhook_endpoints``."""
    root = (base_url or _DEFAULT_BASE).rstrip("/")
    url = f"{root}/api/webhook_endpoints"
    headers = _auth_headers(organization_id, api_key)
    out: list[dict[str, Any]] = []
    after_cursor: str | None = None
    async with httpx.AsyncClient(timeout=timeout) as http:
        for _ in range(50):
            params: dict[str, str] = {"per_page": "100"}
            if after_cursor:
                params["after_cursor"] = after_cursor
            resp = await http.get(url, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()
            batch = _normalize_list_payload(payload)
            out.extend(batch)
            after_cursor = None
            if isinstance(payload, dict):
                ac = payload.get("after_cursor")
                if isinstance(ac, str) and ac:
                    after_cursor = ac
            if not after_cursor:
                break
    return out


async def create_webhook_endpoint(
    *,
    api_key: str,
    organization_id: str,
    url: str,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    root = (base_url or _DEFAULT_BASE).rstrip("/")
    endpoint = f"{root}/api/webhook_endpoints"
    headers = _auth_headers(organization_id, api_key)
    async with httpx.AsyncClient(timeout=timeout) as http:
        resp = await http.post(endpoint, headers=headers, json={"url": url})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected create webhook response shape")
        return data


async def patch_webhook_endpoint(
    *,
    api_key: str,
    organization_id: str,
    endpoint_id: str,
    url: str,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    root = (base_url or _DEFAULT_BASE).rstrip("/")
    ep = f"{root}/api/webhook_endpoints/{endpoint_id}"
    headers = _auth_headers(organization_id, api_key)
    async with httpx.AsyncClient(timeout=timeout) as http:
        resp = await http.patch(ep, headers=headers, json={"url": url})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected patch webhook response shape")
        return data
