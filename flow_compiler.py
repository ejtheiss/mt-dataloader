"""Funds Flow DSL compiler.

Step 1s: passthrough gate only.
Step 2 will add compile_flows() and emit_dataloader_config().
"""

from __future__ import annotations

from models import DataLoaderConfig

__all__ = ["maybe_compile"]


def maybe_compile(config: DataLoaderConfig) -> DataLoaderConfig:
    """If funds_flows is populated, compile to FlowIR and emit back into
    the config's resource sections.  Otherwise return the config unchanged.

    Step 1s: always passthrough.  The actual compiler is added in step 2.
    """
    if not config.funds_flows:
        return config

    raise NotImplementedError(
        "Funds flow compilation is not yet implemented. "
        "Remove funds_flows from your config or wait for step 2."
    )
