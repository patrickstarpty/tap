from datetime import datetime, timezone

import pytest

from tap.interfaces.http.sse import encode_sse
from tap.modules.chat.domain.conversations import ConversationEvent


def test_sse_ids_are_sequences_and_resume_starts_after_last_event_id():
    rendered = encode_sse(
        [
            {"sequence": 4, "eventType": "turn.started", "payload": {"state": "running"}},
            {"sequence": 5, "eventType": "answer.delta", "payload": {"text": "a"}},
        ],
        last_event_id="4",
    )
    assert "id: 4" not in rendered
    assert "id: 5\n" in rendered
    assert "event: answer.delta\n" in rendered


def test_unknown_event_type_is_rejected_and_invalid_resume_never_replays():
    with pytest.raises(ValueError, match="unknown conversation event"):
        ConversationEvent("event-1", 1, "provider.secret", {}, datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="Last-Event-ID"):
        encode_sse([], last_event_id="-1")
