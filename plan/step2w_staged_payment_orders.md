# Step 2 — Staged Resources (Model + Engine)

## Scope

All model and engine changes for staged resources. Four resource types support
staging — payment orders, incoming payment details, expected payments, and
ledger transactions. After this step:

- `PaymentOrderConfig`, `IncomingPaymentDetailConfig`, `ExpectedPaymentConfig`,
  and `LedgerTransactionConfig` each have `staged: bool = Field(default=False, exclude=True)`
- `StagedEntry` dataclass exists in `models.py`
- `RunManifest` has `resources_staged` list, with `record_staged()`, `_to_dict()`, and `load()` support
- `engine.py` `create_one()` skips API call for staged resources, saves resolved payloads
- `runs/<run_id>_staged.json` written at run completion with fully-resolved payloads
- `dry_run()` validates that no non-staged resource depends on a staged resource
- Existing configs with no `staged` fields work identically (default `False`)

Everything is testable via the existing UI and curl before any new templates.

### Why these four types

| Type | Demo value | Fire complexity | Notes |
|------|-----------|----------------|-------|
| **Payment order** | High — "now I'll send this payment" *click* | Simple — one API call | Core use case |
| **Incoming payment detail** | Very high — "watch the deposit land" *click* | Medium — needs `_poll_ipd_status` for completion + child ref harvest (`transaction_id`, `ledger_transaction_id`) | IPD handler already polls; fire endpoint reuses the same logic |
| **Expected payment** | Medium-high — "I'll create the EP, then we'll see it reconcile" *click* | Simple — one API call | Nothing depends on EPs |
| **Ledger transaction** | High — "posting this journal entry" *click* | Simple — one API call | Watch for `ledgerable_id` refs to staged POs |

### What does NOT get `staged`

Connections, legal entities, internal accounts, counterparties, external
accounts, virtual accounts, ledgers, ledger accounts, ledger account
categories, returns, reversals, category memberships, nested categories.
These are infrastructure — staging them would break downstream refs and
serves no demo purpose.

### Fire dispatch (Step 3 concern, noted here)

The Step 3 fire endpoint cannot hardcode `client.payment_orders.create()`.
It needs a dispatch table by `resource_type`:

```python
_FIRE_DISPATCH = {
    "payment_order":          lambda c, r, **kw: c.payment_orders.create(**r, **kw),
    "expected_payment":       lambda c, r, **kw: c.expected_payments.create(**r, **kw),
    "ledger_transaction":     lambda c, r, **kw: c.ledger_transactions.create(**r, **kw),
    "incoming_payment_detail": lambda c, r, **kw: c.incoming_payment_details.create_async(**r, **kw),
}
```

IPDs use `create_async` (not `create`) and require polling afterward — the
fire endpoint reuses `_poll_ipd_status` from `handlers.py` and registers
child refs (`transaction_id`, `ledger_transaction_id`) into the webhook
correlation index. Polling is used (not webhooks) per project requirements.

---

## 2.1 `staged` Field on Four Config Types

### The same one-line change on each class

```python
    staged: bool = Field(default=False, exclude=True)
```

Added to:

| Class | Location in `models.py` | Insert after |
|-------|------------------------|--------------|
| `PaymentOrderConfig` | line ~870 | `line_items` field |
| `IncomingPaymentDetailConfig` | line ~904 | `description` field |
| `ExpectedPaymentConfig` | line ~828 | `ledger_transaction` field |
| `LedgerTransactionConfig` | line ~925 | `ledgerable_id` field |

### Why `exclude=True` is mandatory

`resolve_refs()` calls `config.model_dump(exclude_none=True)` (engine.py
line 142). Without `exclude=True`, the resolved dict would contain
`"staged": false` (or `true`). This gets passed as `**resolved` to the
SDK create method, which fails because no MT SDK method accepts a `staged`
keyword argument.

This mirrors `depends_on` on `_BaseResourceConfig` (models.py line 180):

```python
    depends_on: list[RefStr] = Field(
        default_factory=list,
        exclude=True,
        ...
    )
```

