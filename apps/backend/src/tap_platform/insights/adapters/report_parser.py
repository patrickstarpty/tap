"""Frozen parser dispatch shared by ingestion and projection rebuild."""

from tap_platform.insights.adapters.allure import parse_allure
from tap_platform.insights.adapters.junit import parse_junit
from tap_platform.insights.adapters.pytest_xml import parse_pytest
from tap_platform.insights.domain.reports import ReportManifest, TestAttemptFact

VERSIONS = {
    "junit": "junit-v1",
    "pytest": "pytest-junit-v1",
    "allure": "allure-results-v1",
}


def parser_version(manifest: ReportManifest) -> str:
    return VERSIONS[manifest.report_format]


def parse_report(
    raw: bytes, manifest: ReportManifest, recorded_version: str | None = None
) -> list[TestAttemptFact]:
    if recorded_version is not None and recorded_version != parser_version(manifest):
        raise RuntimeError(f"unsupported recorded parser version: {recorded_version}")
    if manifest.report_format == "allure":
        return parse_allure(raw, manifest)
    if manifest.report_format == "pytest":
        return parse_pytest(raw, manifest)
    return parse_junit(raw, manifest)
