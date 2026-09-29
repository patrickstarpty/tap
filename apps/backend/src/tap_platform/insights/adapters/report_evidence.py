"""Read typed evidence from retained originals without a second authority store."""

from __future__ import annotations

from typing import Any

from tap_platform.insights.adapters.allure import archive_members, parse_allure_members
from tap_platform.insights.adapters.junit import parse_junit_cases
from tap_platform.insights.adapters.pytest_xml import parse_pytest_cases
from tap_platform.insights.adapters.report_parser import parser_version
from tap_platform.insights.domain.reports import (
    ReportManifest,
    ReportReceipt,
    TestAttemptFact,
    legacy_v1_fact_key,
    logical_attempt_key,
)


def report_evidence(
    raw: bytes,
    manifest: ReportManifest,
    version: str,
    *,
    receipt: ReportReceipt | None = None,
) -> dict[str, Any]:
    """Parse the retained original once and key each evidence node by attempt."""
    if version != parser_version(manifest):
        raise RuntimeError(f"unsupported recorded parser version: {version}")
    pairs: list[tuple[TestAttemptFact, dict[str, Any]]]
    if manifest.report_format == "allure":
        facts, nodes = parse_allure_members(archive_members(raw), manifest)
        pairs = [(fact, nodes[fact.source_locator]) for fact in facts]
    else:
        cases = (
            parse_pytest_cases(raw, manifest)
            if manifest.report_format == "pytest"
            else parse_junit_cases(raw, manifest)
        )
        pairs = [
            (
                fact,
                {
                    "name": case.name,
                    "status": case.status,
                    "message": case.message,
                    "trace": case.trace,
                    "steps": [],
                    "attachments": [],
                },
            )
            for fact, case in cases
        ]
    return {
        "reportFormat": manifest.report_format,
        "attempts": [
            {
                "factKey": logical_attempt_key(manifest, fact),
                # Receipts projected by the frozen v1 payload store the legacy key.
                **(
                    {"legacyFactKey": legacy_v1_fact_key(receipt, manifest, fact)}
                    if receipt is not None
                    else {}
                ),
                **node,
            }
            for fact, node in pairs
        ],
    }


def attachment_sources(details: dict[str, Any]) -> set[str]:
    sources: set[str] = set()

    def collect(node: dict[str, Any]) -> None:
        for attachment in node["attachments"]:
            if attachment["available"]:
                sources.add(attachment["source"])
        for step in node["steps"]:
            collect(step)

    for attempt in details["attempts"]:
        collect(attempt)
    return sources