Both are loader-internal fields that must never reach the API.

### Why `False` default

Existing configs have no `staged` key. Pydantic fills in `False`, so all
existing resources execute normally. Only resources with explicit
`"staged": true` in the JSON are held back.

### JSON config usage

```json
{
  "incoming_payment_details": [
    {
      "ref": "ipd_buyer_deposit",
      "type": "ach",
      "direction": "credit",
      "amount": 13750000,
      "internal_account_id": "$ref:internal_account.buyer_maya_wallet",
      "staged": true
    }
  ],
  "payment_orders": [
    {
      "ref": "po_collect_fee",
      "type": "book",
      "amount": 412500,
      "direction": "credit",
      "originating_account_id": "$ref:internal_account.buyer_maya_wallet",
      "receiving_account_id": "$ref:internal_account.revenue",
      "depends_on": ["$ref:incoming_payment_detail.ipd_buyer_deposit"],
      "staged": true
    }
  ],
  "expected_payments": [
    {
      "ref": "ep_inbound_wire",
      "amount_lower_bound": 100000,
      "amount_upper_bound": 200000,
      "direction": "credit",
      "internal_account_id": "$ref:internal_account.buyer_owen_wallet",
      "reconciliation_rule_variables": [{"key":"k","val":"v"}],
      "staged": true
    }
  ],
  "ledger_transactions": [
    {
      "ref": "lt_manual_entry",
      "ledger_entries": [
        {"amount": 5000, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
        {"amount": 5000, "direction": "credit", "ledger_account_id": "$ref:ledger_account.revenue"}
      ],
      "staged": true
    }
  ]
}
```

### IPD-specific note: child refs at fire time

When `IncomingPaymentDetailConfig` is staged, its resolved payload is saved
like any other staged resource. But when **fired** (Step 3), the fire endpoint
must:
1. Call `client.incoming_payment_details.create_async(**resolved)`
2. Poll via `_poll_ipd_status()` until completed (same handler logic)
3. Harvest `transaction_id` and `ledger_transaction_id` from the completed IPD
4. Register those child refs in the webhook correlation index

This is a Step 3 fire endpoint concern. The engine skip logic in Step 2 is
identical for all four types — `getattr(resource, 'staged', False)`.

### IPD-specific note: staged ordering between IPDs and POs

In the marketplace demo, `po_collect_fee` depends on `ipd_buyer_deposit` via
`depends_on`. If both are staged, **fire ordering matters**: the IPD must be
fired and completed before the PO can fire (the PO's resolved payload already
has the correct account UUIDs, but the demo narrative requires the IPD to
settle first). The Step 3 UI should indicate this dependency — e.g. disable
the PO's fire button until the IPD is fired. The dry-run validator (2.6)
does NOT block this (both are staged), so ordering is enforced in the UI only.

### LedgerTransaction-specific note: `ledgerable_id` refs

A `LedgerTransactionConfig` can have `ledgerable_id: "$ref:payment_order.po_x"`.
If `po_x` is staged, the ledger transaction can't resolve that ref. The
dry-run validator (2.6) catches this: "Resource 'ledger_transaction.lt_y'
depends on staged resource 'payment_order.po_x'." The user must either
un-stage the PO or also stage the ledger transaction.

---

## 2.2 `StagedEntry` Dataclass

### Where in codebase

`models.py` lines 1095-1102, after `FailedEntry`:

```python
@dataclass(frozen=True)
class FailedEntry:
    typed_ref: str
    error: str
    failed_at: str
```

### Change

Add after `FailedEntry`:

```python
@dataclass(frozen=True)
class StagedEntry:
    """Resource resolved but not sent to API — staged for manual fire during demo."""

    resource_type: str
    typed_ref: str
    staged_at: str
```

### Why frozen

Matches `ManifestEntry` and `FailedEntry` — all manifest entries are immutable
once recorded.

---

## 2.3 `RunManifest` Changes

### Where in codebase

`engine.py` lines 326-409 — the `RunManifest` dataclass.

### Changes

**a) Add `resources_staged` field:**

