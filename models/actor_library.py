"""Plan 11a — shared actor library rows (session + ``LoaderDraft``).

Identity fields mirror ``ActorDatasetOverride`` + ``ActorFrame`` inputs used by
generation (``flow_compiler/generation.py``). Bindings map pattern recipe keys
to library ids; see ``dataloader/actor_library_runtime.py`` for hydrate/materialize.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LibraryActorEntry(BaseModel):
    """One row in the operator actor library (stable id for tools + Band 2)."""

    model_config = ConfigDict(extra="forbid")

    library_actor_id: str = Field(
        min_length=1,
        max_length=120,
        description="Slug-like stable id (e.g. legacy:pattern:user_1).",
    )
    label: str = Field(
        default="",
        max_length=200,
        description="Operator-facing short label.",
    )
    frame_type: Literal["user", "direct"] = "user"
    dataset: str | None = None
    entity_type: Literal["business", "individual"] | None = None
    customer_name: str | None = Field(
        default=None,
        max_length=500,
        description="Direct / literal name override.",
    )
    name_template: str | None = Field(default=None, max_length=500)
