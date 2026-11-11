"""The browser transport retains trusted document identities without inventing them."""

import json
from dataclasses import asdict

from tap_platform.browser_automation.contracts import BrowserObservation


def test_document_snapshot_survives_observation_transport() -> None:
    payload = {
        "session_id": "session-1",
        "sequence": 3,
        "url": "https://fixture.test/outer",
        "title": "",
        "bounded_snapshot": "",
        "fields": [],
        "screenshot_ref": None,
        "error": None,
        "documents": [
            {
                "frame_id": "frame-1",
                "document_id": "document-2",
                "url": "https://fixture.test/inner",
            }
        ],
        "extracted": {
            "name": "premium",
            "value": "42",
            "source_frame_id": "frame-1",
            "source_document_id": "document-2",
            "source_url": "https://fixture.test/inner",
            "observation_sequence": 3,
            "observed_at": "2026-11-11T00:00:00Z",
        },
    }
    observation = BrowserObservation(**json.loads(json.dumps(payload)))
    restored = json.loads(json.dumps(asdict(observation)))
    assert restored["documents"] == payload["documents"]
    assert restored["extracted"] == payload["extracted"]
    payload.pop("documents")
    assert BrowserObservation(**payload).documents == ()
