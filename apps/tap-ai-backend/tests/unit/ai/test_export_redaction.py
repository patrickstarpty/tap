"""Export redactor masks PII and credentials before traces leave for Langfuse."""

from __future__ import annotations

import random
import re
import time

import pytest

from tap.modules.ai.application import export_redaction as export_redaction_module
from tap.modules.ai.application.export_redaction import ExportRedactor, load_custom_rules

_TRUNCATION_MARKER = re.compile(r"\[TRUNCATED \d+ chars\]")


def assert_no_pii_digits(out: str, *values: str) -> None:
    """No 4 consecutive digits of any PII value may appear in `out` (separators ignored)."""
    compact = re.sub(r"[\s\-\u2010\u2013\u2014./_]", "", _TRUNCATION_MARKER.sub("", out))
    for value in values:
        digits = re.sub(r"\D", "", value)
        for index in range(len(digits) - 3):
            gram = digits[index : index + 4]
            assert gram not in compact, f"{gram!r} of {value!r} leaked in {out!r}"


def _luhn_check_digit(payload: str) -> str:
    total = 0
    for index, char in enumerate(reversed(payload)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def _random_digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(count))


def _random_card(rng: random.Random) -> str:
    payload = rng.choice("123456789") + _random_digits(rng, 14)
    card = payload + _luhn_check_digit(payload)
    if rng.random() < 0.5:
        return card
    return " ".join(card[i : i + 4] for i in range(0, 16, 4))


def _random_cn_phone(rng: random.Random) -> str:
    phone = "1" + rng.choice("3456789") + _random_digits(rng, 9)
    if rng.random() < 0.5:
        return phone
    sep = rng.choice(" -")
    return f"{phone[:3]}{sep}{phone[3:7]}{sep}{phone[7:]}"


def _random_hk_phone(rng: random.Random) -> str:
    phone = rng.choice("23456789") + _random_digits(rng, 7)
    return f"{phone[:4]} {phone[4:]}"


def _random_mainland_id(rng: random.Random) -> str:
    body = rng.choice("123456789") + _random_digits(rng, 16)
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum(int(d) * w for d, w in zip(body, weights, strict=True))
    return body + "10X98765432"[total % 11]


def test_pii_is_masked():
    r = ExportRedactor()
    out = r.redact(
        "联系 a.b@example.com 或 13812345678，卡 4111 1111 1111 1111，身份证 11010519491231002X"
    )
    assert "[EMAIL]" in out and "[PHONE]" in out and "[CARD_NUMBER]" in out and "[ID_NUMBER]" in out
    assert "13812345678" not in out and "4111" not in out


def test_formatted_numbers_are_masked():
    out = ExportRedactor().redact("电话 +86 138-1234-5678，香港 +852 9123 4567，ＨＫ A123456(7)")
    assert "1234-5678" not in out and "9123 4567" not in out and "A123456" not in out


def test_luhn_and_checksum_reject_random_digits():
    # 宁多勿漏 (over-masking is acceptable, leaking is not): checksum-invalid values
    # get no typed label but are still masked as [NUMBER].
    out = ExportRedactor().redact("订单 4111111111111112，编号 110105194912310021")
    assert "[ID_NUMBER]" not in out
    assert "[NUMBER]" in out

    # A date span is exempt from the [NUMBER] sweep. The adjacent 8-digit amount fits
    # the HK phone shape and is masked as [PHONE] (accepted over-masking).
    out2 = ExportRedactor().redact("日期 2026-10-14，金额 30001234 元")
    assert "2026-10-14" in out2


def test_credentials_masked_without_rejecting_whole_text():
    out = ExportRedactor().redact('ok password="hunter2" tail')
    assert "hunter2" not in out and out.startswith("ok ") and out.endswith(" tail")


def test_unterminated_private_key_fails_field():
    assert ExportRedactor().redact("-----BEGIN PRIVATE KEY-----\nabc") == "[REDACTION_FAILED]"


def test_truncates_after_redaction():
    out = ExportRedactor().redact("x" * 40000)
    assert out == "x" * 32768 + "[TRUNCATED 7232 chars]"


