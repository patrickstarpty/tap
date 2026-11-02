"""Explicitly opted-in smoke: with Tapper (no Agent) and no manual Skill, a
question that plainly needs one catalog Skill makes the real model call
`skill.activate`, and the Turn view lists that Skill as `auto`."""

from __future__ import annotations

import logging
import os
import uuid

import httpx
import pytest

from tap.entrypoints.tapper_generation_worker import GenerationWorker
from tap.entrypoints.tapper_runtime import TapperSettings, _redact_chat_egress, create_api_runtime
from tap.interfaces.http.app import create_app

_TERMINAL = {"completed", "abstained", "failed", "canceled"}
_MAX_WORKER_ROUNDS = 10


async def _auto_activates_a_matching_skill(settings: TapperSettings) -> None:
    runtime = await create_api_runtime(settings)
    try:
        services = runtime.http_services
        if services.readiness is None or services.conversations is None or not services.knowledge:
            pytest.skip("the Tapper production graph does not expose conversations")
        if (await services.readiness.check()).status != "ready":
            pytest.skip("the Tapper production graph is not ready")
        origin = f"http://{settings.web_host}:{settings.web_port}"
        app = create_app(services, allowed_origins=frozenset({origin}))
        base = f"/api/v1/projects/{settings.project_id}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers={"Origin": origin},
            timeout=60,
        ) as client:
            published = (await client.get(f"{base}/knowledge/published-sources")).json()
            items = published.get("items") or []
            if not items:
                pytest.skip("no published Knowledge source to answer against")
            skill_name = f"release-notes-format-{uuid.uuid4().hex[:8]}"
            created = await client.post(
                f"{base}/ai/skills",
                json={
                    "name": skill_name,
                    "description": (
                        "Writes software release notes in the team's standard template. "
                        "Use whenever the user asks to draft or write release notes."
                    ),
                    "instructions": (
                        "Write release notes as: a one-line Summary, then the sections "
                        "Added, Fixed and Changed, each a bulleted list."
                    ),
                    "applicableTasks": ["knowledge.answer"],
                },
            )
            assert created.status_code == 201, created.text
            skill_id = created.json()["skillId"]
            try:
                accepted = await client.post(
                    f"{base}/conversations",
                    headers={"Idempotency-Key": f"skill-smoke-{uuid.uuid4().hex}"},
                    json={
                        "message": (
                            "请按团队标准的发布说明（release notes）模板，起草 2.3.0 版本的"
                            "发布说明：修复了登录超时问题。"
                        ),
                        "modelAlias": runtime.quality_models.chat_alias,
                        "sourceRevisionIds": [items[0]["revisionId"]],
                    },
                )
                assert accepted.status_code == 202, accepted.text
                conversation_id = accepted.json()["conversationId"]
                worker = GenerationWorker(
                    services.conversations,
                    services.knowledge,
                    insights_explanation=services.insights_explanation,
                    max_turn_attempts=settings.turn_max_attempts,
                    turn_budget=settings.turn_budget,
                    drafts=services.draft_stream,
                    redact=_redact_chat_egress,
                    skills=runtime.skill_activation_runtime,
                )
                turn: dict = {}
                for _ in range(_MAX_WORKER_ROUNDS):
                    await worker.run_once(limit=1)
                    detail = (await client.get(f"{base}/conversations/{conversation_id}")).json()
                    turn = detail["turns"][0]
                    if turn["state"] in _TERMINAL:
                        break
                # The published source need not ground release notes, so abstaining is fine.
                assert turn.get("state") in {"completed", "abstained"}, turn
                used = {item["skillId"]: item for item in turn["input"]["skillsUsed"]}
                assert skill_id in used, turn["input"]["skillsUsed"]
                assert used[skill_id]["mode"] == "auto"
            finally:
                await client.post(f"{base}/ai/skills/{skill_id}/disable")
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_real_model_auto_activates_a_matching_skill() -> None:
    if os.environ.get("TAP_RUN_TAPPER_REAL_MODEL_SMOKE") != "1":
        pytest.skip("real Skill activation model smoke requires explicit opt-in")

    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        settings = TapperSettings.from_mapping(os.environ)
        assert not settings.e2e_mode
        assert settings.model_backend == "litellm"
        await _auto_activates_a_matching_skill(settings)
    finally:
        logging.disable(previous_log_disable)
