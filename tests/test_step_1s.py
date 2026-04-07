"""Tests for step 1s: schema models, compiler gate, seed catalog."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
import yaml

from dataloader.engine import (
    RefRegistry,
    all_resources,
    inject_legal_entity_psp_connection_id,
    typed_ref_for,
)
from dataloader.helpers import build_preview
from flow_compiler import AuthoringConfig, compile_to_plan
from models import (
    VALID_STEP_TYPES,
    CounterpartyAccountConfig,
    DataLoaderConfig,
    DisplayPhase,
    FundsFlowConfig,
    FundsFlowScaleConfig,
    FundsFlowStepConfig,
    IncomingPaymentDetailConfig,
    IncomingPaymentDetailStep,
    ReturnConfig,
    ReturnStep,
    ReversalStep,
)
from models.sandbox import SANDBOX_WALLET_DEMO_ADDRESSES
from models.shared import WalletAccountNumberType


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs


def _ipd(**overrides):
    """Minimal valid IPD step dict for flow construction."""
    d = {
        "step_id": "s1",
        "type": "incoming_payment_detail",
        "payment_type": "ach",
        "amount": 1000,
        "internal_account_id": "ia_ops",
    }
    d.update(overrides)
    return d


def _lt(**overrides):
    """Minimal valid LT step dict for flow construction."""
    d = {
        "step_id": "s1",
        "type": "ledger_transaction",
        "ledger_entries": [
            {"amount": 100, "direction": "debit", "ledger_account_id": "la_a"},
            {"amount": 100, "direction": "credit", "ledger_account_id": "la_b"},
        ],
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Typed Step Models (Plan 0: Discriminated Union)
# ---------------------------------------------------------------------------


class TestTypedStepModels:
    def test_ipd_requires_payment_type(self):
        with pytest.raises(Exception, match="payment_type"):
            FundsFlowStepConfig(
                step_id="dep",
                type="incoming_payment_detail",
                amount=5000,
                internal_account_id="ia",
            )

    def test_ipd_requires_internal_account(self):
        with pytest.raises(Exception, match="internal_account_id"):
            FundsFlowStepConfig(
                step_id="dep",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=5000,
            )

    def test_po_requires_direction(self):
        with pytest.raises(Exception, match="direction"):
            FundsFlowStepConfig(
                step_id="pay",
                type="payment_order",
                payment_type="ach",
                amount=5000,
                originating_account_id="ia",
            )

    def test_lt_requires_entries(self):
        with pytest.raises(Exception, match="ledger_entries"):
            FundsFlowStepConfig(step_id="lt", type="ledger_transaction")

    def test_tlt_requires_status(self):
        with pytest.raises(Exception, match="status"):
            FundsFlowStepConfig(
                step_id="t",
                type="transition_ledger_transaction",
            )

    def test_valid_ipd(self):
        step = FundsFlowStepConfig(
            step_id="dep",
            type="incoming_payment_detail",
            payment_type="ach",
            amount=50000,
            internal_account_id="$ref:internal_account.ops",
        )
        assert step.step_id == "dep"
        assert step.type == "incoming_payment_detail"
        assert isinstance(step, IncomingPaymentDetailStep)

    def test_ipd_direction_defaults_to_credit(self):
        step = FundsFlowStepConfig(
            step_id="dep",
            type="incoming_payment_detail",
            payment_type="ach",
            amount=1000,
            internal_account_id="ia",
        )
        assert step.direction == "credit"

    def test_ipd_rejects_debit_direction(self):
        with pytest.raises(Exception):
            FundsFlowStepConfig(
                step_id="dep",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=1000,
                internal_account_id="ia",
                direction="debit",
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception, match="Extra inputs"):
            FundsFlowStepConfig(
                step_id="dep",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=1000,
                internal_account_id="ia",
                bogus_field="nope",
            )

    def test_unbalanced_ledger_entries(self):
        with pytest.raises(ValueError, match="unbalanced"):
            FundsFlowStepConfig(
                step_id="bad_lt",
                type="ledger_transaction",
                ledger_entries=[
                    {
                        "amount": 100,
                        "direction": "debit",
                        "ledger_account_id": "$ref:ledger_account.cash",
                    },
                    {
                        "amount": 200,
                        "direction": "credit",
                        "ledger_account_id": "$ref:ledger_account.revenue",
                    },
                ],
            )

    def test_balanced_ledger_entries(self):
        step = FundsFlowStepConfig(
            step_id="settle",
            type="ledger_transaction",
            ledger_entries=[
                {
                    "amount": 50000,
                    "direction": "debit",
                    "ledger_account_id": "$ref:ledger_account.cash",
                },
                {
                    "amount": 50000,
                    "direction": "credit",
                    "ledger_account_id": "$ref:ledger_account.revenue",
                },
            ],
        )
        assert len(step.ledger_entries) == 2

    def test_invalid_step_type(self):
        with pytest.raises(Exception):
            FundsFlowStepConfig(step_id="bad", type="not_a_real_type")

    def test_valid_step_types_complete(self):
        assert VALID_STEP_TYPES == frozenset(
            {
                "payment_order",
                "incoming_payment_detail",
                "ledger_transaction",
                "expected_payment",
                "return",
                "reversal",
                "transition_ledger_transaction",
                "verify_external_account",
                "complete_verification",
                "archive_resource",
            }
        )

    def test_return_code_defaults_to_r01(self):
        step = FundsFlowStepConfig(step_id="ret", type="return")
        assert isinstance(step, ReturnStep)
        assert step.code == "R01"

    def test_return_code_custom(self):
        step = FundsFlowStepConfig(step_id="ret", type="return", code="R05")
        assert isinstance(step, ReturnStep)
        assert step.code == "R05"

    def test_reversal_reason_defaults_to_duplicate(self):
        step = FundsFlowStepConfig(step_id="rev", type="reversal")
        assert isinstance(step, ReversalStep)
        assert step.reason == "duplicate"

    def test_union_parses_all_types(self):
        """Each step type can be parsed via the compat factory."""
        specs = [
            {
                "step_id": "a",
                "type": "payment_order",
                "payment_type": "ach",
                "direction": "debit",
                "amount": 100,
                "originating_account_id": "ia",
            },
            {
                "step_id": "b",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "amount": 100,
                "internal_account_id": "ia",
            },
            {"step_id": "c", "type": "expected_payment"},
            {
                "step_id": "d",
                "type": "ledger_transaction",
                "ledger_entries": [
                    {"amount": 1, "direction": "debit", "ledger_account_id": "la"},
                    {"amount": 1, "direction": "credit", "ledger_account_id": "lb"},
                ],
            },
            {"step_id": "e", "type": "return"},
            {"step_id": "f", "type": "reversal"},
            {"step_id": "g", "type": "transition_ledger_transaction", "status": "posted"},
        ]
        for spec in specs:
            step = FundsFlowStepConfig.model_validate(spec)
            assert step.type == spec["type"]


# ---------------------------------------------------------------------------
# FundsFlowConfig
# ---------------------------------------------------------------------------


class TestFundsFlowConfig:
    def test_valid_flow(self):
        flow = FundsFlowConfig(
            ref="test_flow",
            pattern_type="deposit_settle",
            steps=[_ipd()],
        )
        assert flow.ref == "test_flow"
        assert len(flow.steps) == 1

    def test_duplicate_step_ids(self):
        with pytest.raises(ValueError, match="Duplicate step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[_ipd(), _lt(step_id="s1")],
            )

    def test_invalid_depends_on(self):
        with pytest.raises(ValueError, match="not a valid step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[_lt(depends_on=["nonexistent"])],
            )

    def test_bad_trace_placeholder(self):
        with pytest.raises(ValueError, match="unknown placeholders"):
            FundsFlowConfig(
                ref="bad",
                pattern_type="test",
                trace_value_template="{ref}-{bad_key}",
                steps=[_ipd()],
            )

    def test_empty_steps_rejected(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(ref="empty", pattern_type="test", steps=[])

    def test_valid_depends_on(self):
        flow = FundsFlowConfig(
            ref="chained",
            pattern_type="test",
            steps=[_ipd(), _lt(step_id="s2", depends_on=["s1"])],
        )
        assert flow.steps[1].depends_on == ["s1"]

    def test_default_trace_template(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            steps=[_ipd()],
        )
        assert flow.trace_value_template == "{ref}-{instance}"
        assert flow.trace_key == "deal_id"

    def test_actors_and_metadata(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            actors={
                "direct_1": {
                    "alias": "Platform",
                    "frame_type": "direct",
                    "customer_name": "Platform",
                    "slots": {"ops": "$ref:internal_account.ops"},
                }
            },
            trace_metadata={"env": "demo"},
            steps=[_ipd()],
        )
        frame = flow.actors["direct_1"]
        assert frame.alias == "Platform"
        assert frame.frame_type == "direct"
        assert frame.slots["ops"].ref == "$ref:internal_account.ops"
        assert flow.trace_metadata == {"env": "demo"}

    def test_scale_config(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            scale=FundsFlowScaleConfig(instances=100),
            steps=[_ipd()],
        )
        assert flow.scale.instances == 100

    def test_extra_fields_forbidden_on_flow(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(
                ref="f1",
                pattern_type="test",
                bogus_field="nope",
                steps=[_ipd()],
            )


# ---------------------------------------------------------------------------
# DataLoaderConfig with funds_flows
# ---------------------------------------------------------------------------


class TestDataLoaderConfigWithFlows:
    def test_existing_config_no_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
        )
        assert config.funds_flows == []

    def test_config_with_funds_flows(self):
        config = DataLoaderConfig(
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [_ipd()],
                }
            ],
        )
        assert len(config.funds_flows) == 1
        assert config.funds_flows[0].ref == "f1"

    def test_mixed_resources_and_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [_ipd()],
                }
            ],
        )
        assert len(config.ledgers) == 1
        assert len(config.funds_flows) == 1


# ---------------------------------------------------------------------------
# MetadataMixin on IPD and Return
# ---------------------------------------------------------------------------


class TestMetadataOnIPDAndReturn:
    def test_ipd_accepts_metadata(self):
        ipd = IncomingPaymentDetailConfig(
            ref="ipd1",
            type="ach",
            direction="credit",
            amount=10000,
            internal_account_id="$ref:internal_account.ops",
            metadata={"deal_id": "deal-001"},
        )
        assert ipd.metadata == {"deal_id": "deal-001"}

    def test_ipd_metadata_defaults_empty(self):
        ipd = IncomingPaymentDetailConfig(
            ref="ipd1",
            type="ach",
            direction="credit",
            amount=10000,
            internal_account_id="$ref:internal_account.ops",
        )
        assert ipd.metadata == {}

    def test_dataloader_strips_originating_account_id_from_raw_ipd(self):
        cfg = DataLoaderConfig.model_validate(
            {
                "incoming_payment_details": [
                    {
                        "ref": "inbound",
                        "type": "stablecoin",
                        "direction": "credit",
                        "amount": 100000,
                        "internal_account_id": "$ref:internal_account.wallet",
                        "originating_account_id": "$ref:counterparty.ext.account[0]",
                        "currency": "USDC",
                    }
                ]
            }
        )
        ipd = cfg.incoming_payment_details[0]
        assert not hasattr(ipd, "originating_account_id")
        dumped = cfg.model_dump()
        assert "originating_account_id" not in dumped["incoming_payment_details"][0]

    def test_return_accepts_metadata(self):
        ret = ReturnConfig(
            ref="r1",
            returnable_id="$ref:incoming_payment_detail.ipd1",
            metadata={"deal_id": "deal-001"},
        )
        assert ret.metadata == {"deal_id": "deal-001"}


# ---------------------------------------------------------------------------
# compile_to_plan gate
# ---------------------------------------------------------------------------


class TestCompilePipeline:
    def test_passthrough_no_flows(self):
        config = DataLoaderConfig()
        result, irs = _compile(config)
        assert irs is None
        assert result.funds_flows == []

    def test_passthrough_with_resources_no_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
        )
        result, irs = _compile(config)
        assert irs is None
        assert len(result.ledgers) == 1

    def test_compiles_with_flows(self):
        config = DataLoaderConfig(
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [
                        {
                            "step_id": "s1",
                            "type": "incoming_payment_detail",
                            "payment_type": "ach",
                            "direction": "credit",
                            "amount": 1000,
                            "internal_account_id": "$ref:internal_account.ops",
                        }
                    ],
                }
            ],
        )
        result, _ = _compile(config)
        assert result.funds_flows == []
        assert len(result.incoming_payment_details) == 1


# ---------------------------------------------------------------------------
# DAG: connections complete before legal entities (execute / preview order)
# ---------------------------------------------------------------------------


class TestDagConnectionsBeforeLegalEntities:
    def test_legal_entities_run_in_later_batch_than_connections(self):
        from dataloader.engine import dry_run

        batches = dry_run(
            DataLoaderConfig.model_validate(
                {
                    "connections": [
                        {
                            "ref": "c_a",
                            "entity_id": "modern_treasury",
                            "nickname": "A",
                        },
                        {
                            "ref": "c_b",
                            "entity_id": "modern_treasury",
                            "nickname": "B",
                        },
                    ],
                    "legal_entities": [
                        {
                            "ref": "le1",
                            "legal_entity_type": "business",
                            "business_name": "Co",
                        },
                    ],
                    "internal_accounts": [
                        {
                            "ref": "ia_usd",
                            "connection_id": "$ref:connection.c_a",
                            "name": "USD",
                            "party_name": "Co",
                            "currency": "USD",
                            "legal_entity_id": "$ref:legal_entity.le1",
                        },
                    ],
                    "funds_flows": [],
                }
            )
        )
        conn_idxs = [
            i for i, b in enumerate(batches) if any(r.startswith("connection.") for r in b)
        ]
        le_idxs = [
            i for i, b in enumerate(batches) if any(r.startswith("legal_entity.") for r in b)
        ]
        assert conn_idxs and le_idxs
        assert max(conn_idxs) < min(le_idxs)


class TestInjectLegalEntityPspConnectionId:
    def test_omits_connection_id_when_sole_modern_treasury(self):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "psp",
                        "entity_id": "modern_treasury",
                        "nickname": "PSP",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                    },
                ],
                "funds_flows": [],
            }
        )
        reg = RefRegistry()
        reg.register("connection.psp", "550e8400-e29b-41d4-a716-446655440000")
        resolved = {"legal_entity_type": "business", "business_name": "Co"}
        inject_legal_entity_psp_connection_id(
            config,
            reg,
            resolved,
            typed_ref="legal_entity.le1",
        )
        assert "connection_id" not in resolved

    def test_prefers_fiat_ia_connection_when_two_modern_treasury_rows(self):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "stablecoin_rail",
                        "entity_id": "modern_treasury",
                        "nickname": "SC",
                    },
                    {
                        "ref": "fiat_rail",
                        "entity_id": "modern_treasury",
                        "nickname": "Fiat",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                    },
                ],
                "internal_accounts": [
                    {
                        "ref": "platform_usd",
                        "connection_id": "$ref:connection.fiat_rail",
                        "name": "USD",
                        "party_name": "Co",
                        "currency": "USD",
                        "legal_entity_id": "$ref:legal_entity.le1",
                    },
                    {
                        "ref": "platform_usdc",
                        "connection_id": "$ref:connection.stablecoin_rail",
                        "name": "USDC",
                        "party_name": "Co",
                        "currency": "USDC",
                        "legal_entity_id": "$ref:legal_entity.le1",
                    },
                ],
                "funds_flows": [],
            }
        )
        reg = RefRegistry()
        reg.register(
            "connection.stablecoin_rail",
            "11111111-1111-1111-1111-111111111111",
        )
        reg.register(
            "connection.fiat_rail",
            "22222222-2222-2222-2222-222222222222",
        )
        resolved = {"legal_entity_type": "business", "business_name": "Co"}
        inject_legal_entity_psp_connection_id(
            config,
            reg,
            resolved,
            typed_ref="legal_entity.le1",
        )
        assert resolved["connection_id"] == "22222222-2222-2222-2222-222222222222"

    def test_skips_when_byob_only(self):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "gwb",
                        "entity_id": "example1",
                        "nickname": "GWB",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                    },
                ],
                "funds_flows": [],
            }
        )
        reg = RefRegistry()
        reg.register("connection.gwb", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        resolved = {"legal_entity_type": "business", "business_name": "Co"}
        inject_legal_entity_psp_connection_id(
            config,
            reg,
            resolved,
            typed_ref="legal_entity.le1",
        )
        assert "connection_id" not in resolved

    def test_strips_connection_id_when_sole_modern_treasury_even_if_resolved_preset(
        self,
    ):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "psp",
                        "entity_id": "modern_treasury",
                        "nickname": "PSP",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                    },
                ],
                "funds_flows": [],
            }
        )
        reg = RefRegistry()
        reg.register("connection.psp", "550e8400-e29b-41d4-a716-446655440000")
        resolved = {
            "legal_entity_type": "business",
            "business_name": "Co",
            "connection_id": "preset-uuid-0000-0000-000000000000",
        }
        inject_legal_entity_psp_connection_id(
            config,
            reg,
            resolved,
            typed_ref="legal_entity.le1",
        )
        assert "connection_id" not in resolved

    def test_skips_injection_when_connection_id_already_in_payload_multi_conn(self):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "c_a",
                        "entity_id": "modern_treasury",
                        "nickname": "A",
                    },
                    {
                        "ref": "c_b",
                        "entity_id": "modern_treasury",
                        "nickname": "B",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                    },
                ],
                "funds_flows": [],
            }
        )
        reg = RefRegistry()
        reg.register("connection.c_a", "550e8400-e29b-41d4-a716-446655440000")
        reg.register("connection.c_b", "660e8400-e29b-41d4-a716-446655440001")
        resolved = {
            "legal_entity_type": "business",
            "business_name": "Co",
            "connection_id": "preset-uuid-0000-0000-000000000000",
        }
        inject_legal_entity_psp_connection_id(
            config,
            reg,
            resolved,
            typed_ref="legal_entity.le1",
        )
        assert resolved["connection_id"] == "preset-uuid-0000-0000-000000000000"

    def test_pydantic_strips_le_connection_id_when_sole_modern_treasury(self):
        config = DataLoaderConfig.model_validate(
            {
                "connections": [
                    {
                        "ref": "psp",
                        "entity_id": "modern_treasury",
                        "nickname": "PSP",
                    },
                ],
                "legal_entities": [
                    {
                        "ref": "le1",
                        "legal_entity_type": "business",
                        "business_name": "Co",
                        "connection_id": "$ref:connection.psp",
                    },
                ],
                "funds_flows": [],
            }
        )
        assert config.legal_entities[0].connection_id is None


class TestSingleConnectionMultiCurrencyInternalAccounts:
    def test_usd_and_usdc_on_same_modern_treasury_connection_validates(self):
        raw = {
            "connections": [
                {
                    "ref": "solo",
                    "entity_id": "modern_treasury",
                    "nickname": "Solo PSP",
                }
            ],
            "legal_entities": [
                {
                    "ref": "le1",
                    "legal_entity_type": "business",
                    "business_name": "Acme Corp",
                }
            ],
            "internal_accounts": [
                {
                    "ref": "usd_ia",
                    "connection_id": "$ref:connection.solo",
                    "name": "USD",
                    "party_name": "Acme",
                    "currency": "USD",
                    "legal_entity_id": "$ref:legal_entity.le1",
                },
                {
                    "ref": "usdc_ia",
                    "connection_id": "$ref:connection.solo",
                    "name": "USDC",
                    "party_name": "Acme",
                    "currency": "USDC",
                    "legal_entity_id": "$ref:legal_entity.le1",
                },
            ],
            "funds_flows": [],
        }
        config = DataLoaderConfig.model_validate(raw)
        assert len(config.internal_accounts) == 2


class TestCounterpartyWalletInlineAccount:
    """Stablecoin wallet counterparties use MT account_details + account_number_type."""

    def test_wallet_account_number_type_populates_account_details(self):
        acct = CounterpartyAccountConfig(
            wallet_account_number_type="ethereum_address",
            party_name="Vendor",
        )
        dumped = acct.model_dump()
        assert len(dumped["account_details"]) == 1
        assert dumped["account_details"][0]["account_number_type"] == "ethereum_address"
        assert dumped["account_details"][0]["account_number"].startswith("0x")
        assert dumped["routing_details"] == []

    @pytest.mark.parametrize("network_type", get_args(WalletAccountNumberType))
    def test_wallet_account_number_type_covers_all_documented_networks(self, network_type: str):
        assert network_type in SANDBOX_WALLET_DEMO_ADDRESSES
        acct = CounterpartyAccountConfig(
            wallet_account_number_type=network_type,  # type: ignore[arg-type]
            party_name="Wallet",
        )
        dumped = acct.model_dump()
        assert len(dumped["account_details"]) == 1
        assert dumped["account_details"][0]["account_number_type"] == network_type
        assert dumped["routing_details"] == []

    def test_sandbox_wallet_demo_keys_match_wallet_account_number_type_literal(self):
        literal_set = frozenset(get_args(WalletAccountNumberType))
        assert frozenset(SANDBOX_WALLET_DEMO_ADDRESSES.keys()) == literal_set

    def test_explicit_wallet_counterparty_matches_mt_post_shape(self):
        """Hand-authored account_details (no wallet_account_number_type helper)."""
        raw = {
            "counterparties": [
                {
                    "ref": "vendor_wallet",
                    "name": "Vendor Wallet",
                    "accounts": [
                        {
                            "account_details": [
                                {
                                    "account_number": "0x9bE868839163E128971Bb6AE045e172Fa806E805",
                                    "account_number_type": "ethereum_address",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        config = DataLoaderConfig.model_validate(raw)
        cp = config.counterparties[0]
        assert cp.name == "Vendor Wallet"
        ad = cp.accounts[0].account_details[0]
        assert ad.account_number_type == "ethereum_address"
        assert ad.account_number.startswith("0x")

    def test_wallet_and_sandbox_behavior_are_mutually_exclusive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CounterpartyAccountConfig(
                sandbox_behavior="success",
                wallet_account_number_type="ethereum_address",
            )


# ---------------------------------------------------------------------------
# Preview list order (execute / setup UI) — not Pydantic field order
# ---------------------------------------------------------------------------


class TestBuildPreviewSetupOrder:
    """Skipped connections were appended after batched rows; sort fixes Setup order."""

    def test_connections_listed_before_legal_entity_when_connections_skipped(self):
        raw = {
            "connections": [
                {"ref": "c_a", "entity_id": "modern_treasury", "nickname": "A"},
                {"ref": "c_b", "entity_id": "modern_treasury", "nickname": "B"},
            ],
            "legal_entities": [
                {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
            ],
            "internal_accounts": [
                {
                    "ref": "ia",
                    "connection_id": "$ref:connection.c_a",
                    "name": "USD",
                    "party_name": "Co",
                    "currency": "USD",
                    "legal_entity_id": "$ref:legal_entity.le1",
                },
            ],
            "funds_flows": [],
        }
        config = DataLoaderConfig.model_validate(raw)
        resource_map = {typed_ref_for(r): r for r in all_resources(config)}
        batches = [["legal_entity.le1"]]
        skip_refs = {"connection.c_a", "connection.c_b"}
        items = build_preview(batches, resource_map, skip_refs=skip_refs)
        setup = [i for i in items if i["display_phase"] == DisplayPhase.SETUP]
        types = [i["resource_type"] for i in setup]
        assert types.index("connection") < types.index("legal_entity")

    def test_batched_resource_shows_create_not_matched_when_recon_stale(self):
        """DAG says create (ref in batches) — UI must not show 'existing' from recon alone."""
        from org.reconciliation import ReconciledResource, ReconciliationResult

        raw = {
            "connections": [
                {"ref": "c1", "entity_id": "example1", "nickname": "C1"},
            ],
            "legal_entities": [
                {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
            ],
            "internal_accounts": [
                {
                    "ref": "ia",
                    "connection_id": "$ref:connection.c1",
                    "name": "USD",
                    "party_name": "Co",
                    "currency": "USD",
                    "legal_entity_id": "$ref:legal_entity.le1",
                },
            ],
            "funds_flows": [],
        }
        config = DataLoaderConfig.model_validate(raw)
        resource_map = {typed_ref_for(r): r for r in all_resources(config)}
        batches = [["connection.c1"]]
        m = ReconciledResource(
            config_ref="connection.c1",
            config_resource=config.connections[0],
            discovered_id="disc-uuid",
            discovered_name="Old MT conn",
            match_reason="test",
            use_existing=True,
        )
        recon = ReconciliationResult(matches=[m])
        items = build_preview(
            batches,
            resource_map,
            skip_refs=set(),
            reconciliation=recon,
        )
        conn_row = next(i for i in items if i["typed_ref"] == "connection.c1")
        assert conn_row["action"] == "create"
        assert conn_row["reconciled"] is False


class TestEditedResourceTypedRefs:
    def test_detects_connection_payload_change(self):
        from dataloader.loader_validation import edited_resource_typed_refs

        raw = {
            "connections": [
                {"ref": "c1", "entity_id": "example1", "nickname": "C1"},
            ],
            "legal_entities": [
                {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
            ],
            "funds_flows": [],
        }
        prior = DataLoaderConfig.model_validate(raw)
        new_cfg = prior.model_copy(deep=True)
        new_cfg.connections[0].entity_id = "example2"
        refs = edited_resource_typed_refs(prior, new_cfg)
        assert "connection.c1" in refs

    def test_prior_none_returns_empty(self):
        from dataloader.loader_validation import edited_resource_typed_refs

        raw = {
            "connections": [
                {"ref": "c1", "entity_id": "example1", "nickname": "C1"},
            ],
            "funds_flows": [],
        }
        cfg = DataLoaderConfig.model_validate(raw)
        assert edited_resource_typed_refs(None, cfg) == set()


# ---------------------------------------------------------------------------
# Existing examples still validate (regression)
# ---------------------------------------------------------------------------

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"


class TestExistingExamplesUnchanged:
    @pytest.mark.parametrize(
        "example",
        sorted(_EXAMPLE_DIR.glob("*.json")),
        ids=lambda p: p.name,
    )
    def test_example_validates(self, example: Path):
        raw = example.read_bytes()
        config = DataLoaderConfig.model_validate_json(raw)
        if not config.funds_flows:
            result, irs = _compile(config)
            assert irs is None


# ---------------------------------------------------------------------------
# funds_flow_demo.json parses at model level
# ---------------------------------------------------------------------------


class TestFundsFlowDemo:
    def test_demo_parses(self):
        demo = _EXAMPLE_DIR / "funds_flow_demo.json"
        if not demo.exists():
            pytest.skip("funds_flow_demo.json not yet created")
        config = DataLoaderConfig.model_validate_json(demo.read_bytes())
        assert len(config.funds_flows) == 1
        assert config.funds_flows[0].ref == "simple_deposit"
        assert len(config.funds_flows[0].steps) == 3

    def test_demo_compiles_end_to_end(self):
        demo = _EXAMPLE_DIR / "funds_flow_demo.json"
        if not demo.exists():
            pytest.skip("funds_flow_demo.json not yet created")
        config = DataLoaderConfig.model_validate_json(demo.read_bytes())
        result, _ = _compile(config)
        assert result.funds_flows == []
        assert len(result.incoming_payment_details) == 1
        assert len(result.ledger_transactions) == 1


# ---------------------------------------------------------------------------
# Seed catalog
# ---------------------------------------------------------------------------

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "flow_compiler" / "seeds"


class TestSeedCatalog:
    def test_curated_yamls_load(self):
        for name in ["harry_potter", "superheroes", "seinfeld"]:
            catalog = yaml.safe_load((_SEEDS_DIR / f"{name}.yaml").read_text())
            assert "business_profiles" in catalog
            assert "individual_profiles" in catalog
            assert len(catalog["business_profiles"]) >= 50

    def test_industry_templates_load(self):
        templates = yaml.safe_load((_SEEDS_DIR / "industry_templates.yaml").read_text())
        for key in [
            "tech",
            "government",
            "payroll",
            "manufacturing",
            "property_management",
            "construction",
        ]:
            assert key in templates, f"Missing industry vertical: {key}"
            assert len(templates[key]["company_patterns"]) >= 5
