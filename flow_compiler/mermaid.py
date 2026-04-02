"""Mermaid sequence diagram renderer.

Contains ``MermaidSequenceBuilder``, ``render_mermaid``, and all
participant/arrow resolution helpers.
"""

from __future__ import annotations

from models import ARROW_BY_TYPE, REVERSES_DIRECTION, ActorFrame, ActorSlot, FundsFlowConfig

from .ir import FlowIR, FlowIRStep, _ref_account_type

# ---------------------------------------------------------------------------
# MermaidSequenceBuilder — structured Mermaid syntax generation
# ---------------------------------------------------------------------------


class _FragmentContext:
    """Context manager for Mermaid fragments (opt, break, rect)."""

    def __init__(self, builder: MermaidSequenceBuilder, keyword: str, label: str):
        self._builder = builder
        self._builder._lines.append(f"    {keyword} {label}")

    def __enter__(self) -> _FragmentContext:
        return self

    def __exit__(self, *_: object) -> None:
        self._builder._lines.append("    end")


class _AltContext:
    """Context manager for alt/else fragments."""

    def __init__(self, builder: MermaidSequenceBuilder, label: str):
        self._builder = builder
        self._builder._lines.append(f"    alt {label}")

    def else_(self, label: str) -> None:
        self._builder._lines.append(f"    else {label}")

    def __enter__(self) -> _AltContext:
        return self

    def __exit__(self, *_: object) -> None:
        self._builder._lines.append("    end")


class _BoxContext:
    """Context manager for box participant grouping."""

    def __init__(self, builder: MermaidSequenceBuilder, label: str, color: str | None):
        color_part = f" {color}" if color else ""
        self._builder = builder
        self._builder._lines.append(f"    box{color_part} {label}")

    def __enter__(self) -> _BoxContext:
        return self

    def __exit__(self, *_: object) -> None:
        self._builder._lines.append("    end")


class MermaidSequenceBuilder:
    """Builds Mermaid sequence diagram syntax with full fragment support."""

    def __init__(self, *, autonumber: bool = True):
        self._lines: list[str] = ["sequenceDiagram"]
        if autonumber:
            self._lines.append("    autonumber")

    def participant(self, key: str, display: str) -> MermaidSequenceBuilder:
        self._lines.append(f"    participant {key} as {display}")
        return self

    def box(self, label: str, color: str | None = None) -> _BoxContext:
        return _BoxContext(self, label, color)

    def message(self, src: str, dest: str, text: str, arrow: str = "->>") -> MermaidSequenceBuilder:
        self._lines.append(f"    {src}{arrow}{dest}: {text}")
        return self

    def note_over(self, participants: list[str], text: str) -> MermaidSequenceBuilder:
        joined = ",".join(participants)
        self._lines.append(f"    Note over {joined}: {text}")
        return self

    def opt(self, label: str) -> _FragmentContext:
        return _FragmentContext(self, "opt", label)

    def alt(self, label: str) -> _AltContext:
        return _AltContext(self, label)

    def brk(self, label: str) -> _FragmentContext:
        return _FragmentContext(self, "break", label)

    def rect(self, color: str) -> _FragmentContext:
        return _FragmentContext(self, "rect", color)

    def raw(self, line: str) -> MermaidSequenceBuilder:
        self._lines.append(line)
        return self

    def build(self) -> str:
        return "\n".join(self._lines)


# ---------------------------------------------------------------------------
# Display name helpers
# ---------------------------------------------------------------------------

_DIR_ABBREV: dict[str, str] = {"debit": "DR", "credit": "CR"}

_CURRENCY_SUFFIXES = {"usd", "eur", "gbp", "jpy", "cad", "aud", "usdc", "usdt"}


def _strip_currency_suffix(name: str) -> str:
    """Remove trailing currency tokens: ``"Ops Usd"`` → ``"Ops"``."""
    words = name.split()
    if len(words) > 1 and words[-1].lower() in _CURRENCY_SUFFIXES:
        return " ".join(words[:-1])
    return name


