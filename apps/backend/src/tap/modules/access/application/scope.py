"""Untrusted routing facts; deliberately contains no caller-supplied identity."""

from dataclasses import dataclass

from tap.modules.access.domain.context import require_identifier


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestFacts:
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.project_id is not None:
            require_identifier("project_id", self.project_id)
