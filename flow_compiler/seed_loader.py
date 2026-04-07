"""Synthetic seed profiles: Faker (standard), YAML industry patterns, curated YAML."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from faker import Faker

__all__ = [
    "actor_subseed",
    "list_datasets",
    "generate_profiles",
    "pick_profile",
    "profile_for",
    "profile_for_split_biz_indiv",
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


def actor_subseed(recipe_seed: int, flow_ref: str, alias: str, instance: int) -> int:
    blob = f"{recipe_seed}\0{flow_ref}\0{alias}\0{instance}".encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def _load_industry_templates() -> dict:
    global _industry_cache
    if _industry_cache is not None:
        return _industry_cache
    with open(_SEEDS_DIR / "industry_templates.yaml") as f:
        _industry_cache = yaml.safe_load(f)
    return _industry_cache


def _load_curated(dataset: str) -> dict:
    if dataset in _curated_cache:
        return _curated_cache[dataset]
    with open(_SEEDS_DIR / f"{dataset}.yaml") as f:
        _curated_cache[dataset] = yaml.safe_load(f)
    return _curated_cache[dataset]


def list_datasets() -> list[dict[str, Any]]:
    return [
        {
            "name": n,
            "label": m["label"],
            "tier": m["tier"],
            "has_individuals": m["tier"] != "industry",
        }
        for n, m in _DATASETS.items()
    ]


def _flat_merge(biz: dict, indiv: dict) -> dict[str, str]:
    return {
        "business_name": biz.get("name", ""),
        "industry": biz.get("industry", ""),
        "country": biz.get("country", "US"),
        "first_name": indiv.get("first_name", ""),
        "last_name": indiv.get("last_name", ""),
    }


def _pair(dataset: str, subseed: int) -> tuple[dict, dict]:
    meta = _DATASETS.get(dataset)
    if meta is None:
        raise ValueError(f"Unknown dataset '{dataset}'. Available: {sorted(_DATASETS.keys())}")
    tier = meta["tier"]
    if tier == "curated":
        data = _load_curated(dataset)
        rb, ri = data["business_profiles"], data["individual_profiles"]
        i = subseed
        return dict(rb[i % len(rb)]), dict(ri[i % len(ri)])
    fake = Faker("en_US")
    if tier == "faker":
        fake.seed_instance(subseed)
        biz = {"name": fake.company(), "industry": "general", "country": "US"}
        fake.seed_instance(subseed + 1_000_000)
        indiv = {"first_name": fake.first_name(), "last_name": fake.last_name()}
        return biz, indiv
    templates = _load_industry_templates()
    vertical = templates.get(dataset, {})
    patterns = vertical.get("company_patterns", ["{last} Inc."])
    tags = vertical.get("industry_tags", ["general"])
    pattern = patterns[subseed % len(patterns)]
    industry = tags[subseed % len(tags)]
    fake.seed_instance(subseed)
    name = pattern.format(
        last=fake.last_name(),
        city=fake.city(),
        word=fake.word().title(),
    )
    biz = {"name": name, "industry": industry, "country": "US"}
    fake.seed_instance(subseed + 1_000_000)
    indiv = {"first_name": fake.first_name(), "last_name": fake.last_name()}
    return biz, indiv


def profile_for(dataset: str, subseed: int) -> dict[str, str]:
    return _flat_merge(*_pair(dataset, subseed))


def profile_for_split_biz_indiv(biz_dataset: str, indiv_dataset: str, subseed: int) -> dict[str, str]:
    if biz_dataset == indiv_dataset:
        return profile_for(biz_dataset, subseed)
    sb = int.from_bytes(
        hashlib.sha256(f"biz:{biz_dataset}\0{subseed}".encode()).digest()[:8], "big"
    )
    si = int.from_bytes(
        hashlib.sha256(f"ind:{indiv_dataset}\0{subseed}".encode()).digest()[:8], "big"
    )
    biz, _ = _pair(biz_dataset, sb)
    _, indiv = _pair(indiv_dataset, si)
    return _flat_merge(biz, indiv)


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
        businesses.append({"name": name, "industry": tags[i % len(tags)], "country": "US"})
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
            f"Expected business_profiles and individual_profiles in {_SEEDS_DIR / f'{dataset}.yaml'}"
        )
    businesses = [raw_biz[i % len(raw_biz)] for i in range(count)]
    individuals = [raw_indiv[i % len(raw_indiv)] for i in range(count)]
    return businesses, individuals


def generate_profiles(
    dataset: str = "standard",
    count: int = 100,
    seed: int = 424242,
) -> tuple[list[dict], list[dict]]:
    meta = _DATASETS.get(dataset)
    if meta is None:
        raise ValueError(f"Unknown dataset '{dataset}'. Available: {sorted(_DATASETS.keys())}")
    tier = meta["tier"]
    if tier == "faker":
        return _generate_standard(count, seed)
    if tier == "industry":
        return _generate_industry(dataset, count, seed)
    if tier == "curated":
        return _generate_curated(dataset, count, seed)
    raise ValueError(f"Unknown tier '{tier}' for dataset '{dataset}'")


def pick_profile(
    business_profiles: list[dict],
    individual_profiles: list[dict],
    instance: int,
) -> dict:
    biz = business_profiles[instance % len(business_profiles)] if business_profiles else {}
    indiv = individual_profiles[instance % len(individual_profiles)] if individual_profiles else {}
    return _flat_merge(biz, indiv)
