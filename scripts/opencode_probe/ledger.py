"""Append-only supervisor accounting across bounded live probe scenarios."""

import json
import os
import fcntl
import math
import secrets
from pathlib import Path
from typing import Any


class ProbeLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def totals(self) -> tuple[int, float]:
        if not self.path.exists():
            return 0, 0.0
        return self._totals_rows(self.path.read_text().splitlines())

    @staticmethod
    def _limits_rows(lines: list[str]) -> tuple[int, float]:
        limits = (120, 2.0)
        for line in lines:
            row = json.loads(line)
            if row.get("kind") == "continuation_authorization":
                limits = (row["call_limit"], row["cost_limit_usd"])
        return limits

    def limits(self) -> tuple[int, float]:
        if not self.path.exists():
            return 120, 2.0
        return self._limits_rows(self.path.read_text().splitlines())

    @staticmethod
    def _totals_rows(lines: list[str]) -> tuple[int, float]:
        calls = 0
        cost = 0.0
        pending: dict[str, tuple[int, float]] = {}
        for line in lines:
            row = json.loads(line)
            if row.get("kind") == "reservation":
                reservation_id = row.get("id")
                if not isinstance(reservation_id, str) or reservation_id in pending:
                    raise ValueError("invalid probe reservation")
                pending[reservation_id] = (row["calls"], row["charged_cost_usd"])
            elif row.get("kind") == "settlement":
                reservation_id = row.get("reservation_id")
                if reservation_id not in pending:
                    raise ValueError("unknown probe reservation")
                old_calls, old_cost = pending.pop(reservation_id)
                calls -= old_calls
                cost -= old_cost
            if (
                not isinstance(row, dict)
                or type(row.get("calls")) is not int
                or row["calls"] < 0
                or not isinstance(row.get("charged_cost_usd"), (float, int))
                or row["charged_cost_usd"] < 0
                or not math.isfinite(row["charged_cost_usd"])
            ):
                raise ValueError("invalid probe ledger row")
            calls += row["calls"]
            cost += row["charged_cost_usd"]
        return calls, round(cost, 10)

    def _locked_append(self, row: dict[str, Any], *, reservation: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            existing = stream.read().splitlines()
            calls, cost = self._totals_rows(existing)
            call_limit, cost_limit = self._limits_rows(existing)
            if reservation and (
                calls + int(row["calls"]) > call_limit
                or cost + float(row["charged_cost_usd"]) > cost_limit
            ):
                raise ValueError("cumulative probe budget exhausted")
            if not reservation and row.get("kind") == "settlement":
                matching = [
                    json.loads(line)
                    for line in existing
                    if json.loads(line).get("kind") == "reservation"
                    and json.loads(line).get("id") == row.get("reservation_id")
                ]
                if len(matching) != 1:
                    raise ValueError("unknown probe reservation")
                reserved = matching[0]
                if (
                    row["calls"] > reserved["calls"]
                    or float(row["charged_cost_usd"])
                    > float(reserved["charged_cost_usd"]) + 1e-9
                ):
                    raise ValueError("probe settlement exceeds reservation")
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def authorize_continuation(
        self, grant_id: str, additional_calls: int, additional_cost_usd: float
    ) -> tuple[int, float]:
        if (
            not isinstance(grant_id, str)
            or not grant_id
            or len(grant_id) > 128
            or type(additional_calls) is not int
            or additional_calls <= 0
            or not isinstance(additional_cost_usd, (int, float))
            or additional_cost_usd <= 0
            or not math.isfinite(additional_cost_usd)
        ):
            raise ValueError("invalid continuation grant")
        try:
            fd = os.open(self.path, os.O_RDWR)
        except FileNotFoundError as error:
            raise ValueError("probe ledger missing") from error
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            existing = stream.read().splitlines()
            calls, cost = self._totals_rows(existing)
            pending: set[str] = set()
            for line in existing:
                row = json.loads(line)
                if (
                    row.get("kind") == "continuation_authorization"
                    and row.get("grant_id") == grant_id
                ):
                    if (
                        row.get("additional_calls") != additional_calls
                        or row.get("additional_cost_usd") != additional_cost_usd
                    ):
                        raise ValueError("conflicting continuation grant ID")
                    return row["call_limit"], row["cost_limit_usd"]
                if row.get("kind") == "reservation":
                    pending.add(row["id"])
                elif row.get("kind") == "settlement":
                    pending.discard(row["reservation_id"])
            if pending:
                raise ValueError("pending probe reservation")
            limits = (calls + additional_calls, round(cost + additional_cost_usd, 10))
            row = {
                "kind": "continuation_authorization",
                "grant_id": grant_id,
                "additional_calls": additional_calls,
                "additional_cost_usd": additional_cost_usd,
                "call_limit": limits[0],
                "cost_limit_usd": limits[1],
                "calls": 0,
                "charged_cost_usd": 0.0,
            }
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            return limits

    def reserve(self, scenario: str, calls: int, cost_usd: float) -> str:
        if calls <= 0 or cost_usd <= 0 or not math.isfinite(cost_usd):
            raise ValueError("invalid probe reservation")
        reservation_id = "res_" + secrets.token_hex(12)
        self._locked_append(
            {
                "kind": "reservation",
                "id": reservation_id,
                "scenario": scenario,
                "calls": calls,
                "charged_cost_usd": cost_usd,
            },
            reservation=True,
        )
        return reservation_id

    def settle(self, reservation_id: str, calls: int, cost_usd: float) -> None:
        if calls < 0 or cost_usd < 0 or not math.isfinite(cost_usd):
            raise ValueError("invalid probe settlement")
        self._locked_append(
            {
                "kind": "settlement",
                "reservation_id": reservation_id,
                "calls": calls,
                "charged_cost_usd": cost_usd,
            },
            reservation=False,
        )

    def append(
        self,
        scenario: str,
        calls: int,
        verified_cost_usd: float,
        unknown_reservation_usd: float,
    ) -> None:
        if calls < 0 or verified_cost_usd < 0 or unknown_reservation_usd < 0:
            raise ValueError("negative probe accounting")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "scenario": scenario,
            "calls": calls,
            "verified_cost_usd": verified_cost_usd,
            "unknown_reservation_usd": unknown_reservation_usd,
            "charged_cost_usd": verified_cost_usd + unknown_reservation_usd,
        }
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