```python
@dataclass
class RunManifest:
    run_id: str
    config_hash: str
    started_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None
    status: str = "running"
    resources_created: list[ManifestEntry] = field(default_factory=list)
    resources_failed: list[FailedEntry] = field(default_factory=list)
    resources_staged: list[StagedEntry] = field(default_factory=list)  # NEW
```

**b) Add `record_staged()` method:**

```python
    def record_staged(self, typed_ref: str, resource_type: str) -> None:
        self.resources_staged.append(
            StagedEntry(
                resource_type=resource_type,
                typed_ref=typed_ref,
                staged_at=_now_iso(),
            )
        )
```

**c) Update `_to_dict()` to serialize staged entries:**

Add after the `resources_failed` block:

```python
            "resources_staged": [
                {
                    "resource_type": s.resource_type,
                    "typed_ref": s.typed_ref,
                    "staged_at": s.staged_at,
                }
                for s in self.resources_staged
            ],
```

**d) Update `load()` to deserialize staged entries:**

Add after the `resources_failed` loop:

```python
        for staged_data in data.get("resources_staged", []):
            manifest.resources_staged.append(StagedEntry(**staged_data))
```

### Import

`StagedEntry` must be imported in `engine.py` alongside the existing model
imports:

```python
from models import (
    DataLoaderConfig,
    FailedEntry,
    HandlerResult,
    ManifestEntry,
    StagedEntry,       # NEW
    _BaseResourceConfig,
)
```

---

## 2.4 Engine Skip Logic in `create_one()`

### Where in codebase

`engine.py` lines 461-500 — the `create_one()` inner function inside
`execute()`.

### Current flow

```
create_one(typed_ref, _batch):
  1. emit_sse("creating", ...)
  2. resolve_refs(resource, registry)
  3. handler_dispatch[resource_type](resolved, ...)  ← API call
  4. registry.register(typed_ref, created_id)
  5. on_resource_created callback
  6. manifest.record(ManifestEntry)
  7. manifest.write()
  8. emit_sse("created", ...)
```

### New flow (staged branch inserted after step 2)

```
create_one(typed_ref, _batch):
  1. emit_sse("creating", ...)
  2. resolve_refs(resource, registry)
  ── if staged: ──
  3s. staged_payloads[typed_ref] = resolved
  4s. manifest.record_staged(typed_ref, resource_type)
  5s. manifest.write(runs_dir)    ← incremental write for crash recovery
  6s. emit_sse("staged", typed_ref, {})
  7s. return  ← skip API call, skip registry, skip on_resource_created
  ── else (normal): ──
  3. handler call → result
  4. registry.register
  5. on_resource_created callback
  6. manifest.record + write
  7. emit_sse("created")
```

### Change

The `staged_payloads` dict is declared before the batch loop, at the same
level as `manifest` and `batch_index`. After `resolve_refs` but before the
handler call, check `getattr(resource, 'staged', False)`:

```python
    staged_payloads: dict[str, dict] = {}  # typed_ref -> resolved payload

    # ... inside create_one(), after resolve_refs:

                async with semaphore:
                    resolved = resolve_refs(resource, registry)

                    if getattr(resource, "staged", False):
                        staged_payloads[typed_ref] = resolved
                        manifest.record_staged(typed_ref, resource.resource_type)
                        manifest.write(runs_dir)
                        await emit_sse("staged", typed_ref, {})
                        return

                    handler = handler_dispatch[resource.resource_type]
                    result = await handler(
                        resolved,
                        idempotency_key=f"{run_id}:{typed_ref}",
                        typed_ref=typed_ref,
                    )
```

### Why `manifest.write()` is mandatory here (review fix #3)

Normal resources call `manifest.write(runs_dir)` after every
`manifest.record()` (engine.py line 495) for crash recovery — if the
process dies mid-run, the on-disk manifest reflects all resources created
so far. Without the same pattern for staged resources, a crash after
staging some resources but before run completion would lose the staged
entries from the on-disk manifest. The run detail page would then show
no staged resources, even though the engine had processed them.

### Why `getattr` not direct attribute access

