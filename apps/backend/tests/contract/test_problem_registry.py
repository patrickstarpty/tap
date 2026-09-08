"""Registered failures stay safe and identical across HTTP and SSE."""

import pytest
from pydantic import ValidationError

from tap.contracts.chat_stream import TurnFailedPayload
from tap.contracts.http import ProblemDetails

BASE = {
    "type": "https://tap.example/problems/answer-unavailable",
    "title": "Answer unavailable",
    "status": 503,
    "detail": "The answer service is currently unavailable.",
    "correlationId": "request-123",
    "retryable": True,
    "failureStage": "answer",
}


def test_registered_problem_is_shared_with_failed_stream_payload() -> None:
    value = ProblemDetails.model_validate(BASE)
    stream = TurnFailedPayload.model_validate({"problem": BASE})
    assert stream.problem == value


@pytest.mark.parametrize("field", ["correlationId", "retryable", "failureStage"])
def test_workflow_problem_requires_correlation_retry_and_stage(field: str) -> None:
    value = {key: item for key, item in BASE.items() if key != field}
    with pytest.raises(ValidationError):
        ProblemDetails.model_validate(value)


@pytest.mark.parametrize(
    "change",
    [
        {"type": "answer-unavailable"},
        {"type": "https://attacker.example/problems/answer-unavailable"},
        {"status": 500},
        {"status": "503"},
        {"retryable": False},
        {"detail": "password=secret provider stack trace"},
        {"title": "provider secret"},
        {"failureStage": "arbitrary"},
        {"correlationId": ""},
        {"correlationId": "x" * 129},
    ],
)
def test_registered_problem_rejects_contract_drift(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProblemDetails.model_validate(BASE | change)


def test_non_workflow_problem_omits_stage() -> None:
    value = {
        "type": "https://tap.example/problems/scope-mismatch",
        "title": "Scope mismatch",
        "status": 403,
        "detail": "The requested project does not match the current scope.",
        "correlationId": "request-123",
        "retryable": False,
    }
    assert "failureStage" not in ProblemDetails.model_validate(value).model_dump(
        by_alias=True, exclude_none=True
    )
    with pytest.raises(ValidationError):
        ProblemDetails.model_validate(value | {"failureStage": "answer"})


def test_exported_schema_keeps_workflow_and_safe_metadata_constraints() -> None:
    schema = ProblemDetails.model_json_schema(by_alias=True)
    variants = schema["oneOf"]
    answer = next(item for item in variants if item["properties"]["type"]["const"] == BASE["type"])
    assert answer["required"] == ["failureStage"]
    assert answer["properties"]["status"] == {"const": 503}
    assert answer["properties"]["detail"] == {
        "const": "The answer service is currently unavailable."
    }
    assert answer["properties"]["retryable"] == {"const": True}
    assert answer["properties"]["failureStage"] == {"const": "answer"}
    scope = next(
        item
        for item in variants
        if item["properties"]["type"]["const"] == "https://tap.example/problems/scope-mismatch"
    )
    assert scope["properties"]["failureStage"] == {"const": None}
    assert "required" not in scope