def test_custom_rule_and_nested_values(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("rules:\n  - name: policy\n    pattern: 'P\\d{6}'\n")
    r = ExportRedactor(load_custom_rules(p))
    assert r.redact_value({"a": ["P123456", 3]}) == {"a": ["[CUSTOM:policy]", 3]}
    # Custom rules run after the digit-span pass: 8+ digit runs are already [NUMBER].
    assert r.redact("P12345678") == "P[NUMBER]"


def test_none_passthrough():
    assert ExportRedactor().redact(None) is None


# --- Fix round 1 regressions ---


def test_card_followed_by_extra_digits_is_fully_masked():
    out = ExportRedactor().redact("card 4111111111111111 123")
    assert "4111" not in out and "1111" not in out and "[PHONE]" not in out


def test_card_with_trailing_expiry_like_digits_is_fully_masked():
    out = ExportRedactor().redact("卡 4111 1111 1111 1111 12/25")
    assert "4111" not in out and "1111" not in out and "[PHONE]" not in out


def test_dash_grouped_card_with_trailing_digits_is_fully_masked():
    out = ExportRedactor().redact("4111-1111-1111-1111-01")
    assert "4111" not in out and "1111" not in out and "[PHONE]" not in out


def test_card_preceded_by_stray_digit_is_fully_masked():
    out = ExportRedactor().redact("No.1 4111 1111 1111 1111")
    assert "4111" not in out and "1111" not in out and "[PHONE]" not in out


def test_large_adversarial_input_redacts_quickly():
    text = "a!" * 100000
    start = time.monotonic()
    ExportRedactor().redact(text)
    assert time.monotonic() - start < 1.0


def test_redact_value_handles_keys_tuples_and_sets():
    out = ExportRedactor().redact_value({"a@b.com": ("13812345678",), "s": {"13812345678"}})
    assert out == {"[EMAIL]": ("[PHONE]",), "s": {"[PHONE]"}}


def test_unexpected_exception_in_pipeline_fails_closed(monkeypatch):
    import tap.modules.ai.application.export_redaction as module

    def boom(_text: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_redact_email", boom)
    assert ExportRedactor().redact("anything") == "[REDACTION_FAILED]"


@pytest.mark.parametrize(
    "content",
    [
        "not-a-mapping",
        "rules: not-a-list",
        "rules:\n  - name: 'Bad Name'\n    pattern: 'x'\n",
        "rules:\n  - name: ok_name\n",
        "rules:\n  - name: ok_name\n    pattern: '('\n",
    ],
    ids=["not-mapping", "rules-not-list", "bad-name", "missing-pattern", "invalid-regex"],
)
def test_load_custom_rules_normalizes_errors_to_value_error(tmp_path, content):
    p = tmp_path / "r.yaml"
    p.write_text(content)
    with pytest.raises(ValueError):
        load_custom_rules(p)


def test_load_custom_rules_missing_file_stays_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_custom_rules(tmp_path / "missing.yaml")


# --- Fix round 2 regressions ---


def test_unseparated_long_digit_run_redacts_quickly():
    text = "1" * 40000
    start = time.monotonic()
    ExportRedactor().redact(text)
    assert time.monotonic() - start < 1.0


def test_separated_long_digit_run_redacts_quickly():
    text = "1 " * 20000
    start = time.monotonic()
    ExportRedactor().redact(text)
    assert time.monotonic() - start < 1.0


def test_value_straddling_match_window_edge_does_not_leak():
    window = export_redaction_module._MATCH_WINDOW
    max_chars = export_redaction_module._MAX_CHARS
    emails = "".join(f"person{i}@example.com " for i in range(20))
    straddle_start = window - 5
    filler_len = straddle_start - len(emails)
    text = emails + ("x" * filler_len) + "13812345678" + ("y" * 2000)
    out = ExportRedactor().redact(text)
    marker = re.search(r"\[TRUNCATED \d+ chars\]$", out)
    body = out[: marker.start()] if marker else out
    assert re.search(r"\d{4,}", body) is None
    overflow = len(text) - max_chars
    assert len(out) <= max_chars + len(f"[TRUNCATED {overflow} chars]")


# --- Fix round 4: single span-first digit pass ---


_LEAK_PROBES = [
    "846346484850 138 6786 9819",
    "157391645947-138-8732-2872",
    "648792 138 2233 3853",
    "901703041797 6494 5068",
    "1671117279914541 138 1077 1977",
    "3190 8833 4288",
    "1490 7518 3833 7650 13855199906",
    "0718 3300-8791 1390",
    "4111  1111  1111  1111",
    "4111111111111111 13812345678",
    "202610141234 13812345678",
    "478266025142 9123 4567",
    "41111111111111111",
    "card 41111111111111111123",
    "1234111111111111111",
    "124111111111111111",
    "411111111111111113812345678",
    "411111111111111111010519491231002X",
    "93152741916111010519491231002X",
    "+86 138 1234 5678",
    "+852 9123 4567",
]


@pytest.mark.parametrize("text", _LEAK_PROBES)
def test_no_pii_digits_leak_from_probes(text):
    assert_no_pii_digits(ExportRedactor().redact(f"x {text} y"), text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4111 1111 1111 1111", "[CARD_NUMBER]"),
        ("13812345678", "[PHONE]"),
        ("+86 138-1234-5678", "[PHONE]"),
        ("11010519491231002X", "[ID_NUMBER]"),
        ("９１２３ ４５６７", "[PHONE]"),
        ("A123456(7)", "[ID_NUMBER]"),
    ],
)
def test_clean_standalone_values_get_typed_placeholders(text, expected):
    assert ExportRedactor().redact(f"值 {text} 结束") == f"值 {expected} 结束"


@pytest.mark.parametrize(
    "text", ["日期 2026-10-14", "第 3 页 共 12 页", "HTTP 422", "编号 1234567"]
)
def test_short_numbers_and_dates_survive(text):
    assert ExportRedactor().redact(text) == text


@pytest.mark.parametrize("separator", ["", " ", "-", "  ", " - "])
def test_random_concatenations_never_leak_pii_digits(separator):
    rng = random.Random(20261014)
    generators = [_random_card, _random_cn_phone, _random_hk_phone, _random_mainland_id]
    for _ in range(1000):
        items: list[str] = []
        pii: list[str] = []
        for _ in range(rng.randint(2, 3)):
            pick = rng.randrange(len(generators) + 1)
            if pick == len(generators):
                items.append(_random_digits(rng, rng.randint(4, 12)))
            else:
                value = generators[pick](rng)
                items.append(value)
                pii.append(value)
        assert_no_pii_digits(ExportRedactor().redact(f"x {separator.join(items)} y"), *pii)


def test_double_spaced_long_digit_run_redacts_quickly():
    start = time.monotonic()
    ExportRedactor().redact("1  " * 13000)
    assert time.monotonic() - start < 1.0


# --- Fix round 5: wider separators, narrow date exemption ---


_SEPARATOR_PROBES = [
    "138\n1234\n5678",
    "9123\n4567",
    "138\xa01234\xa05678",
    "9123\xa04567",
    "4111.1111.1111.1111",
    "4111/1111/1111/1111",
    "4111_1111_1111_1111",
    "4111    1111    1111    1111",
    "138.1234.5678",
    "138 -- 1234 -- 5678",
    "138\u20131234\u20135678",
    "138\u20101234\u20105678",
    "138\u20141234\u20145678",
    "4111\t1111\t1111\t1111",
]


@pytest.mark.parametrize("text", _SEPARATOR_PROBES)
def test_no_pii_digits_leak_across_wider_separators(text):
    assert_no_pii_digits(ExportRedactor().redact(f"x {text} y"), text)


@pytest.mark.parametrize("date", ["2026-10-14", "2026/10/14", "2026.10.14"])
def test_exact_dates_survive(date):
    assert ExportRedactor().redact(f"日期 {date} 止") == f"日期 {date} 止"


@pytest.mark.parametrize(
    "text", ["2026-13-01", "2012-3456", "2099-8877", "2012-34-56", "2026-10/14", "20261014"]
)
def test_non_date_spans_are_masked(text):
    assert_no_pii_digits(ExportRedactor().redact(f"x {text} y"), text)


@pytest.mark.parametrize(
    "separator", ["\t", "\n", "\xa0", "\u2013", ".", "/", "_", "    ", " \u2014 "]
)
def test_random_concatenations_never_leak_with_wider_separators(separator):
    test_random_concatenations_never_leak_pii_digits(separator)


@pytest.mark.parametrize("text", ["1\xa0" * 20000, "1." * 20000])
def test_wide_separator_long_digit_runs_redact_quickly(text):
    start = time.monotonic()
    ExportRedactor().redact(text)
    assert time.monotonic() - start < 1.0
