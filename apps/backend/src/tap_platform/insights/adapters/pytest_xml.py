"""Explicit pytest XML profile; legacy JUnit semantics remain frozen."""

from collections import Counter
from dataclasses import replace
import hashlib

from tap_platform.insights.adapters.junit import JUnitCase, parse_junit_cases
from tap_platform.insights.domain.reports import ReportManifest, TestAttemptFact


def parse_pytest(raw: bytes, manifest: ReportManifest) -> list[TestAttemptFact]:
    return [fact for fact, _ in parse_pytest_cases(raw, manifest)]


def parse_pytest_cases(
    raw: bytes, manifest: ReportManifest
) -> list[tuple[TestAttemptFact, JUnitCase]]:
    # One bounded streaming pass enforces encoding, DTD, depth and size limits.
    cases = parse_junit_cases(raw, manifest)
    counts = Counter(fact.source_test_identity for fact, _ in cases)
    result = []
    for fact, case in cases:
        stable_id = fact.stable_test_id or (
            "pytest-" + hashlib.sha256(fact.source_test_identity.encode()).hexdigest()
            if case.identified
            else None
        )
        # A complete, unique pytest testcase is the first/only attempt. Retry
        # plugin summaries and duplicate nodes do not prove their full history.
        single = (
            manifest.contains_complete_attempts
            and counts[fact.source_test_identity] == 1
            and not case.has_retry
            and "attempt-number-invalid" not in fact.missing_reasons
        )
        attempt = fact.attempt if fact.attempt is not None else (1 if single else None)
        if case.has_retry:
            attempt = None
        missing = set(fact.missing_reasons)
        if stable_id:
            missing.discard("stable-test-id-missing")
        if attempt is not None:
            missing.discard("attempt-number-missing")
        if case.has_retry:
            missing.add("pytest-retry-history-unavailable")
        result.append(
            (
                replace(
                    fact,
                    stable_test_id=stable_id,
                    attempt=attempt,
                    missing_reasons=tuple(sorted(missing)),
                    first_attempt_eligible=bool(
                        stable_id
                        and attempt == 1
                        and manifest.contains_complete_attempts
                    ),
                ),
                case,
            )
        )
    return result
