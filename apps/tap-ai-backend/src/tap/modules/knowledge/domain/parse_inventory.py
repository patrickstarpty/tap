"""Immutable completeness facts emitted by one document parse attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

PARSE_INVENTORY_SCHEMA = "parse-inventory-v1"
PARSE_INVENTORY_ALIGNMENT_SCHEMA = "parse-inventory-v2"
PARSER_CONFIG_SCHEMA = "parser-config-v1"
PARSER_IMPLEMENTATION_VERSION = "tapper-parser-v2"
MAX_ORIGINAL_EXCERPT_BYTES = 16_000


@dataclass(frozen=True, slots=True)
class OriginalExcerptRange:
    """A bounded byte range proven against one immutable source artifact."""

    source_digest: str
    start_byte: int
    end_byte: int
    excerpt_digest: str

    def __post_init__(self) -> None:
        _validate_digest(self.source_digest)
        _validate_digest(self.excerpt_digest)
        if (
            type(self.start_byte) is not int
            or type(self.end_byte) is not int
            or self.start_byte < 0
            or self.end_byte <= self.start_byte
        ):
            raise ValueError("original excerpt range is invalid")
        if self.end_byte - self.start_byte > MAX_ORIGINAL_EXCERPT_BYTES:
            raise ValueError("original excerpt exceeds the byte bound")


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
    FLOW_NODE = "flow_node"
    FLOW_EDGE = "flow_edge"
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
    original_excerpt: OriginalExcerptRange | None = None
    original_alignment_reason: str | None = None

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
        if self.original_excerpt is not None:
            if not isinstance(self.original_excerpt, OriginalExcerptRange):
                raise TypeError("original excerpt must use the closed range model")
            if self.original_alignment_reason is not None:
                raise ValueError("available original alignment cannot carry a reason")
        elif self.original_alignment_reason is not None and (
            not isinstance(self.original_alignment_reason, str)
            or not self.original_alignment_reason.strip()
            or len(self.original_alignment_reason) > 128
        ):
            raise ValueError("original alignment reason is invalid")

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
        original_excerpt: OriginalExcerptRange | None = None,
        original_alignment_reason: str | None = None,
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
            original_excerpt=original_excerpt,
            original_alignment_reason=original_alignment_reason,
        )


def parser_config_digest(media_type: str) -> str:
    if not isinstance(media_type, str) or not media_type.strip():
        raise ValueError("parser media type must be nonblank")
    return _digest(
        {
            "inventorySchema": PARSE_INVENTORY_ALIGNMENT_SCHEMA,
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
    alignment_enabled = any(
        item.original_excerpt is not None or item.original_alignment_reason is not None
        for item in items
    )
    encoded_items: list[dict[str, object]] = []
    for item in items:
        encoded: dict[str, object] = {
            "artifactDigest": item.artifact_digest,
            "decisionActorId": item.decision_actor_id,
            "itemId": item.item_id,
            "kind": item.kind.value,
            "locator": item.locator,
            "reason": item.reason,
            "sourceRevisionId": item.source_revision_id,
            "status": item.status.value,
        }
        if alignment_enabled:
            encoded["originalAlignmentReason"] = item.original_alignment_reason
            encoded["originalExcerpt"] = (
                None
                if item.original_excerpt is None
                else {
                    "endByte": item.original_excerpt.end_byte,
                    "excerptDigest": item.original_excerpt.excerpt_digest,
                    "sourceDigest": item.original_excerpt.source_digest,
                    "startByte": item.original_excerpt.start_byte,
                }
            )
        encoded_items.append(encoded)
    return _digest(
        {
            "items": encoded_items,
            "schema": (
                PARSE_INVENTORY_ALIGNMENT_SCHEMA if alignment_enabled else PARSE_INVENTORY_SCHEMA
            ),
        }
    )


def original_alignment_binding_digest(
    item: ParseInventoryItem,
    *,
    attempt: int,
    parser_digest: str,
    inventory_digest: str,
) -> str | None:
    """Bind alignment metadata to the exact parse attempt and its aggregate digest."""
    if item.original_excerpt is None and item.original_alignment_reason is None:
        return None
    if type(attempt) is not int or attempt < 0:
        raise ValueError("parse inventory attempt is invalid")
    _validate_digest(parser_digest)
    _validate_digest(inventory_digest)
    return _digest(
        {
            "attempt": attempt,
            "inventoryDigest": inventory_digest,
            "itemId": item.item_id,
            "originalAlignmentReason": item.original_alignment_reason,
            "originalExcerpt": (
                None
                if item.original_excerpt is None
                else {
                    "endByte": item.original_excerpt.end_byte,
                    "excerptDigest": item.original_excerpt.excerpt_digest,
                    "sourceDigest": item.original_excerpt.source_digest,
                    "startByte": item.original_excerpt.start_byte,
                }
            ),
            "parserDigest": parser_digest,
            "schema": "original-alignment-binding-v1",
            "sourceRevisionId": item.source_revision_id,
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
