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
