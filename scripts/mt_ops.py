#!/usr/bin/env python3
"""Operational helpers against Modern Treasury (metadata list, ledger balance).

Uses the same credentials as the app: set ``DATALOADER_MT_API_KEY`` and
``DATALOADER_MT_ORG_ID`` (or a ``.env`` in the repo root).

Examples::

    PYTHONPATH=. python scripts/mt_ops.py list-by-metadata expected_payment -m deal_id=DEAL-x-0042
    PYTHONPATH=. python scripts/mt_ops.py list-by-metadata transaction -m Type=Payroll --limit 50
    PYTHONPATH=. python scripts/mt_ops.py ledger-balance la_123 --effective-at 2025-05-01T12:00:00Z
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _parse_metadata(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"metadata must be key=value, got: {item!r}")
        k, _, v = item.partition("=")
        out[k.strip()] = v.strip()
    return out


def _client():
    from modern_treasury import AsyncModernTreasury

    from models.settings import AppSettings

    s = AppSettings()
    if not s.mt_api_key or not s.mt_org_id:
        raise SystemExit(
            "Set DATALOADER_MT_API_KEY and DATALOADER_MT_ORG_ID (see .env.example)."
        )
    return AsyncModernTreasury(api_key=s.mt_api_key, organization_id=s.mt_org_id)


async def _cmd_list(args: argparse.Namespace) -> None:
    from dataloader.handlers.mt_client import MTClient
    from dataloader.handlers.services.queries.list_resources import call as list_resources_call
    from dataloader.mt_app_links import mt_app_resource_url

    sdk = _client()
    mt = MTClient(sdk)
    meta = _parse_metadata(args.metadata)
    kw: dict = {"limit": args.limit}
    if meta:
        kw["metadata"] = meta
    rows = await list_resources_call(mt, args.kind, **kw)
    for row in rows:
        rid = row.get("id", "")
        url = mt_app_resource_url(args.kind, rid) if rid else None
        if url:
            row = {**row, "mt_app_url": url}
        print(json.dumps(row, default=str))


async def _cmd_ledger_balance(args: argparse.Namespace) -> None:
    sdk = _client()
    acc = await sdk.ledger_accounts.retrieve(
        args.ledger_account_id,
        balances={"effective_at": args.effective_at},
    )
    print(acc.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="MT operational CLI (dataloader).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser(
        "list-by-metadata",
        help="List expected_payment or transaction rows filtered by metadata (AND).",
    )
    p_list.add_argument(
        "kind",
        choices=["expected_payment", "transaction"],
        help="SDK resource type name",
    )
    p_list.add_argument(
        "-m",
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Metadata pair (repeat for multiple keys)",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows (default 100)",
    )
    p_list.set_defaults(_run=_cmd_list)

    p_bal = sub.add_parser(
        "ledger-balance",
        help="Retrieve ledger account with balances as of effective_at (ISO8601).",
    )
    p_bal.add_argument("ledger_account_id", help="Ledger account id (e.g. la_...)")
    p_bal.add_argument(
        "--effective-at",
        required=True,
        help="ISO8601 timestamp (e.g. 2025-05-01T00:00:00Z)",
    )
    p_bal.set_defaults(_run=_cmd_ledger_balance)

    args = parser.parse_args()
    asyncio.run(args._run(args))


if __name__ == "__main__":
    main()
