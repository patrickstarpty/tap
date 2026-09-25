"""Public, repository-free projection of an authorized server answer plan."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizedAnswerQuery:
    id: str
    text: str
    depends_on: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizedAnswerExecution:
    plan_id: str
    project_id: str
    acl_digest: str
    model_alias: str
    original_question: str
    standalone_question: str
    queries: tuple[AuthorizedAnswerQuery, ...]
    template_id: str
    template_version: str
    template_digest: str
    candidate_limit: int
    evidence_limit: int
    remaining_seconds: float
    output_requirements: str = ""

    def __post_init__(self):
        if not 1 <= len(self.queries) <= 3 or len({item.id for item in self.queries}) != len(
            self.queries
        ):
            raise ValueError("authorized execution requires one to three unique queries")
        if not 1 <= self.candidate_limit <= 50 or not 1 <= self.evidence_limit <= 20:
            raise ValueError("authorized execution budget is invalid")
