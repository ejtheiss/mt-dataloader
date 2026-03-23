"""Seed profile loader — profile data only.

Loads business and individual profiles from the seed catalog YAML.
No patterns, mutations, or edge case profiles.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CATALOG_PATH = Path(__file__).parent / "seeds" / "seed_catalog.yaml"
_catalog: dict | None = None


def load_seed_catalog(path: Path | None = None) -> dict:
    global _catalog
    if _catalog is not None and path is None:
        return _catalog
    target = path or _CATALOG_PATH
    with open(target) as f:
        data = yaml.safe_load(f)
    if path is None:
        _catalog = data
    return data


def get_profiles(catalog: dict | None = None) -> tuple[list[dict], list[dict]]:
    cat = catalog or load_seed_catalog()
    return cat.get("business_profiles", []), cat.get("individual_profiles", [])


def pick_profile(profiles: list[dict], instance: int) -> dict:
    return profiles[instance % len(profiles)]
