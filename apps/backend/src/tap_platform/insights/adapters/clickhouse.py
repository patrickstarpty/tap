"""Append-only ClickHouse projection with explicit query-time deduplication."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from tap_platform.insights.domain.projection import (
    ProjectedAttempt,
    ProjectionReservation,
    ProjectionSnapshot,
    RunDimension,
)


_DATABASE = re.compile(r"[a-z][a-z0-9_]{2,63}")
_SCOPE_FIELDS = (
    "project_id",
    "source_id",
    "external_run_id",
    "report_batch_id",
    "shard_id",
    "application_commit",
    "script_commit",
    "environment",
    "configuration",
    "timezone",
)


class ClickHouseProjectionError(RuntimeError):
    """ClickHouse rejected or returned a conflicting fact projection."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionBatch:
    reservation: ProjectionReservation
    marker: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    run_dimension: dict[str, Any]
    configuration_dimension: dict[str, Any]


class ClickHouseInsightsStore:
    """Database-scoped HTTP adapter; no query depends on MergeTree merges."""

    def __init__(
        self,
        endpoint: str,
        *,
        username: str,
        password: str,
        database: str | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        query = urllib.parse.parse_qs(parsed.query)
        selected_database = database or query.get("database", ["tap_insights"])[0]
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ClickHouse endpoint must be an HTTP(S) URL")
        if _DATABASE.fullmatch(selected_database) is None:
            raise ValueError("ClickHouse database must be a bounded identifier")
        if not username or not password:
            raise ValueError("ClickHouse credentials are required")
        self._endpoint = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
        )
        self._database = selected_database
        self._authorization = (
            "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        )
        self._timeout_seconds = timeout_seconds

    @property
    def database(self) -> str:
        return self._database

    def append_details(self, batch: ProjectionBatch) -> None:
        self._insert_rows("run_dimensions", (batch.run_dimension,))
        self._insert_rows("configuration_dimensions", (batch.configuration_dimension,))
        self._insert_rows("attempt_facts", batch.attempts)
        self._insert_rows("evidence_refs", batch.evidence)

    def append_marker(self, batch: ProjectionBatch) -> None:
        self._insert_rows("report_batch_markers", (batch.marker,))

    def raw_attempt_count(self) -> int:
        value = self._json_query(
            "SELECT count() AS count FROM attempt_facts FORMAT JSONEachRow"
        )
        return int(value[0]["count"])

    def effective_attempts(
        self, snapshot: ProjectionSnapshot
    ) -> list[ProjectedAttempt]:
        selected = self._selected_markers(snapshot)
        if not selected:
            return []
        rows = self._visible_rows("attempt_facts", snapshot)
        by_key: dict[str, dict[str, Any]] = {}
        checksums: dict[str, set[str]] = {}
        for row in rows:
            scope = _scope_key(row)
            if selected.get(scope) != int(row["correction_no"]):
                continue
            fact_key = str(row["fact_key"])
            checksums.setdefault(fact_key, set()).add(str(row["fact_checksum"]))
            by_key.setdefault(fact_key, row)
        conflicts = sorted(key for key, values in checksums.items() if len(values) > 1)
        if conflicts:
            raise ClickHouseProjectionError(
                "same fact identity/correction has conflicting content: "
                + ",".join(conflicts)
            )
        return sorted(
            (self._row_to_attempt(row) for row in by_key.values()),
            key=lambda item: (
                item.project_id,
                item.source_id,
                item.external_run_id,
                item.report_batch_id,
                item.shard_id,
                item.stable_test_id or item.source_test_identity,
                item.data_row or "",
                item.attempt or 0,
            ),
        )

    def evidence_for(self, snapshot: ProjectionSnapshot, fact_key: str) -> list[str]:
        effective_keys = {item.fact_key for item in self.effective_attempts(snapshot)}
        if fact_key not in effective_keys:
            return []
        rows = self._visible_rows("evidence_refs", snapshot)
        return sorted(
            {str(row["evidence_ref"]) for row in rows if row["fact_key"] == fact_key}
        )

    def run_dimensions(self, snapshot: ProjectionSnapshot) -> list[RunDimension]:
        selected = self._selected_markers(snapshot)
        rows = self._visible_rows("run_dimensions", snapshot)
        dimensions: dict[tuple[str, ...], RunDimension] = {}
        checksums: dict[tuple[str, ...], set[str]] = {}
        for row in rows:
            scope = _scope_key(row)
            if selected.get(scope) != int(row["correction_no"]):
                continue
            checksums.setdefault(scope, set()).add(str(row["dimension_checksum"]))
            dimensions.setdefault(
                scope,
                RunDimension(
                    project_id=str(row["project_id"]),
                    source_id=str(row["source_id"]),
                    external_run_id=str(row["external_run_id"]),
                    report_batch_id=str(row["report_batch_id"]),
                    shard_id=str(row["shard_id"]),
                    correction_no=int(row["correction_no"]),
                    application_commit=str(row["application_commit"]),
                    script_commit=str(row["script_commit"]),
                    environment=str(row["environment"]),
                    configuration=str(row["configuration"]),
                    timezone=str(row["timezone"]),
                    job_id=_optional(row, "job_id"),
                    build_id=_optional(row, "build_id"),
                    branch=_optional(row, "branch"),
                    business_cycle_id=_optional(row, "business_cycle_id"),
                ),
            )
        if any(len(values) > 1 for values in checksums.values()):
            raise ClickHouseProjectionError("run dimension content conflict")
        return sorted(
            dimensions.values(),
            key=lambda item: (
                item.project_id,
                item.source_id,
                item.external_run_id,
                item.report_batch_id,
                item.shard_id,
            ),
        )

    def effective_checksum(self, snapshot: ProjectionSnapshot) -> str:
        checksums = sorted(
            item.fact_checksum for item in self.effective_attempts(snapshot)
        )
        return hashlib.sha256("\n".join(checksums).encode()).hexdigest()

    def _selected_markers(
        self, snapshot: ProjectionSnapshot
    ) -> dict[tuple[str, ...], int]:
        rows = self._visible_rows("report_batch_markers", snapshot)
        selected: dict[tuple[str, ...], int] = {}
        marker_checksums: dict[tuple[tuple[str, ...], int], set[str]] = {}
        for row in rows:
            scope = _scope_key(row)
            correction_no = int(row["correction_no"])
            marker_checksums.setdefault((scope, correction_no), set()).add(
                str(row["marker_checksum"])
            )
            selected[scope] = max(selected.get(scope, -1), correction_no)
        if any(len(values) > 1 for values in marker_checksums.values()):
            raise ClickHouseProjectionError(
                "same report scope/correction has conflicting marker content"
            )
        return selected

    def _visible_rows(
        self, table: str, snapshot: ProjectionSnapshot
    ) -> list[dict[str, Any]]:
        projection_version = _quote(snapshot.projection_version)
        return self._json_query(
            f"SELECT * FROM {table} "
            f"WHERE projection_version = {projection_version} "
            f"AND data_version <= {snapshot.visible_data_version} "
            "FORMAT JSONEachRow"
        )

    @staticmethod
    def _row_to_attempt(row: dict[str, Any]) -> ProjectedAttempt:
        return ProjectedAttempt(
            fact_key=str(row["fact_key"]),
            fact_checksum=str(row["fact_checksum"]),
            projection_version=str(row["projection_version"]),
            data_version=int(row["data_version"]),
            projection_batch_id=str(row["projection_batch_id"]),
            receipt_id=str(row["receipt_id"]),
            project_id=str(row["project_id"]),
            source_id=str(row["source_id"]),
            external_run_id=str(row["external_run_id"]),
            report_batch_id=str(row["report_batch_id"]),
            shard_id=str(row["shard_id"]),
            correction_no=int(row["correction_no"]),
            raw_object_ref=str(row["raw_object_ref"]),
            raw_checksum=str(row["raw_checksum"]),
            parser_version=str(row["parser_version"]),
            application_commit=str(row["application_commit"]),
            script_commit=str(row["script_commit"]),
            environment=str(row["environment"]),
            configuration=str(row["configuration"]),
            timezone=str(row["timezone"]),
            source_test_identity=str(row["source_test_identity"]),
            source_locator=str(row["source_locator"]),
            stable_test_id=(
                str(row["stable_test_id"])
                if int(row["stable_test_id_present"])
                else None
            ),
            data_row=(str(row["data_row"]) if int(row["data_row_present"]) else None),
            attempt=(int(row["attempt"]) if int(row["attempt_present"]) else None),
            result=str(row["result"]),
            duration_seconds=(
                float(row["duration_seconds"])
                if row.get("duration_seconds") is not None
                else None
            ),
            missing_reasons=tuple(str(item) for item in row["missing_reasons"]),
            first_attempt_eligible=bool(row["first_attempt_eligible"]),
        )

    def _insert_rows(self, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        if not rows:
            return
        payload = b"\n".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            for row in rows
        )
        self._execute(f"INSERT INTO {table} FORMAT JSONEachRow", payload)

    def _json_query(self, sql: str) -> list[dict[str, Any]]:
        raw = self._execute(sql)
        return [json.loads(line) for line in raw.splitlines() if line]

    def _execute(self, sql: str, body: bytes | None = None) -> bytes:
        params = urllib.parse.urlencode({"database": self._database, "query": sql})
        request = urllib.request.Request(
            f"{self._endpoint}?{params}",
            data=body if body is not None else b"",
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise ClickHouseProjectionError(
                f"ClickHouse request failed ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ClickHouseProjectionError("ClickHouse is unavailable") from exc


def _scope_key(value: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(value[field]) for field in _SCOPE_FIELDS)


def _optional(value: dict[str, Any], field: str) -> str | None:
    item = value.get(field)
    return str(item) if item is not None else None


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
