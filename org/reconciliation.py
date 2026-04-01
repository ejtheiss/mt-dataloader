"""Reconciliation — match config resources to discovered org resources.

Single-pass reconciliation that runs AFTER faker data injection so
resource names are fully resolved and matchable against the live org.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

from loguru import logger

from engine import all_resources, typed_ref_for
from models import (
    DataLoaderConfig,
    _BaseResourceConfig,
)
from .discovery import (
    DiscoveredCounterparty,
    DiscoveredLegalEntity,
    DiscoveryResult,
    _le_display_name,
)

__all__ = [
    "ReconciledResource",
    "ReconciliationResult",
    "reconcile_config",
    "sync_connection_entities_from_reconciliation",
]


@dataclass
class ReconciledResource:
    config_ref: str
    config_resource: _BaseResourceConfig
    discovered_id: str
    discovered_name: str
    match_reason: str
    use_existing: bool = True
    duplicates: list[dict] | None = None
    child_refs: dict[str, str] = field(default_factory=dict)


@dataclass
class ReconciliationResult:
    matches: list[ReconciledResource] = field(default_factory=list)
    unmatched_config: list[str] = field(default_factory=list)
    unmatched_discovered: list[str] = field(default_factory=list)


def _pick_best_le(candidates: list[DiscoveredLegalEntity]) -> DiscoveredLegalEntity:
    """Prefer active LEs over other statuses when auto-selecting."""
    return next((c for c in candidates if c.status == "active"), candidates[0])


def reconcile_config(
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
) -> ReconciliationResult:
    """Match config-defined resources against discovered org resources.

    Must be called AFTER faker data has been injected (post
    ``generate_from_recipe``) so that resource names are fully resolved
    and matchable against the live org.

    Matching order: connections -> internal accounts -> ledgers ->
    ledger accounts -> ledger account categories -> legal entities ->
    counterparties.  All matchers use list-valued lookups for duplicate
    detection.
    """
    result = ReconciliationResult()
    matched_discovered_ids: set[str] = set()

    # -------------------------------------------------------------------
    # 1. Connections: match entity_id <-> vendor_id + currency overlap
    # -------------------------------------------------------------------
    vendor_id_to_conns: dict[str, list] = {}
    for dc in discovery.connections:
        vendor_id_to_conns.setdefault(dc.vendor_id, []).append(dc)

    config_conn_expected_currencies: dict[str, set[str]] = {}
    for ia in config.internal_accounts or []:
        if ia.connection_id.startswith("$ref:"):
            conn_tref = ia.connection_id[5:]
            config_conn_expected_currencies.setdefault(conn_tref, set()).add(
                ia.currency.upper()
            )

    config_conn_to_discovered: dict[str, str] = {}

    all_conn_options = [
        {
            "id": c.id,
            "name": c.vendor_name,
            "detail": ", ".join(c.currencies) or "no IAs",
        }
        for c in discovery.connections
    ]

    for conn in config.connections or []:
        tref = typed_ref_for(conn)
        candidates = vendor_id_to_conns.get(conn.entity_id, [])
        if candidates:
            expected = config_conn_expected_currencies.get(tref, set())
            if len(candidates) > 1 and expected:
                match = max(
                    candidates,
                    key=lambda c: len(set(c.currencies) & expected),
                )
            else:
                match = candidates[0]
            match_reason = f"entity_id={conn.entity_id}"
            if match.currencies:
                match_reason += f", currencies={','.join(match.currencies)}"
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=conn,
                    discovered_id=match.id,
                    discovered_name=match.vendor_name,
                    match_reason=match_reason,
                    duplicates=all_conn_options,
                )
            )
            config_conn_to_discovered[tref] = match.id
            matched_discovered_ids.add(match.id)
        else:
            # No vendor_id match — prefer **reusing** an existing org connection
            # instead of creating a new sandbox connection (duplicate / incomplete
            # connections often trigger MT 422s like "Connection endpoint must be present").
            expected_curr = config_conn_expected_currencies.get(tref, set())
            all_disc = list(discovery.connections)
            match = None
            match_reason = ""
            if len(all_disc) == 1:
                match = all_disc[0]
                match_reason = (
                    f"fallback: single live connection "
                    f"(config entity_id {conn.entity_id!r} not found on any connection)"
                )
            elif all_disc:
                if expected_curr:
                    best_score = -1
                    for c in all_disc:
                        overlap = len(set(c.currencies) & expected_curr)
                        if overlap > best_score:
                            best_score = overlap
                            match = c
                    if best_score <= 0:
                        match = all_disc[0]
                        match_reason = (
                            f"fallback: first live connection "
                            f"(entity_id {conn.entity_id!r} unmatched; "
                            f"no IA currency overlap on connections)"
                        )
                    else:
                        match_reason = (
                            f"fallback: best currency overlap with internal accounts "
                            f"(entity_id {conn.entity_id!r} unmatched)"
                        )
                else:
                    match = all_disc[0]
                    match_reason = (
                        f"fallback: first live connection "
                        f"(entity_id {conn.entity_id!r} unmatched; no IA currency hints)"
                    )
            if match is not None:
                logger.warning(
                    "Connection {} reconciled to existing org connection {} — {}",
                    tref,
                    match.id[:12],
                    match_reason,
                )
                result.matches.append(
                    ReconciledResource(
                        config_ref=tref,
                        config_resource=conn,
                        discovered_id=match.id,
                        discovered_name=match.vendor_name,
                        match_reason=match_reason,
                        duplicates=all_conn_options,
                    )
                )
                config_conn_to_discovered[tref] = match.id
                matched_discovered_ids.add(match.id)
            else:
                result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # 2. Internal accounts: match name + currency + connection
    # -------------------------------------------------------------------
    disc_ia_by_key: dict[tuple[str, str, str], list] = {}
    for dia in discovery.internal_accounts:
        key = (
            (dia.name or "").strip().lower(),
            dia.currency.upper(),
            dia.connection_id,
        )
        disc_ia_by_key.setdefault(key, []).append(dia)

    for ia in config.internal_accounts or []:
        tref = typed_ref_for(ia)
        conn_ref_value = ia.connection_id
        resolved_conn_id = ""
        if conn_ref_value.startswith("$ref:"):
            config_conn_ref = conn_ref_value[5:]
            resolved_conn_id = config_conn_to_discovered.get(config_conn_ref, "")
        else:
            resolved_conn_id = conn_ref_value

        key = (ia.name.strip().lower(), ia.currency.upper(), resolved_conn_id)
        candidates = disc_ia_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name or c.id[:12],
                        "detail": f"{c.currency}, conn={c.connection_ref or c.connection_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=ia,
                    discovered_id=match.id,
                    discovered_name=match.name or match.id[:12],
                    match_reason=f"name+currency+connection ({ia.name}, {ia.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # 3. Ledgers: match by name
    # -------------------------------------------------------------------
    disc_ledger_by_name: dict[str, list] = {}
    for dl in discovery.ledgers:
        disc_ledger_by_name.setdefault(dl.name.strip().lower(), []).append(dl)

    for ledger in config.ledgers or []:
        tref = typed_ref_for(ledger)
        candidates = disc_ledger_by_name.get(ledger.name.strip().lower(), [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": c.name, "detail": ""}
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=ledger,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name={ledger.name}",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    config_ledger_to_discovered: dict[str, str] = {
        m.config_ref: m.discovered_id
        for m in result.matches
        if m.config_ref.startswith("ledger.")
    }

    # -------------------------------------------------------------------
    # 3b. Ledger Accounts: match name + currency + ledger
    # -------------------------------------------------------------------
    disc_la_by_key: dict[tuple[str, str, str], list] = {}
    for dla in discovery.ledger_accounts:
        key = (dla.name.strip().lower(), dla.currency.upper(), dla.ledger_id)
        disc_la_by_key.setdefault(key, []).append(dla)

    for la_cfg in config.ledger_accounts or []:
        tref = typed_ref_for(la_cfg)
        resolved_ledger_id = ""
        if la_cfg.ledger_id.startswith("$ref:"):
            resolved_ledger_id = config_ledger_to_discovered.get(
                la_cfg.ledger_id[5:], ""
            )
        else:
            resolved_ledger_id = la_cfg.ledger_id

        key = (la_cfg.name.strip().lower(), la_cfg.currency.upper(), resolved_ledger_id)
        candidates = disc_la_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "detail": f"{c.currency}, ledger={c.ledger_ref or c.ledger_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=la_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name+currency+ledger ({la_cfg.name}, {la_cfg.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # 3c. Ledger Account Categories: match name + currency + ledger
    # -------------------------------------------------------------------
    disc_lac_by_key: dict[tuple[str, str, str], list] = {}
    for dlac in discovery.ledger_account_categories:
        key = (dlac.name.strip().lower(), dlac.currency.upper(), dlac.ledger_id)
        disc_lac_by_key.setdefault(key, []).append(dlac)

    for lac_cfg in config.ledger_account_categories or []:
        tref = typed_ref_for(lac_cfg)
        resolved_ledger_id = ""
        if lac_cfg.ledger_id.startswith("$ref:"):
            resolved_ledger_id = config_ledger_to_discovered.get(
                lac_cfg.ledger_id[5:], ""
            )
        else:
            resolved_ledger_id = lac_cfg.ledger_id

        key = (lac_cfg.name.strip().lower(), lac_cfg.currency.upper(), resolved_ledger_id)
        candidates = disc_lac_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "detail": f"{c.currency}, ledger={c.ledger_ref or c.ledger_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=lac_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name+currency+ledger ({lac_cfg.name}, {lac_cfg.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # 4. Legal entities: match by type + name
    # -------------------------------------------------------------------
    disc_le_by_key: dict[tuple[str, str], list] = {}
    for dle in discovery.legal_entities:
        if dle.legal_entity_type == "business":
            key = ("business", (dle.business_name or "").strip().lower())
        elif dle.legal_entity_type == "individual":
            full = f"{dle.first_name or ''} {dle.last_name or ''}".strip().lower()
            key = ("individual", full)
        elif dle.legal_entity_type == "joint":
            name = (
                dle.business_name
                or f"{dle.first_name or ''} {dle.last_name or ''}".strip()
            ).lower()
            key = ("joint", name)
        else:
            continue
        disc_le_by_key.setdefault(key, []).append(dle)

    for le_cfg in config.legal_entities or []:
        tref = typed_ref_for(le_cfg)
        if le_cfg.legal_entity_type == "business":
            key = ("business", (le_cfg.business_name or "").strip().lower())
        elif le_cfg.legal_entity_type == "individual":
            full = f"{le_cfg.first_name or ''} {le_cfg.last_name or ''}".strip().lower()
            key = ("individual", full)
        else:
            key = ("joint", (le_cfg.business_name or "").strip().lower())

        candidates = disc_le_by_key.get(key, [])
        if candidates:
            match = _pick_best_le(candidates)
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": _le_display_name(c), "detail": f"status={c.status}"}
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=le_cfg,
                    discovered_id=match.id,
                    discovered_name=_le_display_name(match),
                    match_reason=f"type+name ({le_cfg.legal_entity_type})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # 5. Counterparties: match by name
    # -------------------------------------------------------------------
    disc_cp_by_name: dict[str, list[DiscoveredCounterparty]] = {}
    for dcp in discovery.counterparties:
        disc_cp_by_name.setdefault((dcp.name or "").strip().lower(), []).append(dcp)

    for cp_cfg in config.counterparties or []:
        tref = typed_ref_for(cp_cfg)
        candidates = disc_cp_by_name.get(cp_cfg.name.strip().lower(), [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": c.name, "detail": f"{c.account_count} accounts"}
                    for c in candidates
                ]
            cp_child_refs: dict[str, str] = {
                f"account[{i}]": aid
                for i, aid in enumerate(match.account_ids)
            }
            if cp_child_refs:
                logger.bind(ref=tref, child_refs=cp_child_refs).info(
                    "Counterparty reconciled with child account refs"
                )
            else:
                logger.bind(
                    ref=tref, account_count=match.account_count,
                    account_ids=match.account_ids,
                ).warning(
                    "Counterparty reconciled but NO child account refs — "
                    "account[0] refs will be unresolvable"
                )
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=cp_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name={cp_cfg.name}",
                    duplicates=dups,
                    child_refs=cp_child_refs,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # -------------------------------------------------------------------
    # Catch-all for reconcilable config resources not yet processed
    # -------------------------------------------------------------------
    reconcilable_types = {
        "connection", "internal_account", "ledger",
        "ledger_account", "ledger_account_category",
        "legal_entity", "counterparty",
    }
    matched_refs = {m.config_ref for m in result.matches}
    unmatched_set = set(result.unmatched_config)
    for res in all_resources(config):
        tref = typed_ref_for(res)
        if res.resource_type not in reconcilable_types:
            continue
        if tref not in matched_refs and tref not in unmatched_set:
            result.unmatched_config.append(tref)
            unmatched_set.add(tref)

    # -------------------------------------------------------------------
    # Unmatched discovered resources
    # -------------------------------------------------------------------
    for dc in discovery.connections:
        if dc.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dc.auto_ref)
    for dia in discovery.internal_accounts:
        if dia.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dia.auto_ref)
    for dl in discovery.ledgers:
        if dl.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dl.auto_ref)
    for dla in discovery.ledger_accounts:
        if dla.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dla.auto_ref)
    for dlac in discovery.ledger_account_categories:
        if dlac.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dlac.auto_ref)
    for dle in discovery.legal_entities:
        if dle.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dle.auto_ref)
    for dcp in discovery.counterparties:
        if dcp.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dcp.auto_ref)

    logger.bind(
        matches=len(result.matches),
        unmatched_config=len(result.unmatched_config),
        unmatched_discovered=len(result.unmatched_discovered),
    ).info("Reconciliation complete")

    return result


_ALLOWED_CONNECTION_ENTITY_IDS = frozenset({"example1", "example2", "modern_treasury"})

_ConnectionEntityId = Literal["example1", "example2", "modern_treasury"]


def sync_connection_entities_from_reconciliation(
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
    reconciliation: ReconciliationResult,
    manual_mappings: dict[str, str] | None = None,
) -> None:
    """Align ``connection.entity_id`` with each chosen MT connection's ``vendor_id``.

    Keeps JSON, drawer payloads, and execution aligned when reconciliation maps
    config connections to discovered org connections (including duplicate-picker
    and manual map flows).
    """
    maps = manual_mappings or {}
    by_id = {dc.id: dc for dc in discovery.connections}
    targets: dict[str, str] = {}

    for m in reconciliation.matches:
        if m.use_existing and m.config_ref.startswith("connection."):
            targets[m.config_ref] = m.discovered_id

    for cref, disc_id in maps.items():
        if cref.startswith("connection.") and disc_id:
            targets[cref] = disc_id

    conns = config.connections
    if not conns:
        return

    for tref, disc_id in targets.items():
        ref_key = tref.split(".", 1)[1] if "." in tref else ""
        if not ref_key:
            continue
        dc = by_id.get(disc_id)
        if dc is None:
            continue
        vid = (dc.vendor_id or "").strip()
        if vid not in _ALLOWED_CONNECTION_ENTITY_IDS:
            logger.warning(
                "Connection {}: skip entity_id sync — vendor_id {!r} not in {}",
                tref,
                vid,
                sorted(_ALLOWED_CONNECTION_ENTITY_IDS),
            )
            continue
        for i, conn in enumerate(conns):
            if conn.ref != ref_key:
                continue
            if conn.entity_id != vid:
                conns[i] = conn.model_copy(
                    update={"entity_id": cast(_ConnectionEntityId, vid)}
                )
            break
