"""``LoaderDraft`` validation — fail invalid ``DataLoaderConfig`` JSON at save time."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.loader_draft import LoaderDraft


def test_config_json_text_must_be_valid_dataloader() -> None:
    with pytest.raises(ValidationError):
        LoaderDraft(config_json_text="not json")


def test_empty_dataloader_config_accepted() -> None:
    d = LoaderDraft(config_json_text="{}")
    assert d.config_json_text == "{}"


def test_working_config_json_validated_when_non_empty() -> None:
    with pytest.raises(ValidationError):
        LoaderDraft(
            config_json_text="{}",
            working_config_json="{not valid",
        )