Four config types (`PaymentOrderConfig`, `IncomingPaymentDetailConfig`,
`ExpectedPaymentConfig`, `LedgerTransactionConfig`) have `staged`. Other
resource types (connections, counterparties, internal accounts, etc.) don't.
`getattr(resource, 'staged', False)` safely returns `False` for all
non-stageable resources. If `staged` is extended to additional types in the
future, no engine change is needed — just add the field to the config class.

### Why the staged branch is inside `async with semaphore`

The semaphore guards `resolve_refs` which reads from the registry (shared
state). The staged branch only adds to `staged_payloads` (a local dict) and
calls `emit_sse` (already used inside the semaphore). Keeping it inside avoids
restructuring the existing code.

### Why no `registry.register()` for staged resources

Staged POs have no `created_id` — the API was never called. Registering a
placeholder would silently produce invalid UUIDs in downstream refs. The
dry-run validator (2.6) ensures no non-staged resource depends on a staged
one, so the missing registration never causes a runtime `KeyError`.

---

## 2.5 Staged Payload Persistence

### What

Write `runs/<run_id>_staged.json` at run completion. Contains fully-resolved
payloads (all `$ref:` strings replaced with UUIDs) ready for direct SDK calls
from the fire endpoint (Step 3).

### Where in codebase

`engine.py` lines 523-525, the run completion block:

```python
    manifest.finalize("completed")
    manifest.write(runs_dir)
    return manifest
```

### Change

After `manifest.write(runs_dir)`, if there are staged payloads:

```python
    manifest.finalize("completed")
    manifest.write(runs_dir)

    if staged_payloads:
        staged_path = Path(runs_dir) / f"{run_id}_staged.json"
        staged_path.write_text(
            json.dumps(staged_payloads, indent=2, default=str),
            encoding="utf-8",
        )

    return manifest
```

### File format

```json
{
  "incoming_payment_detail.ipd_buyer_deposit": {
    "type": "ach",
    "direction": "credit",
    "amount": 13750000,
    "internal_account_id": "uuid-of-buyer-maya-wallet"
  },
  "payment_order.po_collect_fee": {
    "type": "book",
    "amount": 412500,
    "direction": "credit",
    "originating_account_id": "uuid-of-buyer-maya-wallet",
    "receiving_account_id": "uuid-of-revenue-account"
  },
  "expected_payment.ep_inbound_wire": {
    "amount_lower_bound": 100000,
    "amount_upper_bound": 200000,
    "direction": "credit",
    "internal_account_id": "uuid-of-buyer-owen-wallet"
  },
  "ledger_transaction.lt_manual_entry": {
    "ledger_entries": [
      {"amount": 5000, "direction": "debit", "ledger_account_id": "uuid-of-cash"},
      {"amount": 5000, "direction": "credit", "ledger_account_id": "uuid-of-revenue"}
    ]
  }
}
```

Keys are typed refs (e.g. `incoming_payment_detail.ipd_buyer_deposit`). Values
are the exact dicts that would be passed to the corresponding SDK create method
(`client.incoming_payment_details.create_async(**value)`, etc.). No further ref
resolution needed at fire time.

The fire endpoint (Step 3) reads `resource_type` from the key prefix to route
to the correct SDK method via the dispatch table.

### Why at run completion, not incrementally

Unlike `manifest.write()` which is called after every resource (for crash
recovery), staged payloads only matter after the run completes successfully.
If the run fails partway, staged payloads are irrelevant. Writing once at the
end is simpler and sufficient.

### Why disk, not just memory

The server may restart between run completion and the demo. Disk persistence
means the fire endpoint can load payloads from a previous session. The file
is small (~200 bytes per PO) and inspectable for debugging.

---

## 2.6 Staged Dependency Validator

### Problem (three cases)

Staged resources skip the API call and never register a `created_id` in
the `RefRegistry`. Any `$ref:` in a data field that points at a staged
resource (or its child refs) will crash `resolve_refs()` with `KeyError`
at execution time. The validator must catch **all three** failure modes:

