# Seed Data — Faker Hybrid Engine

This directory contains curated YAML profile data and company-name
templates. The `seed_loader.py` module uses a three-tier approach:

## Tiers

| Tier | Source | YAML needed | Examples |
|------|--------|-------------|----------|
| **Standard** | Pure Faker | None | Unlimited realistic US names/companies |
| **Industry verticals** | Faker + `industry_templates.yaml` | Patterns only (~10/vertical) | tech, government, payroll, manufacturing, property_management, construction |
| **Pop-culture** | Curated YAML | Full profiles | harry_potter, superheroes, seinfeld |

## Files

- `industry_templates.yaml` — company name patterns + industry tags for 6 verticals
- `harry_potter.yaml` — ~85 businesses, ~105 individuals from the Wizarding World
- `superheroes.yaml` — ~100 businesses, ~118 heroes/villains (Marvel + DC)
- `seinfeld.yaml` — ~63 businesses, ~64 characters from the show

## What belongs here

- **Business profiles** — company names, industries, countries
- **Individual profiles** — first/last names for counterparties
- **Industry templates** — name patterns with `{last}`, `{city}`, `{word}` Faker placeholders

## What does NOT belong here

- **Flow patterns** — live inline in JSON configs (`funds_flows` section)
- **Mutation profiles** — inline settings on `GenerationRecipeV1`
- **Edge case configs** — inline settings on `GenerationRecipeV1`

## How profiles are used

The generation pipeline (`generate_from_recipe`) calls `seed_loader.generate_profiles(dataset, count, seed)`,
then `pick_profile(biz, indiv, instance)` merges one business + one individual into a flat dict:

```python
{"business_name": "...", "industry": "...", "country": "US", "first_name": "...", "last_name": "..."}
```

This dict feeds into `deep_format_map()` which substitutes `{first_name}`, `{last_name}`,
`{business_name}`, etc. throughout flows and `instance_resources`.

## Determinism

All generation is seeded: `Faker.seed(recipe.seed)` produces identical output
for the same `(dataset, count, seed)` triple. Curated datasets cycle deterministically
when `count > len(profiles)`.

## Adding new datasets

1. **Industry vertical**: add a new key to `industry_templates.yaml`
2. **Pop-culture**: create `seeds/<name>.yaml` with `business_profiles` + `individual_profiles`
3. Register in `seed_loader._DATASETS` dict