def _normalise_cp(name: str) -> str:
    """Replace Cp/Counterparty tokens with EA."""
    words = name.split()
    cleaned = [("EA" if w.lower() in ("cp", "counterparty") else w) for w in words]
    if cleaned == ["EA"]:
        return "EA"
    return " ".join(cleaned)


def actor_display_name(ref_value: str) -> str:
    """``$ref:internal_account.ops_usd`` → ``Ops``."""
    parts = ref_value.replace("$ref:", "").split(".")
    raw = parts[1] if len(parts) > 1 else parts[0]
    raw = raw.split("[")[0]
    display = raw.replace("_", " ").title()
    return _normalise_cp(_strip_currency_suffix(display))


# ---------------------------------------------------------------------------
# Account-consistent participant resolution helpers (Plan 2 Phase 4)
# ---------------------------------------------------------------------------


def _build_ref_display_map(
    actors: dict[str, ActorFrame],
    customer_name: str = "direct",
) -> dict[str, str]:
    """Build a ``$ref: → display_name`` mapping from actor frames.

    Display name = ``frame.alias + " " + slot_name`` title-cased.
    Single-slot frames omit the slot name if it would be redundant.
    """
    ref_map: dict[str, str] = {}
    for _frame_name, frame in actors.items():
        alias = frame.alias
        if customer_name and customer_name.lower() != "direct":
            alias = alias.replace("Customer", customer_name.title())
        for slot_name, slot in frame.slots.items():
            ref = slot.ref if isinstance(slot, ActorSlot) else slot
            slot_display = slot_name.replace("_", " ").title()
            display = _normalise_cp(_strip_currency_suffix(f"{alias} {slot_display}"))
            ref_map[ref] = display
    return ref_map


def _resolve_actor_display(ref: str, ref_display_map: dict[str, str]) -> str:
    """Given a ``$ref:`` string, return its display name from the pre-built map."""
    return ref_display_map.get(ref, actor_display_name(ref))


def _resolve_ipd_source(ref_display_map: dict[str, str]) -> str:
    """Resolve the source of an incoming payment to a specific external actor."""
    for ref, display in ref_display_map.items():
        if _ref_account_type(ref) == "external_account":
            return display
    return "External"


_LIFECYCLE_REF_FIELDS: dict[str, str] = {
    "return": "returnable_id",
    "reversal": "payment_order_id",
    "transition_ledger_transaction": "ledger_transaction_id",
}


def _find_parent_step(step: FlowIRStep, step_lookup: dict[str, FlowIRStep]) -> FlowIRStep | None:
    """Resolve a lifecycle step back to its parent via payload ref fields."""
    ref_field = _LIFECYCLE_REF_FIELDS.get(step.resource_type)
    if not ref_field:
        return None
    ref_value = step.payload.get(ref_field, "")
    if ref_value.startswith("$ref:"):
        after_dot = ref_value.split(".", 1)[-1] if "." in ref_value else ""
        parts = after_dot.rsplit("__", 1)
        target_id = parts[-1] if parts else ""
    else:
        target_id = ref_value
    return step_lookup.get(target_id) if target_id else None


