"""Export redactor masks PII and credentials before traces leave for Langfuse."""

from __future__ import annotations

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
    out = ExportRedactor().redact("订单 4111111111111112，编号 110105194912310021")
    assert "4111111111111112" in out and "110105194912310021" in out


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
