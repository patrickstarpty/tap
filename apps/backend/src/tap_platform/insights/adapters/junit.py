"""Bounded, entity-free JUnit XML mapping."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import cast
from xml.etree import ElementTree

from tap_platform.insights.adapters.report_errors import ReportSecurityError
from tap_platform.insights.domain.reports import ReportManifest, TestAttemptFact


PARSER_VERSION = "junit-v1"
_FORBIDDEN_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class JUnitSecurityError(ReportSecurityError):
    """Rejection raised by the JUnit/pytest XML parsers."""


_RETRY_TAGS = {"rerunFailure", "rerunError", "flakyFailure", "flakyError"}
_OUTCOME_TAGS = (("failure", "failed"), ("error", "broken"), ("skipped", "skipped"))


@dataclass(frozen=True, slots=True)
class JUnitCase:
    """Evidence captured from a testcase during the single streaming pass."""

    name: str
    identified: bool
    has_retry: bool
    status: str
    message: str
    trace: str


def parse_junit(
    raw: bytes,
    manifest: ReportManifest,
    *,
    max_depth: int = 64,
    max_nodes: int = 200_000,
    max_text_chars: int = 2_000_000,
) -> list[TestAttemptFact]:
    return [
        fact
        for fact, _ in parse_junit_cases(
            raw,
            manifest,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_text_chars=max_text_chars,
        )
    ]


def parse_junit_cases(
    raw: bytes,
    manifest: ReportManifest,
    *,
    max_depth: int = 64,
    max_nodes: int = 200_000,
    max_text_chars: int = 2_000_000,
) -> list[tuple[TestAttemptFact, JUnitCase]]:
    if b"\x00" in raw or raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise JUnitSecurityError("xml-encoding-forbidden")
    if _FORBIDDEN_DECLARATION.search(raw):
        raise JUnitSecurityError("xml-dtd-or-entity-forbidden")
    parser = ElementTree.XMLPullParser(events=("start", "end"))
    depth = 0
    nodes = 0
    text_chars = 0
    root_tag: str | None = None
    attempts: list[tuple[TestAttemptFact, JUnitCase]] = []
    mapping = dict(manifest.external_test_id_mapping)
    try:
        for offset in range(0, len(raw), 64 * 1024):
            parser.feed(raw[offset : offset + 64 * 1024])
            for event_value in parser.read_events():
                event, element = cast(tuple[str, ElementTree.Element], event_value)
                if event == "start":
                    depth += 1
                    nodes += 1
                    if root_tag is None:
                        root_tag = _local_name(element.tag)
                    if depth > max_depth:
                        raise JUnitSecurityError("xml-depth-limit")
                    if nodes > max_nodes:
                        raise JUnitSecurityError("xml-node-limit")
                else:
                    text_chars += len(element.text or "") + len(element.tail or "")
                    if text_chars > max_text_chars:
                        raise JUnitSecurityError("xml-text-limit")
                    if _local_name(element.tag) == "testcase":
                        attempts.append(
                            (
                                _map_testcase(
                                    element,
                                    index=len(attempts) + 1,
                                    manifest=manifest,
                                    mapping=mapping,
                                ),
                                _case_evidence(element),
                            )
                        )
                        element.clear()
                    depth -= 1
        parser.close()
    except JUnitSecurityError:
        raise
    except (ElementTree.ParseError, UnicodeError, ValueError) as exc:
        raise JUnitSecurityError("invalid-xml") from exc
    if root_tag not in {"testsuite", "testsuites"}:
        raise JUnitSecurityError("invalid-junit-root")
    return attempts


def _map_testcase(
    element: ElementTree.Element,
    *,
    index: int,
    manifest: ReportManifest,
    mapping: dict[str, str],
) -> TestAttemptFact:
    class_name = element.attrib.get("classname", "")
    display_name = element.attrib.get("name", "")
    source_identity = f"{class_name}::{display_name}"
    properties = {
        item.attrib.get("name", ""): item.attrib.get("value", item.text or "")
        for group in element
        if _local_name(group.tag) == "properties"
        for item in group
        if _local_name(item.tag) == "property"
    }
    stable_id = properties.get("tap.test_id") or mapping.get(source_identity)
    data_row = properties.get("tap.data_row") or None
    attempt_text = properties.get("tap.attempt")
    missing: list[str] = []
    if stable_id is None:
        missing.append("stable-test-id-missing")
    attempt: int | None = None
    if attempt_text is None:
        missing.append("attempt-number-missing")
    else:
        try:
            parsed_attempt = int(attempt_text)
            if not 1 <= parsed_attempt <= 2_147_483_647:
                raise ValueError
            attempt = parsed_attempt
        except ValueError:
            missing.append("attempt-number-invalid")
    if not manifest.contains_complete_attempts:
        missing.append("attempt-history-incomplete")
    attachment = properties.get("tap.attachment")
    evidence_refs: tuple[str, ...] = ()
    if attachment:
        if attachment in manifest.attachments:
            evidence_refs = (attachment,)
        else:
            missing.append(f"attachment-missing:{attachment}")
    result = "pass"
    child_names = {_local_name(child.tag) for child in element}
    if "failure" in child_names:
        result = "fail"
    elif "error" in child_names:
        result = "error"
    elif "skipped" in child_names:
        result = "skipped"
    duration: float | None = None
    if "time" in element.attrib:
        try:
            parsed_duration = float(element.attrib["time"])
            if not math.isfinite(parsed_duration) or parsed_duration < 0:
                raise ValueError
            duration = parsed_duration
        except ValueError:
            missing.append("duration-invalid")
    return TestAttemptFact(
        source_test_identity=source_identity,
        source_locator=f"testcase[{index}]",
        stable_test_id=stable_id,
        data_row=data_row,
        attempt=attempt,
        result=result,
        duration_seconds=duration,
        evidence_refs=evidence_refs,
        missing_reasons=tuple(sorted(missing)),
        first_attempt_eligible=bool(
            manifest.contains_complete_attempts and attempt == 1 and stable_id
        ),
    )


def _case_evidence(element: ElementTree.Element) -> JUnitCase:
    children = {_local_name(child.tag): child for child in reversed(element)}
    status, outcome = "passed", None
    for tag, value in _OUTCOME_TAGS:
        if tag in children:
            status, outcome = value, children[tag]
            break
    return JUnitCase(
        name=element.attrib.get("name", ""),
        identified=bool(element.attrib.get("classname") and element.attrib.get("name")),
        has_retry=any(tag in _RETRY_TAGS for tag in children),
        status=status,
        message=outcome.attrib.get("message", "") if outcome is not None else "",
        trace=(outcome.text or "") if outcome is not None else "",
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
