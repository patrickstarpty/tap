"""Query credentials are removed completely, while ordinary engineering text survives."""

import pytest

from tap.modules.knowledge.ports.redaction import RedactionUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("contact alice@example.com now", "contact [REDACTED_EMAIL] now"),
        ("Authorization: Bearer abc.def+/=_-", "Authorization: Bearer [REDACTED_SECRET]"),
        ('api_key="a value with spaces"', 'api_key="[REDACTED_SECRET]"'),
        ("password='first\nsecond'", "password='[REDACTED_SECRET]'"),
        ("client_secret: a!b@c#d$e%f^g&h*i(j)k", "client_secret: [REDACTED_SECRET]"),
        (
            "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            "[REDACTED_PRIVATE_KEY]",
        ),
        (
            "password policy sha256:abcd /tmp/token 1234 api_key variable",
            "password policy sha256:abcd /tmp/token 1234 api_key variable",
        ),
    ],
    ids=["email", "bearer", "quoted", "multiline", "full-value", "pem", "ordinary"],
)
async def test_query_redaction_complete_values_and_false_positives(query, expected):
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    redactor = PatternEgressRedactor()
    result = await redactor.redact(query)
    assert result.sanitized_text == expected
    assert result.redaction_version == "tapper-pattern-egress-v1"
    assert (await redactor.redact(result.sanitized_text)) == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        None,
        "x" * 8001,
        "-----BEGIN PRIVATE KEY-----\nsecret",
        'password="unterminated',
        'api_key="broken\\',
    ],
    ids=["invalid-type", "over-limit", "unclosed-pem", "unclosed-quote", "dangling-escape"],
)
async def test_query_redaction_rejects_unsafe_input(query):
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    with pytest.raises(RedactionUnavailable):
        await PatternEgressRedactor().redact(query)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ['{"api_key": "synthetic-task6-credential"}', "{'password': 'synthetic-task6-credential'}"],
    ids=["json-key", "quoted-key"],
)
async def test_query_redaction_quoted_assignment_key(query):
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    assert (
        "synthetic-task6-credential"
        not in (await PatternEgressRedactor().redact(query)).sanitized_text
    )


@pytest.mark.asyncio
async def test_query_redaction_quoted_value_suffix_rejected():
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    with pytest.raises(RedactionUnavailable):
        await PatternEgressRedactor().redact('password="first"credential-suffix')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ['{"api_key\': "synthetic"}', 'api_key": "synthetic"'],
    ids=["mismatched-key", "unpaired-key"],
)
async def test_query_redaction_rejects_unpaired_assignment_key(query):
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    with pytest.raises(RedactionUnavailable):
        await PatternEgressRedactor().redact(query)


@pytest.mark.asyncio
async def test_phone_and_resident_id_are_redacted_with_counts():
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    text = "联系 13812345678 或 +86 13912345678，身份证 11010519491231002X"
    sanitized, counts = await PatternEgressRedactor(personal_data=True).redact_with_report(text)

    assert sanitized.count("[REDACTED_PHONE]") == 2
    assert sanitized.count("[REDACTED_ID]") == 1
    assert "13812345678" not in sanitized
    assert "13912345678" not in sanitized
    assert "11010519491231002X" not in sanitized
    assert counts == {"phone": 2, "id_number": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["保单号 2023101612345678901", "订单 913812345678"],
    ids=["long-digit-run", "nine-prefixed-run"],
)
async def test_long_digit_strings_are_not_misread_as_phone(text):
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    sanitized, counts = await PatternEgressRedactor(personal_data=True).redact_with_report(text)

    assert sanitized == text
    assert counts == {}


@pytest.mark.asyncio
async def test_default_redactor_does_not_redact_phone_or_id():
    """The default (query/enforcement) redactor keeps the pre-PR-4 v1 rule set:
    phone/ID numbers are a personal_data=True-only concern (C1)."""
    from tap.modules.knowledge.adapters.pattern_redaction import VERSION, PatternEgressRedactor

    text = "联系 13812345678，身份证 11010519491231002X"
    result = await PatternEgressRedactor().redact(text)

    assert result.sanitized_text == text
    assert result.redaction_version == VERSION


@pytest.mark.asyncio
async def test_personal_data_redactor_reports_v2_version():
    from tap.modules.knowledge.adapters.pattern_redaction import (
        PERSONAL_DATA_VERSION,
        PatternEgressRedactor,
    )

    result = await PatternEgressRedactor(personal_data=True).redact("联系 13812345678")

    assert result.redaction_version == PERSONAL_DATA_VERSION
    assert "13812345678" not in result.sanitized_text


@pytest.mark.asyncio
async def test_mobile_number_prefixed_by_letters_or_digits_is_not_redacted():
    """M3: CN_MOBILE needs the same letter/digit lookbehind as CN_RESIDENT_ID
    so an alphanumeric code suffix is not misread as a phone number."""
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    text = "TC13812345678"
    sanitized, counts = await PatternEgressRedactor(personal_data=True).redact_with_report(text)

    assert sanitized == text
    assert counts == {}


@pytest.mark.asyncio
async def test_existing_patterns_report_counts():
    from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor

    text = "contact alice@example.com, api_key=abc"
    sanitized, counts = await PatternEgressRedactor().redact_with_report(text)

    assert "[REDACTED_EMAIL]" in sanitized
    assert "[REDACTED_SECRET]" in sanitized
    assert counts == {"email": 1, "secret": 1}