def _resolve_step_participants(
    step: FlowIRStep,
    ref_display_map: dict[str, str],
    step_lookup: dict[str, FlowIRStep] | None = None,
) -> tuple[str, str]:
    """Return (source, destination) display names for a sequence arrow."""
    rtype = step.resource_type
    payload = step.payload

    if rtype in REVERSES_DIRECTION and step_lookup:
        parent = _find_parent_step(step, step_lookup)
        if parent:
            src, dest = _resolve_step_participants(parent, ref_display_map, step_lookup)
            return (dest, src)
        return ("External", "Internal")

    if rtype == "incoming_payment_detail":
        dest_ref = payload.get("internal_account_id", "")
        dest = _resolve_actor_display(dest_ref, ref_display_map) if dest_ref else "Internal"
        orig_ref = payload.get("originating_account_id", "")
        if orig_ref:
            src = _resolve_actor_display(orig_ref, ref_display_map)
        else:
            src = _resolve_ipd_source(ref_display_map)
        return (src, dest)

    if rtype == "payment_order":
        direction = payload.get("direction", "credit")
        orig_ref = payload.get("originating_account_id", "")
        recv_ref = payload.get("receiving_account_id", "")
        orig = _resolve_actor_display(orig_ref, ref_display_map) if orig_ref else "Internal"
        recv = _resolve_actor_display(recv_ref, ref_display_map) if recv_ref else "External"
        if direction == "debit":
            return (recv, orig)
        return (orig, recv)

    if rtype == "ledger_transaction":
        if step.ledger_groups:
            entries = step.ledger_groups[0].entries
            debit_acct = next(
                (e.get("ledger_account_id", "") for e in entries if e.get("direction") == "debit"),
                "",
            )
            credit_acct = next(
                (e.get("ledger_account_id", "") for e in entries if e.get("direction") == "credit"),
                "",
            )
            src = _resolve_actor_display(debit_acct, ref_display_map) if debit_acct else "Debit"
            dest = _resolve_actor_display(credit_acct, ref_display_map) if credit_acct else "Credit"
            return (src, dest)
        return ("Ledger", "Ledger")

    if rtype == "expected_payment":
        ia_ref = payload.get("internal_account_id", "")
        dest = _resolve_actor_display(ia_ref, ref_display_map) if ia_ref else "Internal"
        orig_ref = payload.get("originating_account_id", "")
        if orig_ref:
            src = _resolve_actor_display(orig_ref, ref_display_map)
        else:
            src = _resolve_ipd_source(ref_display_map)
        return (src, dest)

    if rtype == "transition_ledger_transaction":
        parent = _find_parent_step(step, step_lookup or {})
        if parent and parent.ledger_groups:
            entries = parent.ledger_groups[0].entries
            acct_refs = [e.get("ledger_account_id", "") for e in entries]
            names = [
                _resolve_actor_display(r, ref_display_map) if r else "Ledger" for r in acct_refs
            ]
            if len(names) >= 2:
                return (names[0], names[1])
        return ("Ledger", "Ledger")

    return ("System", "System")


def _classify_participant(ref: str, display_name: str) -> str:
    """Return 'platform', 'ledger', or 'external' for box grouping."""
    acct_type = _ref_account_type(ref)
    if acct_type in ("external_account", "virtual_account"):
        return "external"
    if acct_type == "ledger_account":
        return "ledger"
    if acct_type == "internal_account":
        return "platform"
    if display_name == "External":
        return "external"
    return "platform"


