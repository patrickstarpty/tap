"""Deterministic ModelGateway for the explicitly isolated fake E2E profile."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import (
    GatewayAttempts,
    LiteLLMModelGateway,
    LiteLLMModelGatewayConfig,
    OnText,
    Redact,
    wire_tool_name,
)
from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, LiteLLMModel, LiteLLMRoutes
from tap.modules.ai.domain.models import ModelOperation, ModelRequest
from tap.modules.ai.ports.model_calls import ModelCallRecorder
from tap.testing.deterministic_model import _first_evidence_sentence, deterministic_vector

BUILT_IN_ROUTES = LiteLLMRoutes(
    {
        "qwen-plus": LiteLLMModel("qwen-plus", "Qwen Plus", "chat", True, True, False),
        "text-embedding-v4": LiteLLMModel(
            "text-embedding-v4", "text-embedding-v4", "embedding", False, False, False
        ),
    }
)


class StaticLiteLLMCatalog(LiteLLMCatalog):
    """Fixed routes for fake and test profiles; never calls `/v1/model/info`."""

    def __init__(self, routes: LiteLLMRoutes = BUILT_IN_ROUTES) -> None:
        self._static_routes = routes

    async def routes(self) -> LiteLLMRoutes:
        return self._static_routes

    async def fresh_routes(self) -> LiteLLMRoutes:
        return self._static_routes

    def cached_routes(self) -> LiteLLMRoutes | None:
        return self._static_routes

    async def aclose(self) -> None:
        return None


AnswerMode = Literal["first-sentence", "best-overlap"]


@dataclass(frozen=True, slots=True)
class ToolTurnScript:
    """One scripted model turn: `(tool_id, arguments)` calls returned in order."""

    calls: tuple[tuple[str, Mapping[str, object]], ...]


_STOP_TERMS = frozenset(
    {
        "什么",
        "哪些",
        "哪个",
        "多少",
        "多久",
        "如何",
        "怎样",
        "是否",
        "需要",
        "可以",
        "the",
        "is",
        "are",
        "what",
        "how",
        "does",
        "do",
        "of",
        "in",
        "to",
        "an",
    }
)


def overlap_terms(text: str) -> frozenset[str]:
    """Lexical terms for deterministic matching: CJK bigrams and ASCII words."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms: set[str] = set()
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9_]+", normalized):
        if part[0] >= "\u3400":
            terms.update(part[index : index + 2] for index in range(len(part) - 1))
        elif len(part) > 1 or part.isdigit():
            terms.add(part)
    return frozenset(terms - _STOP_TERMS)


def _sentences(content: str) -> list[str]:
    sentences: list[str] = []
    for line in content.splitlines():
        start = 0
        for index, character in enumerate(line):
            if character in "。！？" or (
                character in ".!?"
                and (index + 1 == len(line) or line[index + 1].isspace())
                and not (index > 0 and line[index - 1].isdigit())
            ):
                sentences.append(line[start : index + 1].strip())
                start = index + 1
        sentences.append(line[start:].strip())
    return [sentence for sentence in sentences if sentence]


