"""Immutable completeness facts emitted by one document parse attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

PARSE_INVENTORY_SCHEMA = "parse-inventory-v1"
PARSER_CONFIG_SCHEMA = "parser-config-v1"
PARSER_IMPLEMENTATION_VERSION = "tapper-parser-v1"


class ParseInventoryStatus(str, Enum):
    PARSED = "parsed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    EXCLUDED = "excluded"


class ParseInventoryKind(str, Enum):
    DOCUMENT = "document"
    PAGE = "page"
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class ParseInventoryItem:
    """One stable source object and its explicit parse disposition."""

    source_revision_id: str
    item_id: str
    kind: ParseInventoryKind
    locator: str
    status: ParseInventoryStatus
    artifact_digest: str
    reason: str | None = None
    decision_actor_id: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("source revision", self.source_revision_id),
            ("inventory item", self.item_id),
            ("locator", self.locator),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        if not isinstance(self.kind, ParseInventoryKind) or not isinstance(
            self.status, ParseInventoryStatus
        ):
            raise TypeError("inventory kind and status must use the closed model")
        _validate_digest(self.artifact_digest)
        expected_id = _item_id(self.source_revision_id, self.kind, self.locator)
        if self.item_id != expected_id:
            raise ValueError("inventory item identity does not match its source locator")
        if self.status is ParseInventoryStatus.PARSED:
            if self.reason is not None or self.decision_actor_id is not None:
                raise ValueError("parsed inventory items cannot carry a disposition reason")
        elif not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("non-parsed inventory items require a reason")
        if self.status is ParseInventoryStatus.EXCLUDED:
            if not isinstance(self.decision_actor_id, str) or not self.decision_actor_id.strip():
                raise ValueError("excluded inventory items require an accountable actor")
        elif self.decision_actor_id is not None:
            raise ValueError("only excluded inventory items carry a decision actor")

    @classmethod
    def create(
        cls,
        *,
        source_revision_id: str,
        kind: ParseInventoryKind,
        locator: str,
        status: ParseInventoryStatus,
        artifact_digest: str,
        reason: str | None = None,
        decision_actor_id: str | None = None,
    ) -> ParseInventoryItem:
        return cls(
            source_revision_id=source_revision_id,
            item_id=_item_id(source_revision_id, kind, locator),
            kind=kind,
            locator=locator,
            status=status,
            artifact_digest=artifact_digest,
            reason=reason,
            decision_actor_id=decision_actor_id,
        )


def parser_config_digest(media_type: str) -> str:
    if not isinstance(media_type, str) or not media_type.strip():
        raise ValueError("parser media type must be nonblank")
    return _digest(
        {
            "inventorySchema": PARSE_INVENTORY_SCHEMA,
            "mediaType": media_type,
            "parserVersion": PARSER_IMPLEMENTATION_VERSION,
            "schema": PARSER_CONFIG_SCHEMA,
        }
    )


def parse_inventory_digest(items: tuple[ParseInventoryItem, ...]) -> str:
    if not isinstance(items, tuple) or not items:
        raise ValueError("parse inventory must be a non-empty immutable sequence")
    if not all(isinstance(item, ParseInventoryItem) for item in items):
        raise TypeError("parse inventory contains an invalid item")
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("parse inventory item identities must be unique")
    source_revisions = {item.source_revision_id for item in items}
    if len(source_revisions) != 1:
        raise ValueError("parse inventory must belong to one source revision")
    return _digest(
        {
            "items": [
                {
                    "artifactDigest": item.artifact_digest,
                    "decisionActorId": item.decision_actor_id,
                    "itemId": item.item_id,
                    "kind": item.kind.value,
                    "locator": item.locator,
                    "reason": item.reason,
                    "sourceRevisionId": item.source_revision_id,
                    "status": item.status.value,
                }
                for item in items
            ],
            "schema": PARSE_INVENTORY_SCHEMA,
        }
    )


def failed_document_inventory(
    source_revision_id: str,
    source_digest: str,
    reason: str,
    *,
    locator: str = "document:parser",
) -> tuple[ParseInventoryItem, ...]:
    return (
        ParseInventoryItem.create(
            source_revision_id=source_revision_id,
            kind=ParseInventoryKind.DOCUMENT,
            locator=locator,
            status=ParseInventoryStatus.FAILED,
            reason=reason,
            artifact_digest=source_digest,
        ),
    )


def historical_unreviewed_inventory(
    source_revision_id: str, source_digest: str
) -> tuple[ParseInventoryItem, ...]:
    return (
        ParseInventoryItem.create(
            source_revision_id=source_revision_id,
            kind=ParseInventoryKind.DOCUMENT,
            locator="document:legacy",
            status=ParseInventoryStatus.NEEDS_REVIEW,
            reason="historical-unreviewed",
            artifact_digest=source_digest,
        ),
    )


def _item_id(source_revision_id: str, kind: ParseInventoryKind, locator: str) -> str:
    if not isinstance(source_revision_id, str) or not source_revision_id.strip():
        raise ValueError("source revision must be nonblank")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError("inventory locator must be nonblank")
    return (
        "pi_"
        + sha256(
            _canonical_bytes(
                {
                    "kind": kind.value,
                    "locator": locator,
                    "schema": PARSE_INVENTORY_SCHEMA,
                    "sourceRevisionId": source_revision_id,
                }
            )
        ).hexdigest()
    )


def _digest(value: object) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("inventory artifact digest must be canonical sha256")
