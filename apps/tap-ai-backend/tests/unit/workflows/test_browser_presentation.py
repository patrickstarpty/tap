"""Source metadata is redacted only in model views, never frozen browser evidence."""

import json
from copy import deepcopy

import pytest
from browser_use.llm.messages import UserMessage

from tap.modules.workflows.application.browser_presentation import (
    model_observation,
    model_source_url,
)
from tap.modules.workflows.application.direct_browser import DirectBrowserLoop

from .test_browser_use_runtime import BrowserDecision, DecisionGateway, decision_model


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://username:password@example.test:9443/path?access_token=SENTINEL#secret",
            "https://example.test:9443/path",
        ),
        ("about:blank?access_token=SENTINEL", "about:blank"),
        ("data:text/plain,SENTINEL", "[redacted URL]"),
        ("https://example.test:invalid/path?access_token=SENTINEL", "[redacted URL]"),
    ],
)
def test_source_url_redaction_is_conservative(url, expected):
    assert model_source_url(url) == expected


def test_direct_model_view_does_not_expose_or_mutate_private_source_urls():
    source = "https://user:pass@example.test/form?access_token=SENTINEL#private"
    business_value = "https://business.test/value?display=chosen#result"
    original = {
        "sequence": 7,
        "url": source,
        "source_url": source,
        "documents": [{"frame_id": "frame", "document_id": "doc", "url": source}],
        "extracted": {
            "name": "chosen",
            "value": business_value,
            "source_url": source,
            "source_frame_id": "frame",
            "source_document_id": "doc",
        },
        "fields": [{"selector": "#next", "tag": "a", "href": source}],
        "result": {
            "outputs": [
                {
                    "url": business_value,
                    "final_url": source,
                    "provenance": {"chosen": {"source_url": source, "observation_sequence": 7}},
                }
            ]
        },
    }
    frozen = deepcopy(original)
    loop = DirectBrowserLoop(None, None, None, max_rounds=1)
    view = loop._observation(original)
    assert "SENTINEL" not in json.dumps(view)
    assert "access_token" not in json.dumps(view)
    assert "user:pass" not in json.dumps(view)
    assert view["extracted"]["value"] == business_value
    assert view["result"]["outputs"][0]["url"] == business_value
    assert view["documents"][0]["document_id"] == "doc"
    assert original == frozen
    assert loop.controls["7:0"]["href"] == source
    assert (
        model_observation(original)["result"]["outputs"][0]["provenance"]["chosen"]["source_url"]
        == "https://example.test/form"
    )


@pytest.mark.asyncio
async def test_browser_use_gateway_redacts_sdk_tab_metadata_not_business_content():
    gateway = DecisionGateway(['{"action":[{"click":{"index":12}}]}'])
    message = UserMessage(
        content=(
            "<browser_state>\nAvailable tabs:\n"
            "Tab abcd: https://user:pass@example.test/form?access_token=SENTINEL#secret - Form\n"
            "Interactive elements:\n[1] value=https://business.test/value?chosen=1\n</browser_state>"
        )
    )
    original = message.text
    await decision_model(gateway).ainvoke([message], BrowserDecision)
    request = gateway.requests[0]
    assert "SENTINEL" not in request.context
    assert "access_token" not in request.context
    assert "https://business.test/value?chosen=1" in request.context
    assert "Tab abcd: https://example.test/form - Form" in request.context
    assert message.text == original