def _collect_participants(
    flow_ir: FlowIR,
    ref_display_map: dict[str, str],
    step_lookup: dict[str, FlowIRStep],
    flow_config: FundsFlowConfig | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build participants and roles from actor frames and step payloads."""
    participants: dict[str, str] = {}
    roles: dict[str, str] = {}

    for ref, display in ref_display_map.items():
        key = display.replace(" ", "")
        participants[key] = display
        roles[key] = _classify_participant(ref, display)

    for step in flow_ir.steps:
        rtype = step.resource_type
        src, dest = _resolve_step_participants(step, ref_display_map, step_lookup)
        src_key = src.replace(" ", "")
        dest_key = dest.replace(" ", "")

        if src_key not in participants:
            participants[src_key] = src
            if rtype in ("ledger_transaction", "transition_ledger_transaction"):
                roles[src_key] = "ledger"
            elif src == "External":
                roles[src_key] = "external"
            else:
                roles.setdefault(src_key, "platform")

        if dest_key not in participants:
            participants[dest_key] = dest
            if rtype in ("ledger_transaction", "transition_ledger_transaction"):
                roles[dest_key] = "ledger"
            elif dest == "External":
                roles[dest_key] = "external"
            else:
                roles.setdefault(dest_key, "platform")

    return participants, roles


def _emit_ledger_note(
    b: MermaidSequenceBuilder,
    step: FlowIRStep,
    dest_key: str,
    show_amounts: bool,
    ref_display_map: dict[str, str],
) -> None:
    """Emit ledger entry notes for a step's ledger groups."""
    for lg in step.ledger_groups:
        if not lg.entries:
            continue
        entry_parts: list[str] = []
        for entry in lg.entries:
            direction = _DIR_ABBREV.get(
                entry.get("direction", ""),
                entry.get("direction", "?").upper()[:2],
            )
            acct = _resolve_actor_display(entry.get("ledger_account_id", ""), ref_display_map)
            amt = entry.get("amount", 0)
            if show_amounts:
                entry_parts.append(f"{direction} {acct} ${amt / 100:,.2f}")
            else:
                entry_parts.append(f"{direction} {acct}")

        acct_refs = [e.get("ledger_account_id", "") for e in lg.entries]
        acct_names = [
            _resolve_actor_display(r, ref_display_map) if r else "Ledger" for r in acct_refs
        ]
        acct_keys = list(dict.fromkeys(n.replace(" ", "") for n in acct_names))
        if len(acct_keys) >= 2:
            note_parts = [acct_keys[0], acct_keys[1]]
        elif acct_keys:
            note_parts = [acct_keys[0]]
        else:
            note_parts = [dest_key]

        note_text = "<br/>".join(entry_parts)
        b.note_over(note_parts, note_text)


def _step_description(step: FlowIRStep, show_amounts: bool) -> str:
    """Build the human-readable description for a step arrow."""
    desc = step.payload.get("description", step.step_id)
    desc = desc.replace(";", ",").replace("#", "").replace("%%", "pct")
    if show_amounts:
        amount = step.payload.get("amount")
        if amount is not None:
            desc += f" ${amount / 100:,.2f}"
    return desc


def _find_payment_anchor(step: FlowIRStep, participant_keys: list[str]) -> str | None:
    """Find a payment participant to anchor a standalone LT note in payments mode."""
    for lg in step.ledger_groups:
        for entry in lg.entries:
            acct = entry.get("ledger_account_id", "")
            if acct:
                display = actor_display_name(acct)
                key = display.replace(" ", "")
                if key in participant_keys:
                    return key
    if participant_keys:
        return participant_keys[0]
    return None


def _emit_mermaid_arrow(
    b: MermaidSequenceBuilder,
    src_key: str,
    dest_key: str,
    desc: str,
    arrow: str,
    participants: dict[str, str],
    payments_mode: bool,
) -> None:
    """Emit a Mermaid arrow, falling back to self-arrow when endpoints are filtered out."""
    src_ok = src_key in participants
    dest_ok = dest_key in participants
    if src_ok and dest_ok:
        b.message(src_key, dest_key, desc, arrow)
    elif src_ok:
        b.note_over([src_key], desc)
    elif dest_ok:
        b.note_over([dest_key], desc)


def render_mermaid(
    flow_ir: FlowIR,
    flow_config: FundsFlowConfig | None = None,
    *,
    customer_name: str = "direct",
    show_amounts: bool = True,
    show_ledger_entries: bool = True,
    show_participant_boxes: bool = True,
    view_mode: str = "ledger",
) -> str:
    """Render a FlowIR instance as a Mermaid sequence diagram."""
    step_lookup: dict[str, FlowIRStep] = {s.step_id: s for s in flow_ir.steps}
    actors = flow_config.actors if flow_config else {}
    ref_display_map = _build_ref_display_map(actors, customer_name)

    og_step_ids: dict[str, str] = {}
    og_exclusion: dict[str, str | None] = {}
    if flow_config:
        for og in flow_config.optional_groups:
            eg = getattr(og, "exclusion_group", None)
            og_exclusion[og.label] = eg
            for s in og.steps:
                og_step_ids[s.step_id] = og.label

    exclusion_to_labels: dict[str, list[str]] = {}
    for label, eg in og_exclusion.items():
        if eg:
            exclusion_to_labels.setdefault(eg, []).append(label)

    participants, roles = _collect_participants(
        flow_ir,
        ref_display_map,
        step_lookup,
        flow_config,
    )

    payments_mode = view_mode == "payments"
    if payments_mode:
        participants = {
            k: v for k, v in participants.items() if roles.get(k) in ("platform", "external")
        }
        roles = {k: v for k, v in roles.items() if k in participants}

    b = MermaidSequenceBuilder()

    if show_participant_boxes:
        platform = [(k, v) for k, v in participants.items() if roles.get(k) == "platform"]
        external = [(k, v) for k, v in participants.items() if roles.get(k) == "external"]
        ledger = [(k, v) for k, v in participants.items() if roles.get(k) == "ledger"]

        if platform:
            with b.box("Platform"):
                for key, display in platform:
                    b.participant(key, display)
        if external:
            with b.box("External"):
                for key, display in external:
                    b.participant(key, display)
        if ledger:
            with b.box("Ledger"):
                for key, display in ledger:
                    b.participant(key, display)
    else:
        for key, display in participants.items():
            b.participant(key, display)

    b.raw("")
    all_part_keys = list(participants.keys())
    if len(all_part_keys) >= 2:
        b.note_over([all_part_keys[0], all_part_keys[-1]], flow_ir.trace_value)
    elif all_part_keys:
        b.note_over([all_part_keys[0]], flow_ir.trace_value)

    current_group: str | None = None
    current_exclusion: str | None = None
    _alt_ctx: _AltContext | None = None
    _opt_ctx: _FragmentContext | None = None

    for step in flow_ir.steps:
        step_group = og_step_ids.get(step.step_id)

        if step_group != current_group:
            if _opt_ctx is not None:
                _opt_ctx.__exit__(None, None, None)
                _opt_ctx = None
            if current_group is not None and current_exclusion is not None:
                new_exclusion = og_exclusion.get(step_group or "") if step_group else None
                if new_exclusion != current_exclusion:
                    if _alt_ctx is not None:
                        _alt_ctx.__exit__(None, None, None)
                        _alt_ctx = None
                    current_exclusion = None

            if step_group is not None:
                eg = og_exclusion.get(step_group)
                if eg and eg in exclusion_to_labels and len(exclusion_to_labels[eg]) > 1:
                    if current_exclusion == eg and _alt_ctx is not None:
                        _alt_ctx.else_(step_group)
                    else:
                        if _alt_ctx is not None:
                            _alt_ctx.__exit__(None, None, None)
                        _alt_ctx = b.alt(step_group)
                        _alt_ctx.__enter__()
                        current_exclusion = eg
                else:
                    _opt_ctx = b.opt(step_group)
                    _opt_ctx.__enter__()
            else:
                current_exclusion = None

            current_group = step_group

        src, dest = _resolve_step_participants(step, ref_display_map, step_lookup)
        src_key = src.replace(" ", "")
        dest_key = dest.replace(" ", "")
        arrow = ARROW_BY_TYPE.get(step.resource_type, "->>")
        desc = _step_description(step, show_amounts)

        if step.resource_type == "transition_ledger_transaction":
            if payments_mode:
                continue
            status = step.payload.get("status", "posted")
            parent = _find_parent_step(step, step_lookup)
            if parent:
                parent_status = parent.payload.get("ledger_status") or "pending"
            else:
                parent_status = "pending"
            b.note_over(
                [src_key, dest_key] if src_key != dest_key else [src_key],
                f"LT {parent_status} → {status}",
            )
        elif payments_mode and step.resource_type == "ledger_transaction":
            anchor = _find_payment_anchor(step, all_part_keys)
            if anchor:
                b.note_over([anchor], f"📒 {desc}")
        elif step_group and step.resource_type in REVERSES_DIRECTION:
            with b.brk(desc):
                _emit_mermaid_arrow(b, src_key, dest_key, desc, arrow, participants, payments_mode)
                if show_ledger_entries and step.ledger_groups and not payments_mode:
                    _emit_ledger_note(b, step, dest_key, show_amounts, ref_display_map)
        else:
            _emit_mermaid_arrow(b, src_key, dest_key, desc, arrow, participants, payments_mode)
            if show_ledger_entries and step.ledger_groups and not payments_mode:
                _emit_ledger_note(b, step, dest_key, show_amounts, ref_display_map)

    if _opt_ctx is not None:
        _opt_ctx.__exit__(None, None, None)
    if _alt_ctx is not None:
        _alt_ctx.__exit__(None, None, None)

    return b.build()
