"""Immutable review and publication values for governed knowledge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Literal


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    CHECKING = "checking"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    PUBLISHED = "published"
    NEEDS_REVIEW = "needs_review"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class KnowledgeReviewRevision:
    review_id: str
    project_id: str
    source_revision_ids: tuple[str, ...]
    inventory_digest: str
    chunk_manifest_digest: str
    annotation_digest: str
    dependency_digest: str
    editor_actor_ids: tuple[str, ...]
    reviewer_actor_id: str | None
    expires_at: datetime
    status: ReviewStatus
    version: int
    blocking_item_ids: tuple[str, ...]
    approved_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (("review", self.review_id), ("project", self.project_id)):
            _identifier(name, value)
        _unique_nonempty("source revisions", self.source_revision_ids)
        _unique_nonempty("editors", self.editor_actor_ids)
        _unique_nonempty("approved items", self.approved_item_ids)
        if len(set(self.blocking_item_ids)) != len(self.blocking_item_ids):
            raise ValueError("blocking item identities must be unique")
        for item in self.blocking_item_ids:
            _identifier("blocking item", item)
        for value in (
            self.inventory_digest,
            self.chunk_manifest_digest,
            self.annotation_digest,
            self.dependency_digest,
        ):
            _digest(value)
        if self.reviewer_actor_id is not None:
            _identifier("reviewer", self.reviewer_actor_id)
        if self.expires_at.utcoffset() is None:
            raise ValueError("review expiry must be timezone-aware")
        if not isinstance(self.status, ReviewStatus):
            raise TypeError("review status must use the closed model")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("review version must be positive")

    @property
    def approval_digest(self) -> str:
        return canonical_digest(
            {
                "annotationDigest": self.annotation_digest,
                "approvedItemIds": self.approved_item_ids,
                "chunkManifestDigest": self.chunk_manifest_digest,
                "dependencyDigest": self.dependency_digest,
                "inventoryDigest": self.inventory_digest,
                "sourceRevisionIds": self.source_revision_ids,
            }
        )


@dataclass(frozen=True, slots=True)
class KnowledgePublication:
    publication_id: str
    project_id: str
    review_id: str
    review_version: int
    approval_digest: str
    source_revision_ids: tuple[str, ...]
    approved_item_ids: tuple[str, ...]
    generation: str
    published_by: str
    published_at: datetime
    status: Literal["published", "withdrawn"] = "published"
    withdrawn_by: str | None = None
    withdrawn_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("publication", self.publication_id),
            ("project", self.project_id),
            ("review", self.review_id),
            ("generation", self.generation),
            ("publisher", self.published_by),
        ):
            _identifier(name, value)
        _digest(self.approval_digest)
        _unique_nonempty("publication source revisions", self.source_revision_ids)
        _unique_nonempty("publication approved items", self.approved_item_ids)
        if self.status not in {"published", "withdrawn"}:
            raise ValueError("publication status is invalid")
        if self.published_at.utcoffset() is None:
            raise ValueError("publication time must be timezone-aware")
        if self.status == "withdrawn":
            if self.withdrawn_by is None or self.withdrawn_at is None:
                raise ValueError("withdrawn publication requires actor and time")
        elif self.withdrawn_by is not None or self.withdrawn_at is not None:
            raise ValueError("published publication cannot carry withdrawal facts")


def publication_id_for(review: KnowledgeReviewRevision, generation: str) -> str:
    _identifier("generation", generation)
    return (
        "kpb_"
        + canonical_digest(
            {
                "approvalDigest": review.approval_digest,
                "generation": generation,
                "projectId": review.project_id,
                "reviewId": review.review_id,
                "reviewVersion": review.version,
            }
        )[7:39]
    )


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return "sha256:" + sha256(payload).hexdigest()


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} identity must be bounded and nonblank")


def _unique_nonempty(name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
        raise ValueError(f"{name} must be a non-empty unique tuple")
    for value in values:
        _identifier(name, value)


def _digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("review fingerprints must use canonical sha256")
