"""The structured suggestion adapter must request a closed schema and map output to candidates."""

import json

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import ModelOperation
from tap.modules.chat.adapters.model_gateway_suggestions import (
    SUGGESTION_SCHEMA,
    ModelGatewaySuggestionGenerator,
)
from tap.modules.chat.domain.suggestions import Candidate, SuggestionInputs, TopicSource


def inputs(**changes) -> SuggestionInputs:
    defaults = dict(
        topics=(
            TopicSource("src_a", "Onboarding", "rev-1:0", ("Setup", "Access")),
            TopicSource("src_b", "Billing", "rev-1:0", ("Invoices",)),
        ),
        entities=("Billing Engine",),
        personal_questions=("How do I reset my password?",),
        personal_source_ids=("src_a",),
        popular_sources=(("src_b", 3),),
    )
    defaults.update(changes)
    return SuggestionInputs(**defaults)


class FakeGateway:
    def __init__(self, output: dict[str, object]):
        self._output = output
        self.requests: list = []

    async def generate_structured(self, request):
        self.requests.append(request)
        from types import SimpleNamespace

        return SimpleNamespace(output=self._output)


def _generator(gateway) -> ModelGatewaySuggestionGenerator:
    return ModelGatewaySuggestionGenerator(
        gateway, scope=VALIDATION_SCOPE, alias="qwen-plus", timeout_seconds=3.0
    )


@pytest.mark.asyncio
async def test_generator_requests_structured_output_with_schema():
    gateway = FakeGateway({"suggestions": []})
    await _generator(gateway).generate(inputs(), "en")

    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.operation is ModelOperation.STRUCTURED
    assert request.schema["title"] == "PromptSuggestions"
    assert request.allow_retries is False
    assert request.schema is SUGGESTION_SCHEMA or request.schema == SUGGESTION_SCHEMA


@pytest.mark.asyncio
async def test_prompt_contains_inputs_and_locale():
    gateway = FakeGateway({"suggestions": []})
    await _generator(gateway).generate(inputs(), "zh")

    request = gateway.requests[0]
    assert "\n\nINPUT:\n" in request.prompt
    payload = json.loads(request.prompt.split("\n\nINPUT:\n", 1)[1])
    assert payload["locale"] == "zh"
    assert payload["popularSources"] == [{"id": "src_b", "count": 3}]
    assert payload["sources"] == [
        {"id": "src_a", "name": "Onboarding", "headings": ["Setup", "Access"]},
        {"id": "src_b", "name": "Billing", "headings": ["Invoices"]},
    ]
    assert payload["entities"] == ["Billing Engine"]
    assert payload["recentQuestions"] == ["How do I reset my password?"]
    assert payload["recentSourceIds"] == ["src_a"]


@pytest.mark.asyncio
async def test_generator_maps_output_to_candidates():
    gateway = FakeGateway(
        {
            "suggestions": [
                {"question": "What does Onboarding cover?", "sourceIds": ["src_a"]},
                {"question": "How are invoices billed?", "sourceIds": ["src_b"]},
            ]
        }
    )
    candidates = await _generator(gateway).generate(inputs(), "en")

    assert candidates == (
        Candidate("What does Onboarding cover?", ("src_a",)),
        Candidate("How are invoices billed?", ("src_b",)),
    )


@pytest.mark.asyncio
async def test_deterministic_gateway_answers_suggestion_schema():
    from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
    from tap.modules.ai.adapters.litellm_catalog import ModelRoles
    from tap.testing.deterministic_model_gateway import DeterministicModelGateway

    async def redact(text: str) -> str:
        return text

    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="private-provider-key",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    gateway = DeterministicModelGateway(config, scope=VALIDATION_SCOPE, redact=redact)
    generator = _generator(gateway)

    candidates = await generator.generate(inputs(), "en")

    assert candidates == (
        Candidate("What does Onboarding cover?", ("src_a",)),
        Candidate("What does Billing cover?", ("src_b",)),
    )
