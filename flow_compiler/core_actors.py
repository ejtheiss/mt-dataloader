"""Actor frames → ``@actor:`` resolution and trace template expansion (Plan 08 A4-5)."""

from __future__ import annotations

from typing import Any

from models import ActorFrame, ActorSlot


def flatten_actor_refs(actors: dict[str, ActorFrame]) -> dict[str, str]:
    """Build a flat ``frame.slot → $ref:`` mapping from actor frames.

    Used by ``resolve_actors`` and Mermaid rendering to translate
    ``@actor:frame.slot`` references into concrete ``$ref:`` strings.
    """
    flat: dict[str, str] = {}
    for frame_name, frame in actors.items():
        for slot_name, slot in frame.slots.items():
            ref = slot.ref if isinstance(slot, ActorSlot) else slot
            flat[f"{frame_name}.{slot_name}"] = ref
    return flat


def resolve_actors(obj: Any, actor_refs: dict[str, str]) -> Any:
    """Replace ``@actor:frame.slot`` references with concrete ``$ref:`` values.

    ``actor_refs`` is a pre-flattened map from ``flatten_actor_refs``.
    """
    if isinstance(obj, str) and obj.startswith("@actor:"):
        key = obj[7:]
        if key not in actor_refs:
            raise ValueError(f"Unknown actor ref '{key}' — available: {sorted(actor_refs.keys())}")
        return actor_refs[key]
    if isinstance(obj, dict):
        return {k: resolve_actors(v, actor_refs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_actors(v, actor_refs) for v in obj]
    return obj


def expand_trace_value(
    template: str,
    ref: str,
    instance: int,
    profile: dict[str, str] | None = None,
) -> str:
    from collections import defaultdict

    mapping: dict[str, Any] = {"ref": ref, "instance": instance}
    if profile:
        mapping.update(profile)
    try:
        return template.format_map(defaultdict(str, mapping))
    except (ValueError, KeyError) as e:
        raise ValueError(f"Bad placeholder in trace metadata template '{template}': {e}") from e
