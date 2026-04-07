# Plan: Mandatory actor → preview resolution

**Product context:** Locked decisions in **[`PLAN_NAMES_UNIFIED.md`](PLAN_NAMES_UNIFIED.md) §0.5** — grouped preview actors must show **MT resource names** (`mt_display_name` / `display_name`), not `frame.slot` or slug fallbacks.

## Fundamental belief

**Actor resolution is mandatory.** Every actor slot in a funds flow is a `$ref:` to a resource that the loader already models and that `**build_preview`** turns into a row in `session.preview_items` with a stable `typed_ref`.

**Unresolved actor refs are a bug. Full stop.**  
There is no “optional” name, no silent fallback to slug prettifying, and no parallel naming pipeline for the actor column. If we cannot find a preview row for an actor’s resolved `typed_ref`, something is wrong with expansion, the DAG, preview construction, or session coherence — and we should **surface that failure**, not hide it.

---

## Invariants

1. For each flattened `(alias, ref)` from `flatten_actor_refs(flow.actors)`, `ref` is a non-empty `$ref:type.local…` string consistent with compiled config.
2. After applying the same **typed-ref variant walk** we use elsewhere (longest-first parents, e.g. `counterparty.gc.account[0]` → `counterparty.gc`), **at least one** variant key exists in `preview_by_typed` built from `session.preview_items`.
3. The **display string** for that actor is taken **only** from that preview row: non-empty `mt_display_name`, else `display_name` — matching `templates/partials/preview_resource_row.html` (document this coupling in code comments).

---

## Implementation

### 1. Single source of truth

- In `build_flow_grouped_preview` (or immediately after `build_preview` in the apply path), build once:
`preview_by_typed = { item["typed_ref"]: item for item in (session.preview_items or []) }`
- For each actor, resolve keys from `ref` and take the **first** hit in `preview_by_typed`.
- Set one field on the actor dict (e.g. `preview_name` or reuse `display_label` — pick one name and use it only here + template).

### 2. No duplicate naming logic for actors

- Do **not** use `resolve_resource_display`, `extract_display_name` on config, or `actor_display_name` for this column.
- Keeps Infrastructure / Flow steps / Actors aligned: same row, same two fields.

### 3. Failure policy (unresolved = bug)

When no preview row matches after exhausting variants:


| Layer          | Behavior                                                                                                                                                                                                                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tests**      | Hard fail: `pytest.fail` / `assert` with `ref`, attempted keys, and optionally a short dump of related `typed_ref`s.                                                                                                                                                                                          |
| **Production** | Log **error** (structured: session id, flow ref, actor alias, `ref`, tried keys). UI: show an explicit sentinel (e.g. `! unresolved actor ref`) **or** fail the grouped-preview response so the problem is visible — choose one and document it. **Do not** leave the third column blank without explanation. |


Optional: a small `assert_actors_resolve_to_preview(session)` used in tests and callable from a debug/admin path.

---

## Validation & tests

1. **Unit / integration**: Session with a minimal funds flow + `build_preview` + `build_flow_grouped_preview` → every actor has a matching preview row and the actor field equals `mt_display_name` or `display_name` from that row.
2. **Regression**: If preview omits a resource that the flow references (simulated broken session), the test expects **failure** (not empty string).
3. **GCPay-style multi-instance** (if covered elsewhere): each instance’s actor `ref` resolves to the instance-scoped `typed_ref` present in `preview_items`.

---

## Out of scope / anti-patterns

- Fallback to config-only names when preview is missing (masks the bug).
- New shared “label helper” modules for actors — inline resolution + two-field precedence next to the map is enough.
- Treating blank third column as acceptable when preview tables show the same resource correctly.

---

## Success criteria

- Actor column shows the **exact** same visible name as the matching row in Infrastructure or Flow steps for that `typed_ref`.
- CI fails if any actor cannot be matched to `preview_items`.
- Production logs make unresolved cases obvious for diagnosis.

---

## Follow-ups (only if invariant breaks in the wild)

- Trace **why** `typed_ref` on the preview row differs from actor `ref` (instance expansion, ledger_transaction pseudo-refs, etc.) and fix **that** layer so keys align — not the actor UI.