"""Export redactor masks PII and credentials before traces leave for Langfuse."""

from __future__ import annotations

import re
import time

import pytest

from tap.modules.ai.application import export_redaction as export_redaction_module
from tap.modules.ai.application.export_redaction import ExportRedactor, load_custom_rules


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
    # 宁多勿漏 (over-masking is acceptable, leaking is not): the checksum-invalid
    # mainland ID digits may now be swept up by the card sliding-window as an
    # incidental Luhn-valid substring, but they must never be tagged [ID_NUMBER].
    out = ExportRedactor().redact("订单 4111111111111112，编号 110105194912310021")
    assert "[ID_NUMBER]" not in out

    # A 12-digit run matches no rule (too short for a card/ID, wrong shape for a
    # phone) and must survive completely unchanged.
    out2 = ExportRedactor().redact("订单 123456789012")
    assert out2 == "订单 123456789012"


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
    p.write_text("rules:\n  - name: policy\n    pattern: 'P\\d{8}'\n")
    r = ExportRedactor(load_custom_rules(p))
    assert r.redact_value({"a": ["P12345678", 3]}) == {"a": ["[CUSTOM:policy]", 3]}


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


@pytest.mark.parametrize(
    "text",
    [
        "41111111111111111",
        "card 41111111111111111123",
        "1234111111111111111",
        "124111111111111111",
        "411111111111111113812345678",
        "411111111111111111010519491231002X",
    ],
)
def test_card_glued_to_other_digits_with_no_separator_is_fully_masked(text):
    out = ExportRedactor().redact(text)
    assert "4111" not in out and "1111" not in out


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
