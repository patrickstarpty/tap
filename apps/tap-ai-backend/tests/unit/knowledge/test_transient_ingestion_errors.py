from __future__ import annotations

from tap.modules.knowledge.ports.documents import (
    SAFE_ERROR_SUMMARIES,
    SAFE_JOB_ERRORS,
    TRANSIENT_INGESTION_ERRORS,
)


def test_transient_ingestion_errors_are_safe_closed_codes() -> None:
    assert TRANSIENT_INGESTION_ERRORS
    assert TRANSIENT_INGESTION_ERRORS <= SAFE_ERROR_SUMMARIES.keys()
    assert TRANSIENT_INGESTION_ERRORS <= SAFE_JOB_ERRORS


def test_transient_ingestion_errors_cover_unavailable_dependencies() -> None:
    assert {
        "parser-unavailable",
        "visual-analysis-unavailable",
        "embedding-unavailable",
        "index-unavailable",
        "artifact-unavailable",
    } <= TRANSIENT_INGESTION_ERRORS


def test_validation_errors_are_never_transient() -> None:
    assert TRANSIENT_INGESTION_ERRORS.isdisjoint(
        {
            "unsupported-document",
            "document-too-large",
            "empty-document",
            "invalid-document",
            "ocr-required",
            "document-too-complex",
            "visual-analysis-invalid",
            "embedding-dimension-mismatch",
            "lease-exhausted",
        }
    )


def test_lease_exhausted_is_a_safe_summarized_code() -> None:
    assert "lease-exhausted" in SAFE_JOB_ERRORS
    assert SAFE_ERROR_SUMMARIES["lease-exhausted"] == "处理多次中断，已停止重试"