1. **Non-staged → staged (direct ref)**: Non-staged resource B has
   `$ref:payment_order.po_x` and `po_x` is staged. `resolve_refs(B)`
   crashes because `po_x` has no `created_id`.

2. **Non-staged → staged (child ref)**: Non-staged LT has
   `$ref:incoming_payment_detail.ipd_x.transaction_id` and `ipd_x` is
   staged. The exact string `incoming_payment_detail.ipd_x.transaction_id`
   doesn't match `incoming_payment_detail.ipd_x` in `staged_refs` — the
   **original validator missed this** because it used exact string matching.
   Must check the parent ref (`type.key`) of child refs (`type.key.child`).

3. **Staged → staged (data-field ref)**: Staged LT has
   `ledgerable_id: "$ref:payment_order.po_x"` and `po_x` is also staged.
   The **original validator skipped staged resources entirely**
   (`if ref in staged_refs: continue`). But `resolve_refs()` runs
   **before** the staged check in `create_one()` — so it still needs a
   real `created_id` in the registry. `depends_on` between staged resources
   is fine (ordering only, no registry lookup), but data-field `$ref:`
   between staged resources is **not** — it causes `KeyError`.

### Solution

Two-pass validation in `dry_run()`:
- **Pass 1 (staged resources)**: Check data-field refs only (not
  `depends_on`). If any data-field ref points to another staged resource
  or a child of one, reject.
- **Pass 2 (non-staged resources)**: Check ALL deps (data-field refs +
  `depends_on`). If any dep points to a staged resource or a child of one,
  reject.

Both passes use a `_dep_hits_staged()` helper that checks direct match
AND parent-ref match (mirroring `_is_known_or_child` already in
`dry_run()`).

### Where in codebase

`engine.py` `dry_run()` function, lines 233-296. The validation checks
already exist for unresolvable refs (lines 266-283). The staged check goes
after them, before building the batch list.

### Change

After the existing ref validation loops and before `batches: list[list[str]]`:

```python
    # -- Staged dependency validation --
    staged_refs = {
        ref
        for ref, resource in resource_map.items()
        if getattr(resource, "staged", False)
    }
    if staged_refs:

        def _dep_hits_staged(dep: str) -> str | None:
            """Return the staged ref that `dep` conflicts with, or None."""
            if dep in staged_refs:
                return dep
            parts = dep.split(".")
            if len(parts) >= 3:
                parent = f"{parts[0]}.{parts[1]}"
                if parent in staged_refs:
                    return parent
            return None

        for ref, resource in resource_map.items():
            if ref in staged_refs:
                # Staged resources: check DATA field refs only.
                # depends_on between staged resources is fine (ordering
                # only). Data field $ref: requires a registry entry,
                # which staged resources don't have.
                for dep in extract_ref_dependencies(resource):
                    hit = _dep_hits_staged(dep)
                    if hit:
                        raise ValueError(
                            f"Staged resource '{ref}' has a data-field "
                            f"$ref to staged resource '{hit}' (via "
                            f"'{dep}'). Data-field refs between staged "
                            f"resources cannot resolve at execution time "
                            f"because staged resources have no created_id. "
                            f"Either un-stage '{hit}' or remove the $ref."
                        )
            else:
                # Non-staged resources: check ALL deps.
                all_deps = extract_ref_dependencies(resource)
                for dep_str in resource.depends_on:
                    if dep_str.startswith("$ref:"):
                        all_deps.add(dep_str[5:])
                for dep in all_deps:
                    hit = _dep_hits_staged(dep)
                    if hit:
                        raise ValueError(
                            f"Resource '{ref}' depends on staged resource "
                            f"'{hit}' (via '{dep}'). Either un-stage "
                            f"'{hit}' or also stage '{ref}'."
                        )

    batches: list[list[str]] = []
```

### Why `_dep_hits_staged()` checks parent refs (review fix #1)

Child refs like `incoming_payment_detail.ipd_x.transaction_id` have three
dot-separated parts. The parent ref is `incoming_payment_detail.ipd_x`.
Exact string matching against `staged_refs` misses this — the child ref
string is different from the parent ref string. The helper splits on `.`
and checks if the `type.key` prefix is in `staged_refs`.

