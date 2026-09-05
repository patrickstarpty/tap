"""Network-free deterministic model used only by Tapper's exact E2E runtime profile."""

from __future__ import annotations

import math
import re
import unicodedata
from hashlib import sha256

from tap.modules.knowledge.domain.models import Evidence
from tap.modules.knowledge.ports.documents import EmbeddingArtifact
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
)

_DIMENSION = 1536
_EMBEDDING_ALIAS = "tapper-embedding"
_ANSWER_ALIAS = "tapper-chat"
_PROFILE = "quick-hybrid-v1"


def deterministic_vector(text: str, dimension: int = _DIMENSION) -> tuple[float, ...]:
    if (
        not isinstance(text, str)
        or not text.strip()
        or type(dimension) is not int
        or dimension != 1536
    ):
        raise ValueError("deterministic embedding input must use the fixed E2E contract")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = _normalized_tokens(normalized)
    values = [0.0] * dimension
    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % dimension
        sign = -1.0 if digest[8] & 1 else 1.0
        values[bucket] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("deterministic embedding input must contain a token")
    return tuple(float(value / norm) for value in values)


class DeterministicTapperModel:
    """Implement query, document, and answer model ports without any network path."""

    def __init__(self, *, dimension: int = _DIMENSION) -> None:
        if type(dimension) is not int or dimension != _DIMENSION:
            raise ValueError("deterministic E2E dimension must equal 1536")
        self._dimension = dimension

    @property
    def embedding_model_id(self) -> str:
        return _EMBEDDING_ALIAS

    @property
    def embedding_dimension(self) -> int:
        return self._dimension

    async def embed(self, query: str) -> Embedding:
        return Embedding(
            vector=deterministic_vector(query, self._dimension),
            model_id=_EMBEDDING_ALIAS,
            provider_request_id=None,
        )

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        if not isinstance(texts, tuple) or not 1 <= len(texts) <= 32:
            raise ValueError("deterministic embedding batch must contain one to 32 texts")
        embeddings: list[Embedding] = []
        for text in texts:
            embeddings.append(await self.embed(text))
        return tuple(embeddings)

    async def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        model_alias: str,
        chunk_ids: tuple[str, ...],
    ) -> EmbeddingArtifact:
        if (
            model_alias != _EMBEDDING_ALIAS
            or not isinstance(texts, tuple)
            or not 1 <= len(texts) <= 10_000
            or not isinstance(chunk_ids, tuple)
            or len(chunk_ids) != len(texts)
            or len(set(chunk_ids)) != len(chunk_ids)
            or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in chunk_ids)
        ):
            raise ValueError(
                "deterministic document embedding input is outside the closed contract"
            )
        vectors: list[tuple[float, ...]] = []
        for offset in range(0, len(texts), 32):
            batch = texts[offset : offset + 32]
            vectors.extend(item.vector for item in await self.embed_many(batch))
        return EmbeddingArtifact(
            model_alias=model_alias,
            dimension=self._dimension,
            vectors=tuple(vectors),
            chunk_ids=chunk_ids,
        )

    async def answer(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        profile_id: str,
    ) -> AnswerGeneration:
        if (
            not isinstance(query, str)
            or not query.strip()
            or not isinstance(evidence, tuple)
            or not 1 <= len(evidence) <= 20
            or not all(isinstance(item, Evidence) for item in evidence)
            or profile_id != _PROFILE
        ):
            raise ValueError("deterministic answer input is outside the closed E2E contract")
        claims: list[GeneratedClaim] = []
        used: set[str] = set()
        for item in evidence:
            sentence = _first_evidence_sentence(item.content)
            if not sentence or sentence in used:
                continue
            used.add(sentence)
            claims.append(GeneratedClaim(text=sentence, evidence_labels=(item.evidence_label,)))
            if len(claims) == 5:
                break
        if not claims:
            raise ValueError("deterministic answer requires nonblank evidence")
        return AnswerGeneration(
            text="\n\n".join(item.text for item in claims),
            claims=tuple(claims),
            model_id=_ANSWER_ALIAS,
            profile_id=profile_id,
            provider_request_id=None,
        )


def _first_evidence_sentence(content: str) -> str:
    if not isinstance(content, str):
        return ""
    first_paragraph = next((part.strip() for part in content.split("\n\n") if part.strip()), "")
    for index, character in enumerate(first_paragraph):
        if character in "。！？" or (
            character in ".!?"
            and (index + 1 == len(first_paragraph) or first_paragraph[index + 1].isspace())
        ):
            return first_paragraph[: index + 1]
    return first_paragraph


def _normalized_tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9_]+|[^\w\s]", text, flags=re.UNICODE):
        if all("\u3400" <= character <= "\u9fff" for character in part):
            tokens.extend(part)
            tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
        else:
            tokens.append(part)
    return tuple(tokens)
