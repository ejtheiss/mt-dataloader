"""Tests for the Faker hybrid seed engine, deep_format_map,
instance_resources expansion, and the baseline.py currency fix."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_compiler import deep_format_map, _expand_instance_resources, clone_flow, generate_from_recipe
from models import DataLoaderConfig, FundsFlowConfig, GenerationRecipeV1
from seed_loader import generate_profiles, list_datasets, pick_profile

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"
_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# =========================================================================
# Seed loader — list_datasets
# =========================================================================


class TestListDatasets:
    def test_returns_all_ten(self):
        ds = list_datasets()
        assert len(ds) == 10

    def test_all_have_required_keys(self):
        for d in list_datasets():
            assert "name" in d
            assert "label" in d
            assert "tier" in d
            assert d["tier"] in ("faker", "industry", "curated")

    def test_standard_is_present(self):
        names = {d["name"] for d in list_datasets()}
        assert "standard" in names
        assert "harry_potter" in names
        assert "construction" in names


# =========================================================================
# Seed loader — generate_profiles
# =========================================================================


class TestGenerateProfiles:
    def test_standard_deterministic(self):
        b1, i1 = generate_profiles("standard", 50, 42)
        b2, i2 = generate_profiles("standard", 50, 42)
        assert b1 == b2
        assert i1 == i2

    def test_standard_count(self):
        biz, indiv = generate_profiles("standard", 200, 42)
        assert len(biz) == 200
        assert len(indiv) == 200

    def test_industry_has_themed_names(self):
        biz, _ = generate_profiles("construction", 5, 42)
        assert len(biz) == 5
        for b in biz:
            assert b["industry"] in (
                "general_contractor", "electrical", "plumbing",
                "hvac", "concrete", "roofing", "demolition",
            )

    def test_curated_harry_potter(self):
        biz, indiv = generate_profiles("harry_potter", 5, 42)
        assert len(biz) == 5
        names = {b["name"] for b in biz}
        assert "Gringotts Wizarding Bank" in names

    def test_curated_cycles_past_length(self):
        biz, _ = generate_profiles("harry_potter", 200, 42)
        assert len(biz) == 200

    def test_unknown_dataset_raises(self):
        with pytest.raises(ValueError, match="Unknown dataset"):
            generate_profiles("nonexistent", 5, 42)


# =========================================================================
# Seed loader — pick_profile
# =========================================================================


class TestPickProfile:
    def test_merges_correctly(self):
        biz = [{"name": "Acme", "industry": "tech", "country": "US"}]
        indiv = [{"first_name": "Jane", "last_name": "Doe"}]
        p = pick_profile(biz, indiv, 0)
        assert p == {
            "business_name": "Acme",
            "industry": "tech",
            "country": "US",
            "first_name": "Jane",
            "last_name": "Doe",
        }

    def test_cycles(self):
        biz = [{"name": "A", "industry": "x", "country": "US"}, {"name": "B", "industry": "y", "country": "US"}]
        indiv = [{"first_name": "X", "last_name": "Y"}]
        assert pick_profile(biz, indiv, 0)["business_name"] == "A"
        assert pick_profile(biz, indiv, 1)["business_name"] == "B"
        assert pick_profile(biz, indiv, 2)["business_name"] == "A"


# =========================================================================
# deep_format_map
# =========================================================================


class TestDeepFormatMap:
    def test_simple_string(self):
        result = deep_format_map("Hello {name}", {"name": "World"})
        assert result == "Hello World"

    def test_nested_dict(self):
        obj = {"a": {"b": "val_{x}"}, "c": ["{x}", "{y}"]}
        result = deep_format_map(obj, {"x": "1", "y": "2"})
        assert result == {"a": {"b": "val_1"}, "c": ["1", "2"]}

    def test_unknown_placeholder_becomes_empty(self):
        result = deep_format_map("{known} {unknown}", {"known": "yes"})
        assert result == "yes "

    def test_non_string_passthrough(self):
        assert deep_format_map(42, {"x": "y"}) == 42
        assert deep_format_map(None, {"x": "y"}) is None

    def test_ref_strings_get_formatted(self):
        result = deep_format_map("$ref:internal_account.user_{instance}_wallet", {"instance": "0042"})
        assert result == "$ref:internal_account.user_0042_wallet"


# =========================================================================
# _expand_instance_resources
# =========================================================================


class TestExpandInstanceResources:
    def test_basic_expansion(self):
        ir = {
            "legal_entities": [
                {"ref": "user_{instance}", "first_name": "{first_name}", "last_name": "{last_name}"}
            ],
        }
        profile = {"first_name": "Harry", "last_name": "Potter", "business_name": "", "industry": "", "country": "GB"}
        result = _expand_instance_resources(ir, 7, profile)
        assert len(result["legal_entities"]) == 1
        le = result["legal_entities"][0]
        assert le["ref"] == "user_0007"
        assert le["first_name"] == "Harry"
        assert le["last_name"] == "Potter"

    def test_multiple_sections(self):
        ir = {
            "legal_entities": [{"ref": "le_{instance}"}],
            "internal_accounts": [{"ref": "ia_{instance}"}],
        }
        result = _expand_instance_resources(ir, 0, {})
        assert "legal_entities" in result
        assert "internal_accounts" in result


# =========================================================================
# clone_flow with profile
# =========================================================================


class TestCloneFlowProfile:
    def _flow(self):
        return FundsFlowConfig.model_validate({
            "ref": "test",
            "pattern_type": "demo",
            "steps": [
                {"step_id": "s1", "type": "ledger_transaction", "description": "For {first_name}",
                 "ledger_entries": [
                     {"amount": 100, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
                     {"amount": 100, "direction": "credit", "ledger_account_id": "$ref:ledger_account.rev"},
                 ]}
            ],
        })

    def test_profile_substitutes(self):
        flow_dict, _ = clone_flow(self._flow(), 0, {"first_name": "Luna", "last_name": "Lovegood"})
        assert flow_dict["steps"][0]["description"] == "For Luna"

    def test_no_profile_noop(self):
        flow_dict, _ = clone_flow(self._flow(), 0)
        assert "{first_name}" in flow_dict["steps"][0]["description"]

    def test_instance_resources_popped(self):
        flow = FundsFlowConfig.model_validate({
            "ref": "test",
            "pattern_type": "demo",
            "steps": [{"step_id": "s1", "type": "ledger_transaction",
                        "ledger_entries": [
                            {"amount": 100, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
                            {"amount": 100, "direction": "credit", "ledger_account_id": "$ref:ledger_account.rev"},
                        ]}],
            "instance_resources": {"legal_entities": [{"ref": "u_{instance}"}]},
        })
        flow_dict, ir = clone_flow(flow, 5, {"first_name": "X"})
        assert "instance_resources" not in flow_dict
        assert ir is not None
        assert "legal_entities" in ir


# =========================================================================
# Curated YAML file integrity
# =========================================================================


class TestCuratedYamlFiles:
    @pytest.mark.parametrize("name,min_biz,min_indiv", [
        ("harry_potter", 80, 80),
        ("superheroes", 90, 100),
        ("seinfeld", 55, 55),
    ])
    def test_curated_file_counts(self, name, min_biz, min_indiv):
        data = yaml.safe_load((_SEEDS_DIR / f"{name}.yaml").read_text())
        assert len(data["business_profiles"]) >= min_biz
        assert len(data["individual_profiles"]) >= min_indiv

    def test_industry_templates_completeness(self):
        data = yaml.safe_load((_SEEDS_DIR / "industry_templates.yaml").read_text())
        for key in ["tech", "government", "payroll", "manufacturing", "property_management", "construction"]:
            assert key in data
            assert len(data[key]["company_patterns"]) >= 8
            assert len(data[key]["industry_tags"]) >= 5


# =========================================================================
# Example migration validation
# =========================================================================


class TestMigratedExamples:
    @pytest.mark.parametrize("filename", [
        "psp_minimal.json",
        "marketplace_demo.json",
        "staged_demo.json",
        "tradeify.json",
        "funds_flow_demo.json",
    ])
    def test_example_validates(self, filename):
        data = json.loads((_EXAMPLES_DIR / filename).read_text())
        config = DataLoaderConfig.model_validate(data)
        assert len(config.funds_flows) >= 1

    def test_tradeify_has_instance_resources(self):
        data = json.loads((_EXAMPLES_DIR / "tradeify.json").read_text())
        config = DataLoaderConfig.model_validate(data)
        flow = config.funds_flows[0]
        assert flow.instance_resources is not None
        assert "legal_entities" in flow.instance_resources
        assert "ledger_accounts" in flow.instance_resources

    def test_psp_minimal_compiles(self):
        from flow_compiler import AuthoringConfig, compile_to_plan
        data = json.loads((_EXAMPLES_DIR / "psp_minimal.json").read_text())
        config = DataLoaderConfig.model_validate(data)
        raw = config.model_dump_json().encode()
        plan = compile_to_plan(AuthoringConfig.from_json(raw))
        assert len(plan.flow_irs) == 1
        assert len(plan.flow_irs[0].steps) == 1


# =========================================================================
# GenerationRecipeV1 seed_dataset field
# =========================================================================


class TestRecipeSeedDataset:
    def test_default_is_standard(self):
        recipe = GenerationRecipeV1(flow_ref="test", instances=1, seed=42)
        assert recipe.seed_dataset == "standard"

    def test_accepts_curated(self):
        recipe = GenerationRecipeV1(flow_ref="test", instances=1, seed=42, seed_dataset="harry_potter")
        assert recipe.seed_dataset == "harry_potter"


# =========================================================================
# Actor overrides and name_template in generate_from_recipe
# =========================================================================


def _make_actor_flow_config():
    """Minimal flow with two actors (buyer, seller) using name_template."""
    return {
        "funds_flows": [{
            "ref": "actor_test",
            "pattern_type": "demo",
            "actors": {
                "buyer": {
                    "alias": "buyer",
                    "name_template": "{business_name} LLC",
                    "slots": {},
                },
                "seller": {
                    "alias": "seller",
                    "customer_name": "Acme Corp",
                    "slots": {},
                },
            },
            "steps": [
                {
                    "step_id": "lt1",
                    "type": "ledger_transaction",
                    "description": "Pay from {buyer_name} to {seller_name}",
                    "ledger_entries": [
                        {"amount": 100, "direction": "debit", "ledger_account_id": "$ref:ledger_account.cash"},
                        {"amount": 100, "direction": "credit", "ledger_account_id": "$ref:ledger_account.rev"},
                    ],
                },
            ],
        }],
    }


class TestActorNameTemplate:
    def test_name_template_substituted_into_profile(self):
        """buyer_name should be '{business_name} LLC' with faker data."""
        from flow_compiler.generation import _enrich_profile_with_actors, _build_actor_profile_caches
        config_data = _make_actor_flow_config()
        config = DataLoaderConfig.model_validate(config_data)
        pattern = config.funds_flows[0]
        recipe = GenerationRecipeV1(flow_ref="actor_test", instances=1, seed=42)
        biz, indiv = generate_profiles("standard", 1, 42)
        actor_caches = _build_actor_profile_caches(pattern, recipe)
        profile = pick_profile(biz, indiv, 0)
        profile = _enrich_profile_with_actors(
            profile, pattern, recipe, actor_caches, biz, indiv, 0,
        )
        assert profile["buyer_name"].endswith(" LLC")
        assert len(profile["buyer_name"]) > 4
        assert profile["seller_name"] == "Acme Corp"

    def test_name_template_flows_into_step_description(self):
        """clone_flow deep_format_map picks up {buyer_name} and {seller_name}."""
        from flow_compiler.generation import _enrich_profile_with_actors, _build_actor_profile_caches
        config_data = _make_actor_flow_config()
        config = DataLoaderConfig.model_validate(config_data)
        pattern = config.funds_flows[0]
        recipe = GenerationRecipeV1(flow_ref="actor_test", instances=1, seed=42)
        biz, indiv = generate_profiles("standard", 1, 42)
        actor_caches = _build_actor_profile_caches(pattern, recipe)
        profile = pick_profile(biz, indiv, 0)
        profile = _enrich_profile_with_actors(
            profile, pattern, recipe, actor_caches, biz, indiv, 0,
        )
        flow_dict, _ = clone_flow(pattern, 0, profile)
        desc = flow_dict["steps"][0]["description"]
        assert "LLC" in desc
        assert "Acme Corp" in desc
        assert "{buyer_name}" not in desc
        assert "{seller_name}" not in desc


class TestActorDatasetOverride:
    def test_override_uses_different_dataset(self):
        """Actor override dataset produces different names than the global."""
        from models import ActorDatasetOverride
        from flow_compiler.generation import _enrich_profile_with_actors, _build_actor_profile_caches
        config_data = _make_actor_flow_config()
        config = DataLoaderConfig.model_validate(config_data)
        pattern = config.funds_flows[0]
        recipe = GenerationRecipeV1(
            flow_ref="actor_test", instances=3, seed=42,
            seed_dataset="standard",
            actor_overrides={"buyer": ActorDatasetOverride(dataset="harry_potter")},
        )
        biz, indiv = generate_profiles("standard", 3, 42)
        actor_caches = _build_actor_profile_caches(pattern, recipe)
        assert "buyer" in actor_caches
        hp_biz, hp_indiv = actor_caches["buyer"]
        profile = pick_profile(biz, indiv, 0)
        profile = _enrich_profile_with_actors(
            profile, pattern, recipe, actor_caches, biz, indiv, 0,
        )
        assert profile["buyer_business_name"] == hp_biz[0]["name"]

    def test_override_name_template_takes_precedence(self):
        """name_template from override beats name_template from frame."""
        from models import ActorDatasetOverride
        from flow_compiler.generation import _enrich_profile_with_actors, _build_actor_profile_caches
        config_data = _make_actor_flow_config()
        config = DataLoaderConfig.model_validate(config_data)
        pattern = config.funds_flows[0]
        recipe = GenerationRecipeV1(
            flow_ref="actor_test", instances=1, seed=42,
            actor_overrides={"buyer": ActorDatasetOverride(name_template="{business_name} Industries")},
        )
        biz, indiv = generate_profiles("standard", 1, 42)
        actor_caches = _build_actor_profile_caches(pattern, recipe)
        profile = pick_profile(biz, indiv, 0)
        profile = _enrich_profile_with_actors(
            profile, pattern, recipe, actor_caches, biz, indiv, 0,
        )
        assert profile["buyer_name"].endswith(" Industries")
        assert " LLC" not in profile["buyer_name"]
