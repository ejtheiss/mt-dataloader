# Seed Catalog for Pattern Expansion

This directory contains curated YAML seed data for generating high-volume, pattern-based transaction scenarios.

## Files

- `seed_catalog.yaml`: **single file** containing all seed data — business profiles, individual profiles, flow patterns, mutation profiles, edge cases, and recommended compositions. Consolidating into one file simplifies loading, validation, and versioning.

## When to split

If any single section grows past ~500 lines, split that section into its own file and reference it from the catalog. Until then, one file is simpler.

## Why YAML

YAML is easy to review and edit by non-engineers while staying git-friendly.

## When to change format

If this grows into a large corpus, move to:
- `sqlite` for indexed weighted sampling and fast filtering
- `parquet` for analytical-scale seed sets

Keep YAML as the authoring format and compile it into a runtime index if needed.
