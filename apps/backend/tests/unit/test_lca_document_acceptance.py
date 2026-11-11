"""Keep the trusted document-state protocol identical across product adapters."""

from pathlib import Path
from tap_platform.lca.document_acceptance import DOCUMENT_ACCEPTANCE_HELPER


def test_document_state_helpers_match_authoritative_replay_protocol():
    root = Path(__file__).resolve().parents[4]
    source = (
        root
        / "apps/tap-ai-backend/src/tap/modules/workflows/application/browser_replay.py"
    ).read_text()
    expected = source.split('REPLAY_HELPERS = r"""\n', 1)[1].split(
        "// This fixed DOM reader", 1
    )[0]
    assert DOCUMENT_ACCEPTANCE_HELPER.strip() == expected.strip()