This mirrors the existing `_is_known_or_child()` helper (engine.py lines
254-264) used in the unresolvable-ref validation.

### Why staged resources are NOT skipped (review fix #2)

The original plan had `if ref in staged_refs: continue` — skipping staged
resources entirely. This is wrong because `resolve_refs()` in
`create_one()` runs **before** the staged check:

```python
# engine.py create_one() — staged branch
async with semaphore:
    resolved = resolve_refs(resource, registry)  # ← runs FIRST
    if getattr(resource, "staged", False):       # ← checked AFTER
```

If staged resource A has `$ref:` to staged resource B in a data field,
`resolve_refs(A)` looks up B in the registry. B is staged → no registry
entry → `KeyError`. The original plan said "staged → staged is fine
because both resolve against already-created infrastructure." This is only
true when the dependency is via `depends_on` (ordering, no data lookup).
Data-field `$ref:` always requires a registry entry.

### Edge cases (corrected)

- **Staged resource `depends_on` staged resource**: **Allowed.** `depends_on`
  is ordering only — `build_dag()` adds it as a DAG edge, but
  `resolve_refs()` ignores it (it has `exclude=True`). The validator only
  checks data-field refs for staged→staged conflicts.
- **Staged resource data-field `$ref:` to staged resource**: **Rejected.**
  `resolve_refs()` needs a `created_id` in the registry. Clear error
  message tells the user to un-stage the dependency or remove the `$ref:`.
- **Non-staged resource depends on staged resource (any path)**: **Rejected.**
  Whether via data-field `$ref:` or `depends_on`, the non-staged resource
  cannot execute correctly if the staged resource has no `created_id`.
- **Non-staged resource references child ref of staged resource**: **Rejected.**
  e.g. `$ref:incoming_payment_detail.ipd_x.transaction_id` where `ipd_x`
  is staged. The parent-ref check catches this.
- **Staged resource depends on non-staged resource**: **Allowed.** Normal
  case. Non-staged resource creates first (DAG ordering), then staged
  resource resolves its refs (getting real UUIDs) and is held back.
- **Staged IPD — child refs at fire time**: No child refs are registered
  during the run (the API is never called). The fire endpoint (Step 3)
  calls `create_async()`, polls to completion, and registers child refs
  (`transaction_id`, `ledger_transaction_id`).
- **Staged EP — no downstream impact**: Nothing typically depends on an EP,
  so staging is always safe.

### Where this validation runs

`dry_run()` is called from:
1. `validate()` in `main.py` (line 438) — initial validation
2. `revalidate()` in `main.py` (line 544) — re-validation after JSON edit
3. `validate_json()` in `main.py` (line 339) — programmatic API

All three paths get the staged dependency check for free.

### Tests

**Test A — non-staged reversal targeting staged PO (direct ref):**

```bash
curl -s -X POST http://localhost:8000/api/validate-json \
  -H 'Content-Type: application/json' \
  -d '{
    "connections": [{"ref": "conn", "entity_id": "example1"}],
    "internal_accounts": [{"ref": "ia", "name": "Test", "party_name": "Test", "connection_id": "$ref:connection.conn", "currency": "USD"}],
    "payment_orders": [
      {"ref": "po1", "type": "ach", "amount": 100, "direction": "credit", "originating_account_id": "$ref:internal_account.ia", "staged": true}
    ],
    "reversals": [
      {"ref": "rev1", "payment_order_id": "$ref:payment_order.po1", "reason": "duplicate"}
    ]
  }'
# Expected: {"valid": false, "errors": [...message mentions "depends on staged resource 'payment_order.po1'"...]}
```

**Test B — non-staged LT referencing child ref of staged IPD:**

