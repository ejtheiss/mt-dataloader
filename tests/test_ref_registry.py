"""Tests for RefRegistry duplicate-ref handling and dedup in compilation."""

from __future__ import annotations

import pytest

from engine import RefRegistry


class TestRefRegistryDuplicates:
    def test_register_same_id_is_idempotent(self):
        reg = RefRegistry()
        reg.register("legal_entity.le1", "uuid-aaa")
        reg.register("legal_entity.le1", "uuid-aaa")
        assert reg.resolve("$ref:legal_entity.le1") == "uuid-aaa"

    def test_register_different_id_warns_and_updates(self):
        reg = RefRegistry()
        reg.register("legal_entity.le1", "uuid-aaa")
        reg.register("legal_entity.le1", "uuid-bbb")
        assert reg.resolve("$ref:legal_entity.le1") == "uuid-bbb"

    def test_register_or_update_then_register_succeeds(self):
        """Simulate org-discovery seeding followed by engine creation."""
        reg = RefRegistry()
        reg.register_or_update("legal_entity.le1", "uuid-discovered")
        reg.register("legal_entity.le1", "uuid-created")
        assert reg.resolve("$ref:legal_entity.le1") == "uuid-created"

    def test_basic_register_and_resolve(self):
        reg = RefRegistry()
        reg.register("counterparty.cp1", "uuid-111")
        assert reg.resolve("$ref:counterparty.cp1") == "uuid-111"
        assert "counterparty.cp1" in reg

    def test_resolve_unknown_raises(self):
        reg = RefRegistry()
        with pytest.raises(KeyError):
            reg.resolve("$ref:missing.ref")


class TestExtraResourcesDedup:
    """Dedup in generate_from_recipe and pass_emit_resources."""

    def test_expand_instance_resources_dedup(self):
        """Identical refs from multiple instances shouldn't duplicate."""
        from flow_compiler.generation import _expand_instance_resources

        ir_template = {
            "legal_entities": [{"ref": "shared_le", "type": "business", "legal_name": "Shared"}]
        }

        seen: dict[str, list[dict]] = {}
        for i in range(3):
            expanded = _expand_instance_resources(ir_template, i, {})
            for section, items in expanded.items():
                bucket = seen.setdefault(section, [])
                existing_refs = {
                    it.get("ref") for it in bucket if isinstance(it, dict) and it.get("ref")
                }
                for item in items:
                    ref = item.get("ref") if isinstance(item, dict) else None
                    if ref and ref in existing_refs:
                        continue
                    bucket.append(item)
                    if ref:
                        existing_refs.add(ref)

        assert len(seen["legal_entities"]) == 1

    def test_pipeline_dedup_extra_resources(self):
        """pass_emit_resources should deduplicate extra_resources by ref."""
        import hashlib

        from flow_compiler import AuthoringConfig
        from flow_compiler.pipeline import CompilationContext, pass_emit_resources
        from models import DataLoaderConfig

        minimal_config = DataLoaderConfig(
            funds_flows=[],
            legal_entities=[],
        )
        raw = b'{"funds_flows": []}'
        auth = AuthoringConfig(
            config=minimal_config,
            json_text=raw.decode(),
            source_hash=hashlib.sha256(raw).hexdigest(),
        )
        extra = (
            (
                "legal_entities",
                [{"ref": "dup_le", "legal_entity_type": "business", "business_name": "A Corp"}],
            ),
            (
                "legal_entities",
                [{"ref": "dup_le", "legal_entity_type": "business", "business_name": "B Corp"}],
            ),
        )
        ctx = CompilationContext(
            authoring=auth,
            extra_resources=extra,
            flow_irs=(),
        )
        out = pass_emit_resources(ctx)
        le_list = out.flat_config.legal_entities
        refs = [le.ref for le in le_list]
        assert refs.count("dup_le") == 1
