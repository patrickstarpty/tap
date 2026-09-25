"""Prepare a governed current publication in the owned E2E project only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from tap.entrypoints.tapper_runtime import TapperSettings
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_documents import (
    _manifest_from_rows,
    knowledge_chunk_manifest,
    knowledge_document,
    knowledge_document_revision,
    knowledge_parse_inventory,
    knowledge_source,
)
from tap.modules.knowledge.adapters.mysql_projection import (
    MysqlProjectionCoordinator,
    knowledge_projection_state,
)
from tap.modules.knowledge.adapters.mysql_review import (
    MysqlApprovedProjectionVerifier,
    MysqlKnowledgeReviewRepository,
)
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.knowledge.domain.parse_inventory import (
    ParseInventoryItem,
    ParseInventoryKind,
    ParseInventoryStatus,
    parse_inventory_digest,
)
from tap.modules.knowledge.domain.review import (
    KnowledgePublication,
    KnowledgeReviewRevision,
    ReviewStatus,
    canonical_digest,
)
from tap.modules.knowledge.domain.sources import (
    chunk_manifest_digest,
    projection_digest,
)
from tap.platform.db.project_scope import scope_predicates
from tap.platform.db.session import create_engine_and_session_factory

EDITOR = "tapper-e2e-fixture-editor"
REVIEWER = "tapper-e2e-fixture-reviewer"
PUBLISHER = "tapper-e2e-fixture-publisher"


def _docker_output(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("owned Compose MySQL cannot be verified") from error
    if result.returncode != 0 or len(result.stdout) > 65536:
        raise ValueError("owned Compose MySQL cannot be verified")
    return result.stdout


def _require_compose_mysql(receipt: dict[str, object], port: int) -> None:
    context = receipt.get("dockerContext")
    container_id = receipt.get("mysqlContainerId")
    if (
        not isinstance(context, str)
        or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", context) is None
        or not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or _docker_output(["context", "show"]).strip() != context
    ):
        raise ValueError("owned Compose MySQL identity is invalid")
    try:
        contexts = json.loads(_docker_output(["context", "inspect", context]))
        endpoint = contexts[0]["Endpoints"]["docker"]["Host"]
        containers = json.loads(
            _docker_output(["--context", context, "container", "inspect", container_id])
        )
        container = containers[0]
        labels = container["Config"]["Labels"]
        bindings = container["NetworkSettings"]["Ports"]["3306/tcp"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("owned Compose MySQL cannot be verified") from error
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith(("unix://", "npipe://"))
        or len(containers) != 1
        or container.get("Id") != container_id
        or container.get("State", {}).get("Running") is not True
        or labels.get("com.docker.compose.project") != "tap-tapper-e2e"
        or labels.get("com.docker.compose.service") != "mysql"
        or bindings != [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    ):
        raise ValueError("owned Compose MySQL identity does not match")


def _require_owned_database(database_url: str) -> None:
    marker_name = os.environ.get("TAPPER_E2E_OWNERSHIP_FILE", "")
    marker = Path(marker_name) if marker_name else None
    url = make_url(database_url)
    if (
        marker is None
        or marker.is_symlink()
        or not marker.is_file()
        or marker.parent.is_symlink()
        or not marker.parent.name.startswith("tap-tapper-e2e.")
        or url.drivername != "mysql+asyncmy"
        or url.host != "127.0.0.1"
        or url.port is None
        or url.port == 3306
        or not 1024 <= url.port <= 65535
    ):
        raise ValueError("owned E2E database is required")
    marker_stat = marker.stat()
    directory_stat = marker.parent.stat()
    if (
        marker_stat.st_uid != os.getuid()
        or directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(marker_stat.st_mode) != 0o600
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
        or marker_stat.st_size > 4096
    ):
        raise ValueError("owned E2E database marker is invalid")
    try:
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        if (
            receipt["project"] != "tap-tapper-e2e"
            or receipt["databaseUrlSha256"]
            != hashlib.sha256(database_url.encode()).hexdigest()
            or receipt["hostPort"] != url.port
            or type(receipt["runnerPid"]) is not int
            or receipt["runnerPid"] < 1
        ):
            raise ValueError("owned E2E database marker does not match")
        os.kill(receipt["runnerPid"], 0)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("owned E2E database marker is invalid") from error
    _require_compose_mysql(receipt, url.port)


async def prepare(revision_ids: tuple[str, ...]) -> dict[str, object]:
    if (
        os.environ.get("TAP_DEMO_MODE") != "e2e"
        or os.environ.get("TAP_TAPPER_COMPOSE_PROJECT") != "tap-tapper-e2e"
    ):
        raise ValueError("publication fixture requires the owned E2E project")
    settings = TapperSettings.from_mapping(dict(os.environ))
    if settings.project_id != VALIDATION_SCOPE.project_id or not revision_ids:
        raise ValueError("publication fixture requires selected validation revisions")
    _require_owned_database(settings.database_url)
    lock_engine, _ = create_engine_and_session_factory(settings.database_url)
    coordinator = MysqlProjectionCoordinator(
        lock_engine,
        scope=VALIDATION_SCOPE,
        authority_namespace=settings.compose_project,
    )
    try:
        async with coordinator.mutation(settings.alias):
            return await _prepare_locked(revision_ids, settings)
    finally:
        await lock_engine.dispose()


async def _prepare_locked(
    revision_ids: tuple[str, ...], settings: TapperSettings
) -> dict[str, object]:
    engine, sessions = create_engine_and_session_factory(settings.database_url)
    repository = MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE)
    projection = MysqlApprovedProjectionVerifier(sessions, scope=VALIDATION_SCOPE)
    application = KnowledgeReviewApplication(repository, projection)
    try:
        current = await repository.current_publication()
        selected = set(revision_ids)
        if current is not None:
            selected.update(current.source_revision_ids)
        async with sessions() as session:
            active = (
                (
                    await session.execute(
                        select(
                            knowledge_document_revision.c.revision_id,
                            knowledge_document.c.chunk_count,
                            knowledge_document_revision.c.parse_inventory_attempt,
                            knowledge_document_revision.c.parse_inventory_digest,
                            knowledge_document_revision.c.chunk_manifest_digest,
                            knowledge_document_revision.c.projection_digest,
                        )
                        .join(
                            knowledge_document,
                            knowledge_document.c.document_id
                            == knowledge_document_revision.c.document_id,
                        )
                        .join(
                            knowledge_source,
                            knowledge_source.c.source_id
                            == knowledge_document.c.source_id,
                        )
                        .where(
                            *scope_predicates(
                                knowledge_document_revision, VALIDATION_SCOPE
                            ),
                            *scope_predicates(knowledge_document, VALIDATION_SCOPE),
                            *scope_predicates(knowledge_source, VALIDATION_SCOPE),
                            knowledge_document_revision.c.revision_id.in_(selected),
                            knowledge_document.c.current_revision_id
                            == knowledge_document_revision.c.revision_id,
                            knowledge_document.c.status == "ready",
                            knowledge_document.c.deleted_at.is_(None),
                            knowledge_source.c.deleted_at.is_(None),
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_id = {row["revision_id"]: row for row in active}
            if not set(revision_ids) <= by_id.keys():
                raise ValueError("selected revision is not active and ready")
            generation = await session.scalar(
                select(knowledge_projection_state.c.physical_collection).where(
                    *scope_predicates(knowledge_projection_state, VALIDATION_SCOPE),
                    knowledge_projection_state.c.alias_name
                    == f"tap-tapper-e2e:{settings.alias}",
                )
            )
            if not generation:
                raise ValueError("owned projection is not active")
            inventory_facts: list[dict[str, str]] = []
            manifest_facts: list[dict[str, str]] = []
            projection_facts: list[dict[str, str]] = []
            approved_items: list[str] = []
            for revision_id in sorted(by_id):
                row = by_id[revision_id]
                if not all(
                    row[key]
                    for key in (
                        "parse_inventory_attempt",
                        "parse_inventory_digest",
                        "chunk_manifest_digest",
                        "projection_digest",
                    )
                ):
                    raise ValueError(
                        "ready revision lacks durable inventory or projection"
                    )
                inventory_rows = (
                    (
                        await session.execute(
                            select(knowledge_parse_inventory)
                            .where(
                                *scope_predicates(
                                    knowledge_parse_inventory, VALIDATION_SCOPE
                                ),
                                knowledge_parse_inventory.c.source_revision_id
                                == revision_id,
                                knowledge_parse_inventory.c.attempt
                                == row["parse_inventory_attempt"],
                            )
                            .order_by(knowledge_parse_inventory.c.ordinal)
                        )
                    )
                    .mappings()
                    .all()
                )
                inventory = tuple(
                    ParseInventoryItem(
                        source_revision_id=revision_id,
                        item_id=item["item_id"],
                        kind=ParseInventoryKind(item["item_kind"]),
                        locator=item["locator"],
                        status=ParseInventoryStatus(item["status"]),
                        artifact_digest=item["artifact_digest"],
                        reason=item["reason"],
                        decision_actor_id=item["decision_actor_id"],
                    )
                    for item in inventory_rows
                )
                if (
                    not inventory
                    or parse_inventory_digest(inventory)
                    != row["parse_inventory_digest"]
                    or any(
                        item.status
                        not in {
                            ParseInventoryStatus.PARSED,
                            ParseInventoryStatus.EXCLUDED,
                        }
                        for item in inventory
                    )
                ):
                    raise ValueError("selected inventory is incomplete or unreviewed")
                approved_items.extend(
                    item.item_id
                    for item in inventory
                    if item.status is ParseInventoryStatus.PARSED
                )
                manifest_rows = (
                    (
                        await session.execute(
                            select(knowledge_chunk_manifest)
                            .where(
                                *scope_predicates(
                                    knowledge_chunk_manifest, VALIDATION_SCOPE
                                ),
                                knowledge_chunk_manifest.c.revision_id == revision_id,
                            )
                            .order_by(knowledge_chunk_manifest.c.ordinal)
                        )
                    )
                    .mappings()
                    .all()
                )
                manifest = _manifest_from_rows(list(manifest_rows))
                if (
                    not manifest
                    or len(manifest) != row["chunk_count"]
                    or tuple(item.ordinal for item in manifest)
                    != tuple(range(len(manifest)))
                    or chunk_manifest_digest(manifest) != row["chunk_manifest_digest"]
                ):
                    raise ValueError(
                        "durable chunk manifest differs from revision receipt"
                    )
                if (
                    projection_digest(
                        revision_id,
                        settings.schema_version,
                        settings.index_version,
                        manifest,
                    )
                    != row["projection_digest"]
                ):
                    raise ValueError(
                        "durable projection facts differ from revision receipt"
                    )
                inventory_facts.append(
                    {"revisionId": revision_id, "digest": row["parse_inventory_digest"]}
                )
                manifest_facts.append(
                    {"revisionId": revision_id, "digest": row["chunk_manifest_digest"]}
                )
                projection_facts.append(
                    {"revisionId": revision_id, "digest": row["projection_digest"]}
                )
        if not approved_items:
            raise ValueError("publication requires parsed inventory items")
        now = datetime.now(UTC)
        source_revision_ids = tuple(sorted(by_id))
        inventory_digest = canonical_digest(inventory_facts)
        manifest_digest = canonical_digest(manifest_facts)
        annotation_digest = canonical_digest({"annotations": []})
        dependency_digest = canonical_digest(
            {"generation": generation, "projectionDigests": projection_facts}
        )
        review = KnowledgeReviewRevision(
            review_id="krv_"
            + canonical_digest(
                {
                    "currentPublicationId": current.publication_id if current else None,
                    "generation": generation,
                    "sourceRevisionIds": source_revision_ids,
                    "inventoryDigest": inventory_digest,
                    "chunkManifestDigest": manifest_digest,
                    "dependencyDigest": dependency_digest,
                }
            )[7:39],
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=source_revision_ids,
            inventory_digest=inventory_digest,
            chunk_manifest_digest=manifest_digest,
            annotation_digest=annotation_digest,
            dependency_digest=dependency_digest,
            editor_actor_ids=(EDITOR,),
            reviewer_actor_id=None,
            expires_at=now + timedelta(days=1),
            status=ReviewStatus.DRAFT,
            version=1,
            blocking_item_ids=(),
            approved_item_ids=tuple(sorted(approved_items)),
        )
        if (
            current is not None
            and current.source_revision_ids == source_revision_ids
            and current.approved_item_ids == review.approved_item_ids
            and current.approval_digest == review.approval_digest
            and current.generation == generation
            and current.status == "published"
            and current.expires_at > now
        ):
            return await _verified_result(repository, projection, current, revision_ids)
        existing = await repository.get_review(review.review_id)
        if existing is None:
            review = await repository.create_review(review)
        elif (
            existing.approval_digest != review.approval_digest
            or existing.source_revision_ids != review.source_revision_ids
            or existing.editor_actor_ids != (EDITOR,)
        ):
            raise ValueError("fixture review intent changed")
        else:
            review = existing
        if review.status is ReviewStatus.DRAFT:
            review = await application.transition_review(
                review.review_id,
                target=ReviewStatus.CHECKING,
                actor_id=EDITOR,
                expected_version=review.version,
            )
        if review.status is ReviewStatus.CHECKING:
            review = await application.transition_review(
                review.review_id,
                target=ReviewStatus.REVIEWING,
                actor_id=EDITOR,
                expected_version=review.version,
            )
        if review.status is ReviewStatus.REVIEWING:
            review = await application.approve_review(
                review.review_id,
                actor_id=REVIEWER,
                expected_version=review.version,
                now=now,
            )
        if review.status is not ReviewStatus.APPROVED:
            raise ValueError("fixture review is not approved for publication")
        publication = await application.publish_review(
            review.review_id,
            generation=generation,
            idempotency_key=f"e2e-publish-{review.review_id}",
            actor_id=PUBLISHER,
            now=now,
        )
        return await _verified_result(repository, projection, publication, revision_ids)
    finally:
        await engine.dispose()


async def _verified_result(
    repository: MysqlKnowledgeReviewRepository,
    projection: MysqlApprovedProjectionVerifier,
    publication: KnowledgePublication,
    revision_ids: tuple[str, ...],
) -> dict[str, object]:
    persisted_review = await repository.get_review(publication.review_id)
    if (
        persisted_review is None
        or persisted_review.status is not ReviewStatus.PUBLISHED
    ):
        raise RuntimeError("governed review was not persisted")
    if await repository.current_publication() != publication:
        raise RuntimeError("current publication did not advance")
    if not await projection.verify(persisted_review, publication.generation):
        raise RuntimeError("current publication projection is not ready")
    await PublishedKnowledgeAuthority(repository).authorize_selection(
        VALIDATION_SCOPE.project_id, tuple(revision_ids)
    )
    for revision_id in revision_ids:
        if revision_id not in publication.source_revision_ids:
            raise RuntimeError("selected revision is outside publication")
    return {
        "publicationId": publication.publication_id,
        "sourceRevisionIds": list(publication.source_revision_ids),
        "approvedItemIds": list(publication.approved_item_ids),
        "generation": publication.generation,
        "editorActorIds": list(persisted_review.editor_actor_ids),
        "reviewerActorId": persisted_review.reviewer_actor_id,
        "publishedBy": publication.published_by,
        "status": publication.status,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("selected revision IDs are required")
    print(json.dumps(asyncio.run(prepare(tuple(sys.argv[1:]))), sort_keys=True))