```bash
curl -s -X POST http://localhost:8000/api/validate-json \
  -H 'Content-Type: application/json' \
  -d '{
    "connections": [{"ref": "conn", "entity_id": "example1"}],
    "internal_accounts": [{"ref": "ia", "name": "Test", "party_name": "Test", "connection_id": "$ref:connection.conn", "currency": "USD"}],
    "ledgers": [{"ref": "main", "name": "Main"}],
    "ledger_accounts": [
      {"ref": "cash", "name": "Cash", "ledger_id": "$ref:ledger.main", "normal_balance": "debit"},
      {"ref": "rev", "name": "Revenue", "ledger_id": "$ref:ledger.main", "normal_balance": "credit"}
    ],
    "incoming_payment_details": [
      {"ref": "ipd1", "type": "ach", "direction": "credit", "amount": 50000, "internal_account_id": "$ref:internal_account.ia", "staged": true}
    ],
    "ledger_transactions": [
      {"ref": "lt1", "ledgerable_type": "transaction", "ledgerable_id": "$ref:incoming_payment_detail.ipd1.transaction",
       "ledger_entries": [
         {"amount": 50000, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
         {"amount": 50000, "direction": "credit", "ledger_account_id": "$ref:ledger_account.rev"}
       ]}
    ]
  }'
# Expected: {"valid": false, "errors": [...message mentions "depends on staged resource 'incoming_payment_detail.ipd1'"...]}
# (child ref 'incoming_payment_detail.ipd1.transaction' triggers parent match)
```

**Test C — staged LT with data-field ref to staged PO (staged→staged):**

```bash
curl -s -X POST http://localhost:8000/api/validate-json \
  -H 'Content-Type: application/json' \
  -d '{
    "connections": [{"ref": "conn", "entity_id": "example1"}],
    "internal_accounts": [{"ref": "ia", "name": "Test", "party_name": "Test", "connection_id": "$ref:connection.conn", "currency": "USD"}],
    "ledgers": [{"ref": "main", "name": "Main"}],
    "ledger_accounts": [
      {"ref": "cash", "name": "Cash", "ledger_id": "$ref:ledger.main", "normal_balance": "debit"},
      {"ref": "rev", "name": "Revenue", "ledger_id": "$ref:ledger.main", "normal_balance": "credit"}
    ],
    "payment_orders": [
      {"ref": "po1", "type": "ach", "amount": 100, "direction": "credit", "originating_account_id": "$ref:internal_account.ia", "staged": true}
    ],
    "ledger_transactions": [
      {"ref": "lt1", "ledgerable_type": "payment_order", "ledgerable_id": "$ref:payment_order.po1",
       "ledger_entries": [
         {"amount": 100, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
         {"amount": 100, "direction": "credit", "ledger_account_id": "$ref:ledger_account.rev"}
       ], "staged": true}
    ]
  }'
# Expected: {"valid": false, "errors": [...message mentions "Staged resource 'ledger_transaction.lt1' has a data-field $ref to staged resource 'payment_order.po1'"...]}
```

**Test D — staged PO `depends_on` staged IPD (should PASS):**

```bash
curl -s -X POST http://localhost:8000/api/validate-json \
  -H 'Content-Type: application/json' \
  -d '{
    "connections": [{"ref": "conn", "entity_id": "example1"}],
    "internal_accounts": [
      {"ref": "ia1", "name": "Wallet1", "party_name": "Test", "connection_id": "$ref:connection.conn", "currency": "USD"},
      {"ref": "ia2", "name": "Wallet2", "party_name": "Test", "connection_id": "$ref:connection.conn", "currency": "USD"}
    ],
    "incoming_payment_details": [
      {"ref": "ipd1", "type": "ach", "direction": "credit", "amount": 50000, "internal_account_id": "$ref:internal_account.ia1", "staged": true}
    ],
    "payment_orders": [
      {"ref": "po1", "type": "book", "amount": 50000, "direction": "credit",
       "originating_account_id": "$ref:internal_account.ia1",
       "receiving_account_id": "$ref:internal_account.ia2",
       "depends_on": ["$ref:incoming_payment_detail.ipd1"], "staged": true}
    ]
  }'
# Expected: {"valid": true, ...}
# (depends_on between staged resources is ordering-only — allowed)
```

---

## Design Decision: Why `staged` stays on individual types (not `_BaseResourceConfig`)

