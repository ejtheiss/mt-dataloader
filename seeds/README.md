# Seed Catalog — Profile Data for Generation

This directory contains curated YAML profile data used to inject
name/identity variety when scaling funds flows ("generate 100 of these").

## What belongs here

- **Business profiles** — company names, industries, countries
- **Individual profiles** — first/last names for counterparties

## What does NOT belong here

- **Flow patterns** — live inline in JSON configs (`funds_flows` section)
- **Mutation profiles** — inline settings on `GenerationRecipeV1`
- **Edge case configs** — inline settings on `GenerationRecipeV1`

## Files

- `seed_catalog.yaml` — single file containing all profile data.

## How profiles are used

The generation pipeline picks profiles by modular cycling:

```python
profile = profiles[instance_index % len(profiles)]
```

For N > profile count, profiles repeat deterministically.

## Why YAML

YAML is easy to review and edit by non-engineers while staying
git-friendly.

## When to change format

If this grows into a large corpus, move to:
- `sqlite` for indexed weighted sampling and fast filtering
- `parquet` for analytical-scale seed sets

Keep YAML as the authoring format and compile it into a runtime
index if needed.
