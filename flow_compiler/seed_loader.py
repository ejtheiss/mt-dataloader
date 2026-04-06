"""Seed profile generation engine — Faker hybrid approach.

Three tiers:
  1. "standard" — pure Faker (zero YAML)
  2. Industry verticals — Faker names + YAML company-name patterns
  3. Pop-culture — curated YAML (Harry Potter, superheroes, Seinfeld)

All tiers support deterministic seeding for reproducibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from faker import Faker

__all__ = [
    "list_datasets",
    "generate_profiles",
    "pick_profile",
]

_SEEDS_DIR = Path(__file__).parent / "seeds"

_DATASETS: dict[str, dict[str, str]] = {
    "standard": {"label": "Standard", "tier": "faker"},
    "tech": {"label": "Tech / SaaS", "tier": "industry"},
    "government": {"label": "Government", "tier": "industry"},
    "payroll": {"label": "Payroll / HR", "tier": "industry"},
    "manufacturing": {"label": "Manufacturing", "tier": "industry"},
    "property_management": {"label": "Property Management", "tier": "industry"},
    "construction": {"label": "Construction", "tier": "industry"},
    "harry_potter": {"label": "Harry Potter", "tier": "curated"},
    "superheroes": {"label": "Superheroes", "tier": "curated"},
    "seinfeld": {"label": "Seinfeld", "tier": "curated"},
}

_industry_cache: dict | None = None
_curated_cache: dict[str, dict] = {}


def _load_industry_templates() -> dict:
    global _industry_cache
    if _industry_cache is not None:
        return _industry_cache
    path = _SEEDS_DIR / "industry_templates.yaml"
    with open(path) as f:
        _industry_cache = yaml.safe_load(f)
    return _industry_cache


def _load_curated(dataset: str) -> dict:
    if dataset in _curated_cache:
        return _curated_cache[dataset]
    path = _SEEDS_DIR / f"{dataset}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    _curated_cache[dataset] = data
    return data


def list_datasets() -> list[dict[str, Any]]:
    """Return metadata for all available seed datasets."""
    result = []
    for name, meta in _DATASETS.items():
        result.append(
            {
                "name": name,
                "label": meta["label"],
                "tier": meta["tier"],
                "has_individuals": meta["tier"] != "industry",
            }
        )
    return result


def _generate_standard(count: int, seed: int) -> tuple[list[dict], list[dict]]:
    fake = Faker("en_US")
    Faker.seed(seed)
    businesses = [
        {"name": fake.company(), "industry": "general", "country": "US"} for _ in range(count)
    ]
    Faker.seed(seed + 1_000_000)
    individuals = [
        {"first_name": fake.first_name(), "last_name": fake.last_name()} for _ in range(count)
    ]
    return businesses, individuals


def _generate_industry(dataset: str, count: int, seed: int) -> tuple[list[dict], list[dict]]:
    templates = _load_industry_templates()
    vertical = templates.get(dataset, {})
    patterns = vertical.get("company_patterns", ["{last} Inc."])
    tags = vertical.get("industry_tags", ["general"])

    fake = Faker("en_US")
    Faker.seed(seed)
    businesses: list[dict] = []
    for i in range(count):
        pattern = patterns[i % len(patterns)]
        name = pattern.format(
            last=fake.last_name(),
            city=fake.city(),
            word=fake.word().title(),
        )
        industry = tags[i % len(tags)]
        businesses.append({"name": name, "industry": industry, "country": "US"})

    Faker.seed(seed + 1_000_000)
    individuals = [
        {"first_name": fake.first_name(), "last_name": fake.last_name()} for _ in range(count)
    ]
    return businesses, individuals


def _generate_curated(dataset: str, count: int, seed: int) -> tuple[list[dict], list[dict]]:
    data = _load_curated(dataset)
    raw_biz = data.get("business_profiles", [])
    raw_indiv = data.get("individual_profiles", [])

    if not raw_biz or not raw_indiv:
        raise ValueError(
            f"Curated dataset '{dataset}' has no profiles. "
            f"Expected business_profiles and individual_profiles in "
            f"{_SEEDS_DIR / f'{dataset}.yaml'}"
        )

    businesses = [raw_biz[i % len(raw_biz)] for i in range(count)]
    individuals = [raw_indiv[i % len(raw_indiv)] for i in range(count)]
    return businesses, individuals


def generate_profiles(
    dataset: str = "standard",
    count: int = 100,
    seed: int = 424242,
) -> tuple[list[dict], list[dict]]:
    """Generate (business_profiles, individual_profiles) for a dataset.

    Returns deterministic output for the same (dataset, count, seed).
    """
    meta = _DATASETS.get(dataset)
    if meta is None:
        raise ValueError(f"Unknown dataset '{dataset}'. Available: {sorted(_DATASETS.keys())}")

    tier = meta["tier"]
    if tier == "faker":
        return _generate_standard(count, seed)
    elif tier == "industry":
        return _generate_industry(dataset, count, seed)
    elif tier == "curated":
        return _generate_curated(dataset, count, seed)
    else:
        raise ValueError(f"Unknown tier '{tier}' for dataset '{dataset}'")


def pick_profile(
    business_profiles: list[dict],
    individual_profiles: list[dict],
    instance: int,
) -> dict:
    """Merge a business + individual profile for instance i.

    Returns flat dict with keys: business_name, industry, country,
    first_name, last_name.
    """
    biz = business_profiles[instance % len(business_profiles)] if business_profiles else {}
    indiv = individual_profiles[instance % len(individual_profiles)] if individual_profiles else {}
    return {
        "business_name": biz.get("name", ""),
        "industry": biz.get("industry", ""),
        "country": biz.get("country", "US"),
        "first_name": indiv.get("first_name", ""),
        "last_name": indiv.get("last_name", ""),
    }