def _best_overlap_answer(query: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Cite the evidence sentence sharing the most terms with the query, or abstain."""

    wanted = overlap_terms(query)
    best: tuple[int, str, str] | None = None
    for item in evidence:
        content = item.get("content")
        if not isinstance(content, str):
            continue
        for sentence in _sentences(content):
            overlap = len(wanted & overlap_terms(sentence))
            if best is None or overlap > best[0]:
                best = (overlap, sentence, item["label"])
    if best is None or best[0] < 2:
        return {"answer": "", "claims": []}
    return {"answer": best[1], "claims": [{"text": best[1], "evidenceLabels": [best[2]]}]}


class DeterministicModelGateway(LiteLLMModelGateway):
    def __init__(
        self,
        config: LiteLLMModelGatewayConfig,
        *,
        scope: ProjectScopeContext,
        redact: Redact,
        recorder: ModelCallRecorder | None = None,
        answer_mode: AnswerMode = "first-sentence",
        tool_script: Sequence[ToolTurnScript] | None = None,
    ) -> None:
        if answer_mode not in ("first-sentence", "best-overlap"):
            raise ValueError("unsupported deterministic answer mode")
        self._answer_mode = answer_mode
        self._tool_script = deque(tool_script or ())
        self._tool_call_count = 0
        super().__init__(
            config,
            scope=scope,
            redact=redact,
            catalog=StaticLiteLLMCatalog(),
            recorder=recorder,
        )

    async def _post_stream(
        self,
        request: ModelRequest,
        payload: dict[str, Any],
        attempts: GatewayAttempts,
        on_text: OnText,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        body, headers = await self._post(request, payload, attempts)
        content = body["choices"][0]["message"]["content"]
        for start in range(0, len(content), 16):
            await on_text(content[start : start + 16])
        return body, headers

    async def _post(
        self, request: ModelRequest, payload: dict[str, Any], attempts: GatewayAttempts
    ) -> tuple[dict[str, Any], httpx.Headers]:
        attempts.count = 1
        if "tools" in payload and self._tool_script:
            return self._scripted_tool_turn(request), httpx.Headers()
        if request.operation is ModelOperation.EMBED:
            vector = (
                deterministic_vector(request.context)
                if self._config.embedding_dimension == 1536
                else tuple([1.0] + [0.0] * (self._config.embedding_dimension - 1))
            )
            body: dict[str, Any] = {
                "model": "fake/deterministic-embedding-v1",
                "data": [{"index": 0, "embedding": list(vector)}],
                "usage": {"prompt_tokens": len(request.context.split())},
            }
        else:
            content = "Grounded"
            if (
                request.operation is ModelOperation.STRUCTURED
                and isinstance(request.schema, dict)
                and request.schema.get("title") == "PromptSuggestions"
            ):
                try:
                    data = json.loads(request.context)
                    if not isinstance(data, dict):
                        data = {}
                except ValueError:
                    data = {}
                locale = data.get("locale")
                suggestions = [
                    {
                        "question": (
                            f"《{source['name']}》主要包含哪些内容？"
                            if locale == "zh"
                            else f"What does {source['name']} cover?"
                        ),
                        "sourceIds": [source["id"]],
                    }
                    for source in data.get("sources", [])[:8]
                ]
                content = json.dumps({"suggestions": suggestions})
            elif request.operation is ModelOperation.STRUCTURED and (
                self._answer_mode == "best-overlap"
            ):
                try:
                    answer_context = json.loads(request.context)
                    query = str(answer_context.get("query", ""))
                    evidence = list(answer_context.get("evidence", []))
                except (ValueError, AttributeError, TypeError):
                    query, evidence = "", []
                content = json.dumps(_best_overlap_answer(query, evidence), ensure_ascii=False)
            elif request.operation is ModelOperation.STRUCTURED:
                try:
                    evidence = json.loads(request.context).get("evidence", [])
                except (ValueError, AttributeError):
                    evidence = []
                claims: list[dict[str, Any]] = []
                used = set()
                for item in evidence:
                    sentence = _first_evidence_sentence(item["content"])
                    if sentence and sentence not in used:
                        used.add(sentence)
                        claims.append({"text": sentence, "evidenceLabels": [item["label"]]})
                    if len(claims) == 5:
                        break
                output = (
                    {"answer": "\n\n".join(item["text"] for item in claims), "claims": claims}
                    if claims
                    else {"answer": "Grounded"}
                )
                content = json.dumps(output)
            body = {
                "model": "fake/deterministic-chat-v1",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": len(request.context.split()),
                    "completion_tokens": len(content.split()),
                },
            }
        return body, httpx.Headers()

    def _scripted_tool_turn(self, request: ModelRequest) -> dict[str, Any]:
        calls = []
        for name, arguments in self._tool_script.popleft().calls:
            self._tool_call_count += 1
            calls.append(
                {
                    "id": f"call-{self._tool_call_count}",
                    "type": "function",
                    "function": {
                        "name": wire_tool_name(name),
                        "arguments": json.dumps(dict(arguments), ensure_ascii=False),
                    },
                }
            )
        return {
            "model": "fake/deterministic-chat-v1",
            "choices": [
                {"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": calls}}
            ],
            "usage": {"prompt_tokens": len(request.context.split()), "completion_tokens": 1},
        }
