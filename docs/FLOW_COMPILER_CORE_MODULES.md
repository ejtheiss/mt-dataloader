# Flow compiler core modules (Plan 08 Track A)

This document records how **`compile_flows` / `emit_dataloader_config`** were split for **Plan 08** (compiler / Mermaid scope). The normative product plan lives under the maintainer-local `plan/` tree (e.g. `plan/3.31.26_plans-data_loader/08_compiler_mermaid_scope.md`). This **tracked** note exists so **follow-up plans** and reviewers can map prose (`core/lifecycle.py`) to **actual paths** without assuming a `flow_compiler/core/` package exists yet.

## Why `core_*.py` instead of `flow_compiler/core/`?

Plan 08 §A.3 shows a normative tree:

```text
flow_compiler/core/
  lifecycle.py
  step_compile.py
  emit.py
  …
```

A real **`flow_compiler/core/`** directory with `__init__.py` would make **`import flow_compiler.core`** resolve to that **package**, which **shadows** the existing module **`flow_compiler/core.py`**. That would break stable imports (`from flow_compiler.core import compile_flows`) until a dedicated migration renames the monolith (e.g. facade + package).

Until that migration, extracted pieces live as **sibling modules**:

| Plan name (§A.2 / §A.4) | Implemented module |
| ----------------------- | ------------------ |
| `core/lifecycle.py` (A4-1) | `flow_compiler/core_lifecycle.py` |
| `core/optional_groups.py` (A4-2) | `flow_compiler/core_optional_groups.py` |
| `core/emit.py` (A4-3) | `flow_compiler/core_emit.py` |
| `core/step_compile.py` (A4-4) | `flow_compiler/core_step_compile.py` |
| `compile_pipeline` / thin `compile_flows` | `flow_compiler/core.py` (`compile_flows`, `_compile_one_flow`, phase helpers) |

**Public API:** Callers should keep using **`from flow_compiler import …`** or **`from flow_compiler.core import …`**. The package **`__init__.py`** and **`core.py`** re-export symbols so tests and routers do not need to import `core_*` directly.

## Import cycle note (A4-4)

`core_step_compile._compile_step` calls **`resolve_actors`**, which lives on **`flow_compiler.core`**. A top-level mutual import would cycle while **`core.py`** is loading. The step module uses a **lazy** `import flow_compiler.core` inside **`_resolve_actors`**, and **`core.py`** imports **`_compile_step`** only **after** `resolve_actors` is defined (`# noqa: E402` on that import line).

## Golden snapshots (Plan 08 §A.5 / R1)

| Concern | Test file | What is snapshotted |
| -------- | ---------- | ------------------- |
| IR drift (`FlowIR`) | `tests/test_compile_flows_snapshots.py` | `compile_flows` → `dataclasses.asdict` per flow |
| Emit drift (`DataLoaderConfig`) | `tests/test_emit_dataloader_config_snapshots.py` | `emit_dataloader_config(compile_flows(...), base_config=config)` → JSON round-trip, dict keys sorted for stability |
| Shared fixture list | `tests/compiler_snapshot_examples.py` | Same `*.json` basenames for IR + emit suites |

Emitted configs include **legal entity** sandbox mocks; mock EIN/SSN digits use **`hashlib`-based derivation** in `models/resources/legal_and_foundation.py` (`_mock_nine_digits`) so values are **stable across processes** (Python’s built-in `hash()` is salted per process and broke syrupy reruns).

Update snapshots after intentional output changes:

```bash
pytest --snapshot-update tests/test_compile_flows_snapshots.py
pytest --snapshot-update tests/test_emit_dataloader_config_snapshots.py
```

See **`docs/CONTRIBUTING.md`** (Snapshot tests) for review discipline.

## Mermaid import boundary (Plan 08 R7 / §Inbound)

- **`pass_render_diagrams`** lazy-imports **`render_mermaid`** so **`flow_compiler.pipeline`** does not load Mermaid at import time.
- **`import flow_compiler`** still loads **`mermaid.py`** today because **`flow_compiler.display`** (imported from **`__init__.py`**) imports Mermaid helpers. Achieving a fully Mermaid-free root import would require additional lazy loading in **`display`** / **`__init__`** (future work).

## Related

- **`pyproject.toml`** — `importlinter`: `flow_compiler` must not import `dataloader` app layers.
- **`docs/ARCHITECTURE_NAMING_AND_DISPLAY.md`** — NW-5 / Mermaid checklist (Track C; separate from this split).
