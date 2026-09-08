from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.catalog import ModelCatalog
from tap.modules.ai.domain.models import ModelCapability, ModelDescriptor


class _Gateway:
    async def catalog(self, scope):
        assert scope == VALIDATION_SCOPE
        return (
            ModelDescriptor(
                alias="tapper-chat",
                display_name="GPT-5.6 Sol",
                capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
                enabled=True,
            ),
        )


@pytest.mark.asyncio
async def test_catalog_exposes_only_enabled_public_model_fields() -> None:
    catalog = ModelCatalog(_Gateway())

    result = await catalog.list_models(VALIDATION_SCOPE)

    assert result == (
        ModelDescriptor(
            alias="tapper-chat",
            display_name="GPT-5.6 Sol",
            capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
            enabled=True,
        ),
    )
    assert not hasattr(result[0], "provider")
    assert not hasattr(result[0], "reasoning_effort")
