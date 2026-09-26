"""Append-only ClickHouse projection with explicit query-time deduplication."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tap_platform.insights.application.queries import (
    MetricQuery,
    QueryLimitExceeded,
    QueryLimits,
)
from tap_platform.insights.domain.metrics import MetricAttempt, MetricWindow
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
        self,
        snapshot: ProjectionSnapshot,
        *,
        project_id: str | None = None,
        limits: QueryLimits | None = None,
        query: MetricQuery | None = None,
        window: MetricWindow | None = None,
        deadline: float | None = None,
    ) -> list[ProjectedAttempt]:
        selected = self._selected_markers(
            snapshot,
            project_id=project_id,
            limits=_remaining_limits(limits, deadline),
            query=query,
        )
        if not selected:
            return []
        rows = self._visible_rows(
            "attempt_facts",
            snapshot,
            project_id=project_id,
            limits=_remaining_limits(limits, deadline),
            query=query,
            window=window,
        )
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

    def run_dimensions(
        self,
        snapshot: ProjectionSnapshot,
        *,
        project_id: str | None = None,
        limits: QueryLimits | None = None,
    ) -> list[RunDimension]:
        selected = self._selected_markers(
            snapshot, project_id=project_id, limits=limits
        )
        rows = self._visible_rows(
            "run_dimensions", snapshot, project_id=project_id, limits=limits
        )
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
                    started_at=_datetime(row, "started_at"),
                    finished_at=_datetime(row, "finished_at"),
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

    def query_attempts(
        self,
        *,
        snapshot: ProjectionSnapshot,
        project_id: str,
        query: MetricQuery,
        window: MetricWindow,
        limits: QueryLimits,
    ) -> list[MetricAttempt]:
        """Execute only the fixed, project-scoped effective-fact templates."""
        try:
            deadline = time.monotonic() + limits.timeout_seconds
            attempts = self.effective_attempts(
                snapshot,
                project_id=project_id,
                limits=limits,
                query=query,
                window=window,
                deadline=deadline,
            )
        except ClickHouseProjectionError as exc:
            detail = str(exc).lower()
            if "timeout_exceeded" in detail or "maximum execution time" in detail:
                raise TimeoutError("ClickHouse query timed out") from exc
            if any(
                token in detail
                for token in (
                    "limit exceeded",
                    "limit for rows",
                    "limit for bytes",
                    "too many rows",
                    "too_many_rows",
                    "too_many_bytes",
                    "memory limit",
                    "memory_limit_exceeded",
                )
            ):
                raise QueryLimitExceeded("ClickHouse query limit exceeded") from exc
            raise
        results: list[MetricAttempt] = []
        for item in attempts:
            results.append(
                MetricAttempt(
                    fact_key=item.fact_key,
                    receipt_id=item.receipt_id,
                    project_id=item.project_id,
                    source_id=item.source_id,
                    external_run_id=item.external_run_id,
                    stable_test_id=item.stable_test_id,
                    source_test_identity=item.source_test_identity,
                    data_row=item.data_row,
                    application_commit=item.application_commit,
                    script_commit=item.script_commit,
                    environment=item.environment,
                    configuration=item.configuration,
                    attempt=item.attempt,
                    result=item.result,
                    duration_seconds=item.duration_seconds,
                    first_attempt_eligible=item.first_attempt_eligible,
                    missing_reasons=item.missing_reasons,
                    run_started_at=item.started_at,
                )
            )
        return results

    def effective_checksum(self, snapshot: ProjectionSnapshot) -> str:
        checksums = sorted(
            item.fact_checksum for item in self.effective_attempts(snapshot)
        )
        return hashlib.sha256("\n".join(checksums).encode()).hexdigest()

    def _selected_markers(
        self,
        snapshot: ProjectionSnapshot,
        *,
        project_id: str | None = None,
        limits: QueryLimits | None = None,
        query: MetricQuery | None = None,
    ) -> dict[tuple[str, ...], int]:
        rows = self._visible_rows(
            "report_batch_markers",
            snapshot,
            project_id=project_id,
            limits=limits,
            query=query,
        )
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
        self,
        table: str,
        snapshot: ProjectionSnapshot,
        *,
        project_id: str | None = None,
        limits: QueryLimits | None = None,
        query: MetricQuery | None = None,
        window: MetricWindow | None = None,
    ) -> list[dict[str, Any]]:
        if table not in {
            "attempt_facts",
            "evidence_refs",
            "report_batch_markers",
            "run_dimensions",
        }:
            raise ValueError("ClickHouse query table is not allowlisted")
        projection_version = _quote(snapshot.projection_version)
        prefix = "fact." if table == "attempt_facts" else ""
        select_clause = "*"
        from_clause = table
        if table == "attempt_facts":
            select_clause = (
                "fact.*, coalesce(fact.started_at, run.started_at) "
                "AS resolved_started_at"
            )
            from_clause = (
                "attempt_facts AS fact ANY LEFT JOIN run_dimensions AS run ON "
                "run.projection_version = fact.projection_version "
                "AND run.data_version = fact.data_version "
                "AND run.projection_batch_id = fact.projection_batch_id "
                "AND run.project_id = fact.project_id "
                "AND run.source_id = fact.source_id "
                "AND run.external_run_id = fact.external_run_id "
                "AND run.report_batch_id = fact.report_batch_id "
                "AND run.shard_id = fact.shard_id "
                "AND run.correction_no = fact.correction_no"
            )
        project_clause = (
            f" AND {prefix}project_id = {_quote(project_id)}"
            if project_id is not None
            else ""
        )
        scope_clause = _query_scope_clause(query, prefix=prefix)
        window_clause = ""
        if table == "attempt_facts" and window is not None:
            window_clause = (
                " AND (coalesce(fact.started_at, run.started_at) IS NULL OR "
                "parseDateTimeBestEffortOrNull(coalesce(fact.started_at, run.started_at)) >= "
                f"parseDateTimeBestEffort({_quote(window.start.isoformat())}) "
                "AND parseDateTimeBestEffortOrNull(coalesce(fact.started_at, run.started_at)) < "
                f"parseDateTimeBestEffort({_quote(window.end.isoformat())}))"
            )
        settings = ""
        if limits is not None:
            settings = (
                " SETTINGS"
                f" max_rows_to_read = {limits.max_rows_to_read},"
                f" max_bytes_to_read = {limits.max_bytes_to_read},"
                f" max_memory_usage = {limits.max_memory_bytes},"
                f" max_result_rows = {limits.max_rows_to_read},"
                f" max_result_bytes = {limits.max_bytes_to_read},"
                f" max_execution_time = {limits.timeout_seconds},"
                " max_threads = 1,"
                " read_overflow_mode = 'throw',"
                " result_overflow_mode = 'throw',"
                " timeout_overflow_mode = 'throw'"
            )
        return self._json_query(
            f"SELECT {select_clause} FROM {from_clause} "
            f"WHERE {prefix}projection_version = {projection_version} "
            f"AND {prefix}data_version <= {snapshot.visible_data_version} "
            f"{project_clause}{scope_clause}{window_clause}{settings} FORMAT JSONEachRow",
            timeout_seconds=(limits.timeout_seconds if limits is not None else None),
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
            started_at=_datetime(row, "resolved_started_at"),
        )

    def _insert_rows(self, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        if not rows:
            return
        payload = b"\n".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            for row in rows
        )
        self._execute(f"INSERT INTO {table} FORMAT JSONEachRow", payload)

    def _json_query(
        self, sql: str, *, timeout_seconds: float | None = None
    ) -> list[dict[str, Any]]:
        raw = (
            self._execute(sql)
            if timeout_seconds is None
            else self._execute(sql, timeout_seconds=timeout_seconds)
        )
        return [json.loads(line) for line in raw.splitlines() if line]

    def _execute(
        self,
        sql: str,
        body: bytes | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
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
            timeout = (
                self._timeout_seconds
                if timeout_seconds is None
                else min(self._timeout_seconds, timeout_seconds)
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise ClickHouseProjectionError(
                f"ClickHouse request failed ({exc.code}): {detail}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("ClickHouse query timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("ClickHouse query timed out") from exc
            raise ClickHouseProjectionError("ClickHouse is unavailable") from exc


def _scope_key(value: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(value[field]) for field in _SCOPE_FIELDS)


def _optional(value: dict[str, Any], field: str) -> str | None:
    item = value.get(field)
    return str(item) if item is not None else None


def _datetime(value: dict[str, Any], field: str) -> datetime | None:
    item = value.get(field)
    if item is None:
        return None
    parsed = datetime.fromisoformat(str(item).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _query_scope_clause(query: MetricQuery | None, *, prefix: str = "") -> str:
    if query is None:
        return ""
    fields = (
        ("source_id", query.filters.source_ids),
        ("external_run_id", query.filters.run_ids),
        ("environment", query.filters.environments),
        ("configuration", query.filters.configurations),
    )
    return "".join(
        f" AND {prefix}{field} IN ({','.join(_quote(value) for value in values)})"
        for field, values in fields
        if values
    )


def _remaining_limits(
    limits: QueryLimits | None, deadline: float | None
) -> QueryLimits | None:
    if limits is None or deadline is None:
        return limits
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ClickHouse query timed out")
    from dataclasses import replace

    return replace(limits, timeout_seconds=remaining)
