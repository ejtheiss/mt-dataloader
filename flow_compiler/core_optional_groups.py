"""Optional-group flattening: merge activated group steps into the main steps list."""

from __future__ import annotations


def flatten_optional_groups(flow_dict: dict, activated_groups: set[str] | None = None) -> dict:
    """Merge activated optional group steps into the main steps list.

    If activated_groups is None, ALL groups are included
    (for preview/documentation rendering). If it's an empty set,
    none are included (happy-path only).

    Respects ``position`` and ``insert_after``:
    - ``"after"`` + no anchor -> append to end (default)
    - ``"after"`` + ``insert_after: "X"`` -> insert after step X
    - ``"before"`` + no anchor -> prepend to start
    - ``"before"`` + ``insert_after: "X"`` -> insert before step X
    - ``"replace"`` + ``insert_after: "X"`` -> remove step X, insert
      group steps in its place, rewrite downstream depends_on

    Mutates and returns flow_dict. Removes the optional_groups key.
    """
    optional_groups = flow_dict.pop("optional_groups", [])
    steps: list[dict] = flow_dict.setdefault("steps", [])

    for og in optional_groups:
        if activated_groups is not None and og["label"] not in activated_groups:
            continue

        position = og.get("position", "after")
        anchor = og.get("insert_after")
        og_steps = og["steps"]

        if position == "replace" and anchor:
            anchor_idx = next(
                (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                None,
            )
            if anchor_idx is not None:
                steps.pop(anchor_idx)
                for j, new_step in enumerate(og_steps):
                    steps.insert(anchor_idx + j, new_step)
                last_new_id = og_steps[-1].get("step_id")
                if last_new_id:
                    for s in steps:
                        deps = s.get("depends_on")
                        if deps and anchor in deps:
                            s["depends_on"] = [last_new_id if d == anchor else d for d in deps]
            else:
                steps.extend(og_steps)

        elif position == "before":
            if anchor:
                anchor_idx = next(
                    (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                    None,
                )
                if anchor_idx is not None:
                    for j, new_step in enumerate(og_steps):
                        steps.insert(anchor_idx + j, new_step)
                else:
                    steps.extend(og_steps)
            else:
                for j, new_step in enumerate(og_steps):
                    steps.insert(j, new_step)

        else:
            if anchor:
                anchor_idx = next(
                    (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                    None,
                )
                if anchor_idx is not None:
                    for j, new_step in enumerate(og_steps):
                        steps.insert(anchor_idx + 1 + j, new_step)
                else:
                    steps.extend(og_steps)
            else:
                steps.extend(og_steps)

    return flow_dict
