from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.chat.application.conversations import (
    ConversationNotFound,
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    GraphContextStatus,
    RetrievalSummary,
    TurnInput,
    conversation_title,
)

OWNER = VALIDATION_SCOPE.actor_id


def _input(message: str = "What changed?") -> TurnInput:
    return TurnInput(
        message=message,
        actor_id=OWNER,
        identity_mode=VALIDATION_SCOPE.identity_mode.value,
        model_alias="qwen-plus",
    )


async def _service_with(*titles: str) -> ConversationService:
    service = ConversationService(InMemoryConversationRepository(), scope=VALIDATION_SCOPE)
    base = datetime(2026, 9, 1, tzinfo=UTC)
    for index, title in enumerate(titles):
        conversation_id = f"conversation-{index}"
        await service.create(conversation_id, f"turn-{index}", f"request-{index}", _input(title))
        stored = service.repository.values[conversation_id]
        moment = base + timedelta(minutes=index)
        service.repository.values[conversation_id] = replace(
            stored, created_at=moment, updated_at=moment
        )
    return service


def test_conversation_title_is_stripped_and_bounded():
    assert conversation_title("  Release risks  ") == "Release risks"
    assert conversation_title("x" * 120) == "x" * 120
    for invalid in ("", "   ", "x" * 121):
        with pytest.raises(ValueError):
            conversation_title(invalid)


@pytest.mark.asyncio
async def test_created_conversation_records_the_creating_actor():
    service = await _service_with("Hello")
    assert service.repository.values["conversation-0"].actor_id == OWNER


@pytest.mark.asyncio
async def test_creator_renames_conversation_and_bumps_updated_at():
    service = await _service_with("Original")
    before = service.repository.values["conversation-0"].updated_at

    renamed = await service.rename("conversation-0", "  Renamed title ", actor_id=OWNER)

    assert renamed.title == "Renamed title"
    assert renamed.updated_at > before
    assert (await service.load("conversation-0")).title == "Renamed title"


@pytest.mark.asyncio
async def test_rename_rejects_blank_other_actor_unknown_and_deleted():
    service = await _service_with("Original")
    with pytest.raises(ValueError):
        await service.rename("conversation-0", "   ", actor_id=OWNER)
    with pytest.raises(AuthorizationDenied):
        await service.rename("conversation-0", "Mine now", actor_id="someone-else")
    with pytest.raises(ConversationNotFound):
        await service.rename("missing", "Title", actor_id=OWNER)
    await service.delete("conversation-0", actor_id=OWNER)
    with pytest.raises(ConversationNotFound):
        await service.rename("conversation-0", "Title", actor_id=OWNER)
    assert service.repository.values["conversation-0"].title == "Original"


@pytest.mark.asyncio
async def test_delete_cancels_active_turns_and_hides_the_conversation_everywhere():
    service = await _service_with("Keep evidence")
    await service.append("conversation-0", "turn-extra", "request-extra", _input("Second"))

    await service.delete("conversation-0", actor_id=OWNER)

    stored = service.repository.values["conversation-0"]
    assert stored.deleted_at is not None
    assert stored.deleted_by == OWNER
    assert [turn.state for turn in stored.turns] == ["canceled", "canceled"]
    assert all(turn.answer_snapshot is not None for turn in stored.turns)
    assert (await service.list(limit=10, cursor=None))[0] == ()
    with pytest.raises(ConversationNotFound):
        await service.load("conversation-0")
    with pytest.raises(ConversationNotFound):
        await service.append("conversation-0", "turn-3", "request-3", _input())
    with pytest.raises(ConversationNotFound):
        await service.cancel("conversation-0", "turn-0")
    with pytest.raises(ConversationNotFound):
        await service.authorize_citation("conversation-0", "turn-0", "citation-1")
    assert await service.replay("conversation-0", "request-0") is None


@pytest.mark.asyncio
async def test_delete_is_idempotent_creator_only_and_unknown_is_not_found():
    service = await _service_with("Mine")
    with pytest.raises(AuthorizationDenied):
        await service.delete("conversation-0", actor_id="someone-else")
    assert service.repository.values["conversation-0"].deleted_at is None
    await service.delete("conversation-0", actor_id=OWNER)
    first = service.repository.values["conversation-0"].deleted_at
    await service.delete("conversation-0", actor_id=OWNER)
    assert service.repository.values["conversation-0"].deleted_at == first
    with pytest.raises(ConversationNotFound):
        await service.delete("missing", actor_id=OWNER)


@pytest.mark.asyncio
async def test_delete_leaves_terminal_turns_untouched():
    service = await _service_with("Done")
    await service.complete_evidence(
        "conversation-0",
        "turn-0",
        AnswerEvidence(
            "answer", "completed", RetrievalSummary("completed"), GraphContextStatus.NOT_REQUESTED
        ),
    )
    await service.delete("conversation-0", actor_id=OWNER)
    assert service.repository.values["conversation-0"].turns[0].state == "completed"


@pytest.mark.asyncio
async def test_history_search_matches_title_case_insensitively_with_literal_wildcards():
    service = await _service_with("Release RISKS", "100% coverage", "snake_case plan", "Other")

    async def titles(query: str | None) -> list[str]:
        values, _ = await service.list(limit=10, cursor=None, query=query)
        return [item.title for item in values]

    assert await titles("risks") == ["Release RISKS"]
    assert await titles("  risks  ") == ["Release RISKS"]
    assert await titles("%") == ["100% coverage"]
    assert await titles("_") == ["snake_case plan"]
    assert await titles("") == ["Other", "snake_case plan", "100% coverage", "Release RISKS"]
    assert await titles("   ") == await titles(None)
    assert await titles("missing") == []


@pytest.mark.asyncio
async def test_history_search_composes_with_keyset_cursor_and_limit():
    service = await _service_with("plan a", "other", "plan b", "plan c", "unrelated")
    first, cursor = await service.list(limit=2, cursor=None, query="PLAN")
    assert [item.title for item in first] == ["plan c", "plan b"]
    assert cursor is not None
    second, final = await service.list(limit=2, cursor=cursor, query="PLAN")
    assert [item.title for item in second] == ["plan a"]
    assert final is None
