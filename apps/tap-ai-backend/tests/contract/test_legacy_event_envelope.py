"""Compatibility intentions remain closed, scoped and tamper evident."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext

SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="tapper-demo",
    actor_id="tapper-local-user",
    identity_mode=IdentityMode.VALIDATION,
)


def values(**changes):
    from tap.platform.messaging.mysql_outbox import compatibility_outbox_values

    arguments = dict(
        outbox_id="old:outbox",
        command_id="command",
        aggregate_type="turn",
        aggregate_id="turn-1",
        message_type="turn.process_requested",
        sequence=None,
        created_at=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    arguments.update(changes)
    return compatibility_outbox_values(SCOPE, **arguments)


def test_compatibility_preserves_facts_without_inventing_domain_payload():
    from tap.platform.messaging.mysql_outbox import validate_outbox_row

    row = values()
    envelope = validate_outbox_row(row)
    assert row["identity_origin"] == "VALIDATION"
    assert envelope.event_id == "old:outbox"
    assert envelope.correlation_id == "old:outbox"
    assert envelope.idempotency_key == "command"
    assert envelope.aggregate_version == 0
    assert envelope.payload == {"aggregateId": "turn-1", "sequence": None}
    assert envelope.causation_id is None
    assert row["event_content_digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "changes",
    [
        {"message_type": "future.event"},
        {"aggregate_type": "DocumentRevision"},
        {"sequence": -1},
        {"sequence": True},
        {"command_id": "x" * 129},
    ],
)
def test_writer_rejects_unknown_or_invalid_legacy_shapes(changes):
    with pytest.raises((ValueError, TypeError)):
        values(**changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "other"),
        ("actor_id", "other"),
        ("identity_mode", "product"),
        ("identity_origin", "PRODUCT"),
        ("enterprise_id", "other"),
        ("command_id", "other"),
        ("outbox_id", "other"),
        ("aggregate_type", "chat_turn"),
        ("aggregate_id", "other"),
        ("message_type", "chat.event_appended"),
        ("sequence", 1),
        ("event_content_digest", "sha256:" + "0" * 64),
        ("envelope", None),
    ],
)
def test_reader_rejects_relational_json_contradictions(field, value):
    from tap.platform.messaging.mysql_outbox import validate_outbox_row

    row = deepcopy(values())
    row[field] = value
    with pytest.raises((ValueError, TypeError)):
        validate_outbox_row(row)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("scope_kind", "PLATFORM"),
        ("payload", {"aggregateId": "other", "sequence": None}),
        ("aggregate_version", 1),
    ],
)
def test_reader_rejects_malformed_envelopes(field, value):
    from tap.platform.messaging.mysql_outbox import validate_outbox_row

    row = deepcopy(values())
    row["envelope"][field] = value
    with pytest.raises((ValueError, TypeError)):
        validate_outbox_row(row)


def test_digest_excludes_delivery_identity_time_and_tracks_content():
    first = values()
    second = values(
        outbox_id="new", created_at=datetime(2026, 9, 5), correlation_id="new-correlation"
    )
    assert first["event_content_digest"] == second["event_content_digest"]
    assert first["event_content_digest"] != values(sequence=1)["event_content_digest"]


def test_project_scope_helper_rejects_untrusted_context_and_scoped_ids_are_bounded():
    from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
    from tap.platform.db.schema import outbox
    from tap.platform.messaging.mysql_outbox import scoped_outbox_id

    with pytest.raises(TypeError):
        require_project_scope(None)
    assert scope_values(SCOPE)["identity_mode"] == "validation"
    assert len(scope_predicates(outbox, SCOPE)) == 2
    other = ProjectScopeContext(
        enterprise_id="local",
        project_id="other",
        actor_id="tapper-local-user",
        identity_mode=IdentityMode.VALIDATION,
    )
    first = scoped_outbox_id(SCOPE, kind="turn-command", identity="a" * 128)
    assert len(first) <= 128
    assert first != scoped_outbox_id(other, kind="turn-command", identity="a" * 128)
