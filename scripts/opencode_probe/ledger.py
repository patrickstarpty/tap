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
            if reservation and (
                calls + int(row["calls"]) > 120
                or cost + float(row["charged_cost_usd"]) > 2.00
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