An alternative approach (review suggestion #10) is to add `staged` to
`_BaseResourceConfig` so all 16+ types get it. This eliminates `getattr`
in the engine and validator.

**Rejected.** `_BaseResourceConfig` has `extra="forbid"`. If `staged` is
on the base class, `"staged": true` on a `ConnectionConfig` or
`LegalEntityConfig` is **silently accepted** by Pydantic — it's a known
field, not "extra." The engine would then try to stage a connection
(nonsensical), and the Step 3 fire dispatch table wouldn't have a handler
for it. With `staged` on 4 individual types only, `extra="forbid"`
naturally **rejects** `"staged": true` on non-stageable types with a
clear Pydantic validation error at parse time. The `getattr` in the engine
and validator is a small style cost for free type safety.

---

## File-Level Summary

| File | Changes | Lines |
|------|---------|-------|
| `models.py` | `staged` field on `PaymentOrderConfig`, `IncomingPaymentDetailConfig`, `ExpectedPaymentConfig`, `LedgerTransactionConfig`; `StagedEntry` dataclass | +13 |
| `engine.py` | Import `StagedEntry`, `resources_staged` + `record_staged()` on `RunManifest`, `_to_dict()`/`load()` updates, staged skip in `create_one()` (with `manifest.write()`), `staged_payloads` dict + persistence, two-pass staged dep validator in `dry_run()` | +70 |

**Total: ~83 new lines across 2 files.**

---

## Review Fixes Incorporated

| Review # | Issue | Fix |
|----------|-------|-----|
| 1 | Validator misses child-ref deps on staged resources | `_dep_hits_staged()` checks parent ref via `.split(".")` |
| 2 | Validator misses staged→staged data-field deps | Two-pass: staged resources check data-field refs (not `depends_on`); no longer skipped |
| 3 | Missing `manifest.write()` after `record_staged()` | Added `manifest.write(runs_dir)` in staged branch of `create_one()` |
| 10 | Move `staged` to `_BaseResourceConfig` | Rejected — `extra="forbid"` provides free type safety on individual types |

---

## Task Checklist

- [ ] 2.1a — `staged: bool = Field(default=False, exclude=True)` on `PaymentOrderConfig`
- [ ] 2.1b — `staged: bool = Field(default=False, exclude=True)` on `IncomingPaymentDetailConfig`
- [ ] 2.1c — `staged: bool = Field(default=False, exclude=True)` on `ExpectedPaymentConfig`
- [ ] 2.1d — `staged: bool = Field(default=False, exclude=True)` on `LedgerTransactionConfig`
- [ ] 2.2 — `StagedEntry` dataclass in `models.py`
- [ ] 2.3 — `RunManifest` changes: `resources_staged`, `record_staged()`, `_to_dict()`, `load()`
- [ ] 2.4 — Staged skip logic in `create_one()` with `manifest.write()` (review fix #3)
- [ ] 2.5 — `staged_payloads` dict + `_staged.json` persistence
- [ ] 2.6 — Two-pass staged dep validator in `dry_run()` with `_dep_hits_staged()` (review fixes #1, #2)

### Verification sequence

1. Existing configs (no `staged` field) work identically — run a demo, verify normal execution
2. Config with `staged: true` on each of the four types validates successfully
3. Staged resources show as "staged" in SSE output during execution (not "created")
4. `runs/<run_id>.json` manifest has `resources_staged` array with correct `resource_type` values
5. `runs/<run_id>_staged.json` has fully-resolved payloads with real UUIDs for all staged types
6. Non-staged reversal targeting staged PO → fails dry-run (Test A)
7. Non-staged LT referencing child ref of staged IPD → fails dry-run (Test B)
8. Staged LT with data-field `$ref:` to staged PO → fails dry-run (Test C)
9. Staged PO `depends_on` staged IPD → passes validation (Test D)
10. Config where non-staged LT has `ledgerable_id` pointing to staged PO fails validation
11. Staged IPD payload in `_staged.json` includes resolved `internal_account_id` UUID
12. Kill server mid-run after staging — restart — manifest on disk includes staged entries
