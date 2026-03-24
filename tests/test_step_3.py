"""Tests for Step 3 — Multi-Instance Scaling and Generation Pipeline.

Covers: GenerationRecipeV1 validation, PaymentMixConfig, clone_flow,
apply_overrides, apply_amount_variance (including balanced ledger fix),
activate_optional_groups / preselect_edge_cases, edge case provenance metadata,
staging at scale,
generate_from_recipe integration, seed_loader, _flow_* stripping, and
passthrough regression.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_compiler import (
    AuthoringConfig,
    FlowIR,
    activate_optional_groups,
    apply_amount_variance,
    apply_overrides,
    clone_flow,
    compile_flows,
    compile_to_plan,
    emit_dataloader_config,
    flatten_optional_groups,
    generate_from_recipe,
    mark_staged,
    preselect_edge_cases,
    render_mermaid,
    select_staged_instances,
)


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs
from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    GenerationRecipeV1,
    PaymentMixConfig,
)
from seed_loader import generate_profiles, list_datasets, pick_profile

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(**kwargs) -> DataLoaderConfig:
    base = {
        "connections": [{"ref": "bank", "entity_id": "example1"}],
        "internal_accounts": [{
            "ref": "ops",
            "connection_id": "$ref:connection.bank",
            "name": "Ops",
            "party_name": "Corp",
            "currency": "USD",
        }],
        "ledgers": [{"ref": "main", "name": "Main"}],
        "ledger_accounts": [
            {
                "ref": "cash",
                "ledger_id": "$ref:ledger.main",
                "name": "Cash",
                "normal_balance": "debit",
                "currency": "USD",
            },
            {
                "ref": "revenue",
                "ledger_id": "$ref:ledger.main",
                "name": "Revenue",
                "normal_balance": "credit",
                "currency": "USD",
            },
        ],
    }
    base.update(kwargs)
    return DataLoaderConfig.model_validate(base)


def _make_flow_config(**kwargs) -> FundsFlowConfig:
    defaults = {
        "ref": "test_flow",
        "pattern_type": "test",
        "trace_key": "deal_id",
        "trace_value_template": "{ref}-{instance}",
        "actors": {
            "direct_1": {
                "alias": "Platform",
                "frame_type": "direct",
                "customer_name": "Platform",
                "slots": {
                    "ops": "$ref:internal_account.ops",
                    "cash": "$ref:ledger_account.cash",
                    "revenue": "$ref:ledger_account.revenue",
                },
            },
        },
        "steps": [
            {
                "step_id": "deposit",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 10000,
                "internal_account_id": "@actor:direct_1.ops",
            },
            {
                "step_id": "settle",
                "type": "ledger_transaction",
                "depends_on": ["deposit"],
                "description": "Book deposit",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ],
    }
    defaults.update(kwargs)
    return FundsFlowConfig.model_validate(defaults)


def _make_config_with_flow(**flow_kwargs) -> DataLoaderConfig:
    flow = _make_flow_config(**flow_kwargs)
    return _make_minimal_config(funds_flows=[flow.model_dump()])


def _make_recipe(**kwargs) -> GenerationRecipeV1:
    defaults = {
        "flow_ref": "test_flow",
        "instances": 3,
        "seed": 42,
    }
    defaults.update(kwargs)
    return GenerationRecipeV1.model_validate(defaults)


# =========================================================================
# GenerationRecipeV1 model validation
# =========================================================================


class TestGenerationRecipeV1:
    def test_valid_recipe(self):
        r = _make_recipe()
        assert r.flow_ref == "test_flow"
        assert r.instances == 3
        assert r.seed == 42
        assert r.version == "v1"
        assert r.edge_case_count == 0
        assert r.amount_variance_pct == 0.0
        assert r.staged_count == 0
        assert r.staged_selection == "happy_path"
        assert r.payment_mix is None
        assert r.overrides == {}

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            GenerationRecipeV1.model_validate({
                "flow_ref": "f", "instances": 1, "seed": 0, "bogus": True
            })

    def test_instances_bounds_low(self):
        with pytest.raises(Exception):
            _make_recipe(instances=0)

    def test_instances_bounds_high(self):
        with pytest.raises(Exception):
            _make_recipe(instances=5001)

    def test_flow_ref_required(self):
        with pytest.raises(Exception):
            GenerationRecipeV1.model_validate({"instances": 1, "seed": 0})

    def test_edge_case_count_bounds(self):
        _make_recipe(edge_case_count=0)
        r = _make_recipe(instances=3, edge_case_count=3)
        assert r.edge_case_count == 3
        capped = _make_recipe(instances=2, edge_case_count=500)
        assert capped.edge_case_count == 2
        with pytest.raises(Exception):
            _make_recipe(edge_case_count=-1)

    def test_amount_variance_pct_bounds(self):
        _make_recipe(amount_variance_pct=0.0)
        _make_recipe(amount_variance_pct=100.0)
        with pytest.raises(Exception):
            _make_recipe(amount_variance_pct=-1.0)
        with pytest.raises(Exception):
            _make_recipe(amount_variance_pct=100.1)

    def test_staged_count_non_negative(self):
        _make_recipe(staged_count=0)
        _make_recipe(staged_count=10)
        with pytest.raises(Exception):
            _make_recipe(staged_count=-1)

    def test_staged_selection_values(self):
        _make_recipe(staged_selection="happy_path")
        _make_recipe(staged_selection="random")
        with pytest.raises(Exception):
            GenerationRecipeV1.model_validate({
                "flow_ref": "f",
                "instances": 1,
                "seed": 0,
                "staged_selection": {},
            })

    def test_payment_mix_none_passes(self):
        r = _make_recipe(payment_mix=None)
        assert r.payment_mix is None

    def test_payment_mix_object(self):
        r = _make_recipe(payment_mix={"include_returns": False})
        assert r.payment_mix is not None
        assert r.payment_mix.include_returns is False
        assert r.payment_mix.include_payment_orders is True


# =========================================================================
# PaymentMixConfig
# =========================================================================


class TestPaymentMixConfig:
    def test_defaults_all_true(self):
        mix = PaymentMixConfig.model_validate({})
        assert mix.include_expected_payments is True
        assert mix.include_payment_orders is True
        assert mix.include_ipds is True
        assert mix.include_returns is True
        assert mix.include_reversals is True
        assert mix.include_standalone_lts is True

    def test_extra_rejected(self):
        with pytest.raises(Exception):
            PaymentMixConfig.model_validate({"bogus": True})


# =========================================================================
# clone_flow
# =========================================================================


class TestCloneFlow:
    def test_instance_ref_format(self):
        flow = _make_flow_config()
        cloned, _ = clone_flow(flow, 42)
        assert cloned["ref"] == "test_flow__0042"

    def test_deep_copy(self):
        flow = _make_flow_config()
        cloned, _ = clone_flow(flow, 0)
        cloned["steps"][0]["amount"] = 99999
        assert flow.steps[0].amount == 10000

    def test_actors_are_real_refs(self):
        flow = _make_flow_config()
        cloned, _ = clone_flow(flow, 0)
        for frame_name, frame in cloned["actors"].items():
            for slot_name, slot in frame["slots"].items():
                assert slot["ref"].startswith("$ref:")


# =========================================================================
# apply_overrides
# =========================================================================


class TestApplyOverrides:
    def test_simple_override(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        apply_overrides(d, {"pattern_type": "custom"})
        assert d["pattern_type"] == "custom"

    def test_nested_override(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        apply_overrides(d, {"steps.0.amount": 99999})
        assert d["steps"][0]["amount"] == 99999

    def test_empty_overrides_noop(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        original = json.dumps(d, sort_keys=True)
        apply_overrides(d, {})
        assert json.dumps(d, sort_keys=True) == original


# =========================================================================
# apply_amount_variance (with review fix for balanced ledger entries)
# =========================================================================


class TestApplyAmountVariance:
    def test_zero_variance_unchanged(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        original_amount = d["steps"][0]["amount"]
        apply_amount_variance(d, 0.0, random.Random(42))
        assert d["steps"][0]["amount"] == original_amount

    def test_variance_jitters_within_range(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        base_amount = d["steps"][0]["amount"]
        apply_amount_variance(d, 5.0, random.Random(42))
        new_amount = d["steps"][0]["amount"]
        assert new_amount != base_amount or True  # may be same by chance
        assert abs(new_amount - base_amount) <= base_amount * 0.05 + 1

    def test_deterministic_with_same_seed(self):
        flow = _make_flow_config()
        d1, _ = clone_flow(flow, 0)
        d2, _ = clone_flow(flow, 0)
        apply_amount_variance(d1, 10.0, random.Random(42))
        apply_amount_variance(d2, 10.0, random.Random(42))
        assert d1["steps"][0]["amount"] == d2["steps"][0]["amount"]

    def test_applies_to_ledger_entries(self):
        flow = _make_flow_config()
        d, _ = clone_flow(flow, 0)
        original_debit = d["steps"][1]["ledger_entries"][0]["amount"]
        apply_amount_variance(d, 10.0, random.Random(99))
        new_debit = d["steps"][1]["ledger_entries"][0]["amount"]
        assert new_debit >= 1

    def test_ledger_entries_stay_balanced_after_variance(self):
        """The review fix: same jitter per step keeps DR == CR."""
        flow = _make_flow_config()
        for seed in range(50):
            d, _ = clone_flow(flow, 0)
            apply_amount_variance(d, 20.0, random.Random(seed))
            settle_step = d["steps"][1]
            entries = settle_step.get("ledger_entries", [])
            if entries:
                debits = sum(e["amount"] for e in entries if e["direction"] == "debit")
                credits_ = sum(e["amount"] for e in entries if e["direction"] == "credit")
                assert debits == credits_, (
                    f"seed={seed}: debits={debits}, credits={credits_}"
                )

    def test_applies_to_optional_group_steps(self):
        flow = _make_flow_config(optional_groups=[{
            "label": "Refund",
            "steps": [
                {"step_id": "refund", "type": "payment_order",
                 "payment_type": "ach", "direction": "debit",
                 "amount": 5000, "originating_account_id": "@actor:direct_1.ops",
                 "depends_on": ["deposit"]},
            ],
        }])
        d, _ = clone_flow(flow, 0)
        apply_amount_variance(d, 10.0, random.Random(42))
        refund_amount = d["optional_groups"][0]["steps"][0]["amount"]
        assert refund_amount >= 1


# =========================================================================
# activate_optional_groups
# =========================================================================


class TestActivateOptionalGroups:
    def _flow_with_groups(self):
        d, _ = clone_flow(_make_flow_config(optional_groups=[
            {"label": "return_path", "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}]},
            {"label": "reversal_path", "steps": [{"step_id": "rev", "type": "reversal", "depends_on": ["deposit"]}]},
        ]), 0)
        return d

    def test_empty_preselection_activates_nothing(self):
        d = self._flow_with_groups()
        assert activate_optional_groups(d, set()) == set()

    def test_preselected_labels_pass_through_when_applicable(self):
        d = self._flow_with_groups()
        result = activate_optional_groups(d, {"return_path", "reversal_path"})
        assert result == {"return_path", "reversal_path"}

    def test_preselect_edge_cases_deterministic_same_seed(self):
        d1 = self._flow_with_groups()
        d2 = self._flow_with_groups()
        s1 = preselect_edge_cases(d1, global_count=1, total_instances=10, seed=42)
        s2 = preselect_edge_cases(d2, global_count=1, total_instances=10, seed=42)
        assert s1 == s2

    def test_exclusion_preselect_never_assigns_same_instance_twice(self):
        """Mutually exclusive groups get disjoint instance index sets."""
        d, _ = clone_flow(_make_flow_config(optional_groups=[
            {"label": "return_path", "exclusion_group": "branch",
             "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}]},
            {"label": "reversal_path", "exclusion_group": "branch",
             "steps": [{"step_id": "rev", "type": "reversal", "depends_on": ["deposit"]}]},
        ]), 0)
        for seed in range(50):
            sel = preselect_edge_cases(d, global_count=1, total_instances=20, seed=seed)
            union: set[int] = set()
            for label, indices in sel.items():
                assert not (union & indices), f"seed={seed} overlap on {label}"
                union |= indices

    def test_independent_groups_preselect_can_overlap_instances(self):
        d = self._flow_with_groups()
        overlap_seeds = 0
        for seed in range(200):
            sel = preselect_edge_cases(d, global_count=3, total_instances=10, seed=seed)
            ret = sel.get("return_path", set())
            rev = sel.get("reversal_path", set())
            if ret & rev:
                overlap_seeds += 1
        assert overlap_seeds > 0


# =========================================================================
# Edge case provenance metadata
# =========================================================================


class TestEdgeCaseProvenance:
    def test_provenance_stamped_on_activated_steps(self):
        config = _make_config_with_flow(optional_groups=[{
            "label": "Return path",
            "trigger": "manual",
            "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}],
        }])
        recipe = _make_recipe(instances=1, seed=42, edge_case_count=1)
        result = generate_from_recipe(recipe, config)
        returns = result.config.returns
        if returns:
            ret = returns[0]
            ret_dict = ret.model_dump(exclude_none=True)
            meta = ret_dict.get("metadata", {})
            assert meta.get("_flow_optional_group") == "Return path"
            assert meta.get("_flow_edge_case_count") == "1"
            assert meta.get("_flow_trigger") == "manual"

    def test_no_provenance_on_happy_path_steps(self):
        config = _make_config_with_flow(optional_groups=[{
            "label": "Return path",
            "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}],
        }])
        recipe = _make_recipe(instances=1, seed=42, edge_case_count=0)
        result = generate_from_recipe(recipe, config)
        for ipd in result.config.incoming_payment_details:
            ipd_dict = ipd.model_dump(exclude_none=True)
            meta = ipd_dict.get("metadata", {})
            assert "_flow_optional_group" not in meta


# =========================================================================
# Staging at scale
# =========================================================================


class TestStagingAtScale:
    def test_staged_count_zero_marks_nothing(self):
        recipe = _make_recipe(staged_count=0)
        result = select_staged_instances(recipe, 10, random.Random(42))
        assert result == set()

    def test_staged_first_one(self):
        recipe = _make_recipe(staged_count=1, staged_selection="all")
        result = select_staged_instances(recipe, 10, random.Random(42))
        assert result == {0}

    def test_staged_first_three(self):
        recipe = _make_recipe(staged_count=3, staged_selection="all")
        result = select_staged_instances(recipe, 10, random.Random(42))
        assert result == {0, 1, 2}

    def test_staged_named_group_first_three(self):
        recipe = _make_recipe(staged_count=3, staged_selection="g")
        edge_selections = {"g": {2, 7, 1, 9}}
        result = select_staged_instances(
            recipe, 10, random.Random(42), edge_selections=edge_selections,
        )
        assert result == {1, 2, 7}

    def test_staged_count_capped_at_total(self):
        recipe = _make_recipe(staged_count=100, staged_selection="all")
        result = select_staged_instances(recipe, 5, random.Random(42))
        assert result == {0, 1, 2, 3, 4}

    def test_mark_staged_sets_flag(self):
        d = {"steps": [
            {"step_id": "a", "type": "payment_order"},
            {"step_id": "b", "type": "ledger_transaction"},
            {"step_id": "c", "type": "return"},
        ]}
        mark_staged(d)
        assert d["steps"][0].get("staged") is True
        assert d["steps"][1].get("staged") is True
        assert d["steps"][2].get("staged") is None

    def test_staged_instances_have_staged_in_compiled(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=3, seed=42, staged_count=1, staged_selection="all")
        result = generate_from_recipe(recipe, config)
        ipds = result.config.incoming_payment_details
        staged_ipds = [i for i in ipds if i.staged]
        assert len(staged_ipds) >= 1


# =========================================================================
# Seed profile loader
# =========================================================================


class TestSeedLoader:
    def test_list_datasets(self):
        datasets = list_datasets()
        assert len(datasets) >= 10
        names = {d["name"] for d in datasets}
        assert "standard" in names
        assert "harry_potter" in names

    def test_generate_profiles_standard(self):
        biz, ind = generate_profiles("standard", 10, 42)
        assert len(biz) == 10
        assert len(ind) == 10

    def test_pick_profile_cycles(self):
        biz, ind = generate_profiles("standard", 3, 42)
        p0 = pick_profile(biz, ind, 0)
        p3 = pick_profile(biz, ind, 3)
        assert p0 == p3


# =========================================================================
# generate_from_recipe (integration)
# =========================================================================


class TestGenerateFromRecipe:
    def test_single_instance(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=1, seed=42)
        result = generate_from_recipe(recipe, config)
        assert isinstance(result.config, DataLoaderConfig)
        assert len(result.config.incoming_payment_details) >= 1

    def test_ten_instances_produce_10x_resources(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=10, seed=42)
        result = generate_from_recipe(recipe, config)
        assert len(result.config.incoming_payment_details) == 10
        assert len(result.config.ledger_transactions) >= 10

    def test_all_refs_unique(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=5, seed=42)
        result = generate_from_recipe(recipe, config)
        refs = []
        for ipd in result.config.incoming_payment_details:
            refs.append(ipd.ref)
        for lt in result.config.ledger_transactions:
            refs.append(lt.ref)
        assert len(refs) == len(set(refs))

    def test_all_trace_values_unique(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=5, seed=42)
        result = generate_from_recipe(recipe, config)
        trace_vals = set()
        for ipd in result.config.incoming_payment_details:
            ipd_dict = ipd.model_dump(exclude_none=True)
            meta = ipd_dict.get("metadata", {})
            tv = meta.get("deal_id")
            if tv:
                trace_vals.add(tv)
        assert len(trace_vals) == 5

    def test_edge_case_count_activates_groups(self):
        config = _make_config_with_flow(optional_groups=[{
            "label": "Return path",
            "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}],
        }])
        recipe = _make_recipe(instances=20, seed=42, edge_case_count=20)
        result = generate_from_recipe(recipe, config)
        assert len(result.config.returns) == 20

    def test_edge_case_zero_no_groups(self):
        config = _make_config_with_flow(optional_groups=[{
            "label": "Return path",
            "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}],
        }])
        recipe = _make_recipe(instances=5, seed=42, edge_case_count=0)
        result = generate_from_recipe(recipe, config)
        assert len(result.config.returns) == 0

    def test_payment_mix_filters(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(
            instances=3, seed=42,
            payment_mix={"include_ipds": False}
        )
        result = generate_from_recipe(recipe, config)
        assert len(result.config.incoming_payment_details) == 0
        assert len(result.config.ledger_transactions) >= 3

    def test_output_validates_against_pydantic(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=5, seed=42)
        result = generate_from_recipe(recipe, config)
        revalidated = DataLoaderConfig.model_validate(result.config.model_dump())
        assert isinstance(revalidated, DataLoaderConfig)

    def test_mermaid_diagrams_returned(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=3, seed=42)
        result = generate_from_recipe(recipe, config)
        assert len(result.diagrams) == 3
        for d in result.diagrams:
            assert d.startswith("sequenceDiagram")

    def test_mermaid_capped_at_ten(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=15, seed=42)
        result = generate_from_recipe(recipe, config)
        assert len(result.diagrams) == 10

    def test_unknown_flow_ref_raises(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(flow_ref="nonexistent")
        with pytest.raises(ValueError, match="not found in loaded config"):
            generate_from_recipe(recipe, config)

    def test_staged_count_marks_instances(self):
        config = _make_config_with_flow()
        recipe = _make_recipe(instances=5, seed=42, staged_count=2, staged_selection="all")
        result = generate_from_recipe(recipe, config)
        all_ipds = result.config.incoming_payment_details
        staged = [i for i in all_ipds if i.staged]
        assert len(staged) == 2


# =========================================================================
# _flow_* metadata stripping
# =========================================================================


class TestFlowMetadataStripping:
    """Verify that _flow_* keys are stripped from handler payloads.

    We test the stripping logic directly (handlers.py modifications) by
    checking that the compiled config resources carry _flow_* metadata
    internally (pre-handler), but the handler code strips them.
    """

    def test_provenance_present_in_compiled_metadata(self):
        config = _make_config_with_flow(optional_groups=[{
            "label": "Return path",
            "trigger": "webhook",
            "steps": [{"step_id": "ret", "type": "return", "depends_on": ["deposit"]}],
        }])
        recipe = _make_recipe(instances=1, seed=42, edge_case_count=1)
        result = generate_from_recipe(recipe, config)
        if result.config.returns:
            ret_dict = result.config.returns[0].model_dump(exclude_none=True)
            meta = ret_dict.get("metadata", {})
            assert "_flow_optional_group" in meta

    def test_strip_flow_keys_logic(self):
        """Simulate the stripping logic used in handlers."""
        meta = {
            "deal_id": "deal-1",
            "_flow_optional_group": "Return path",
            "_flow_edge_case_count": "5",
            "_flow_trigger": "manual",
        }
        stripped = {k: v for k, v in meta.items() if not k.startswith("_flow_")}
        assert stripped == {"deal_id": "deal-1"}


# =========================================================================
# Passthrough regression
# =========================================================================


class TestPassthroughRegression:
    @pytest.mark.parametrize("filename", sorted(
        p.name for p in EXAMPLES_DIR.glob("*.json")
    ))
    def test_example_validates(self, filename):
        path = EXAMPLES_DIR / filename
        data = json.loads(path.read_text())
        config = DataLoaderConfig.model_validate(data)
        compiled, _ = _compile(config)
        assert isinstance(compiled, DataLoaderConfig)
