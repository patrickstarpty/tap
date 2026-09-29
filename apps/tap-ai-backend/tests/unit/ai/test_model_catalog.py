from __future__ import annotations

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.catalog import ModelCatalog
from tap.modules.ai.domain.models import (
    ModelCapability,
    ModelDescriptor,
    ModelGatewayUnavailable,
)

_QWEN_PLUS = ModelDescriptor(
    alias="qwen-plus",
    display_name="Qwen Plus",
    capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
    enabled=True,
)
_QWEN_MAX = ModelDescriptor(
    alias="qwen-max",
    display_name="Qwen Max",
    capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED}),
)
_DISABLED = ModelDescriptor(
    alias="qwen-flash",
    display_name="qwen-flash",
    capabilities=frozenset({ModelCapability.CHAT}),
    enabled=False,
)


class _Gateway:
    async def catalog(self, scope):
        assert scope == VALIDATION_SCOPE
        return (_QWEN_PLUS, _QWEN_MAX, _DISABLED)


@pytest.mark.asyncio
async def test_catalog_exposes_only_enabled_public_model_fields() -> None:
    catalog = ModelCatalog(_Gateway(), default_alias="qwen-plus")

    result = await catalog.list_models(VALIDATION_SCOPE)

    assert result == (_QWEN_PLUS, _QWEN_MAX)
    assert catalog.default_alias == "qwen-plus"
    assert not hasattr(result[0], "provider")
    assert not hasattr(result[0], "reasoning_effort")


@pytest.mark.asyncio
async def test_catalog_requires_the_configured_default_model() -> None:
    with pytest.raises(ModelGatewayUnavailable):
        await ModelCatalog(_Gateway(), default_alias="removed-model").list_models(VALIDATION_SCOPE)
