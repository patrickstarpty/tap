from pathlib import Path

import pytest
from scripts.build_coding_image import validate_binary


def test_pinned_binary_rejects_untrusted_bytes(tmp_path: Path):
    path = tmp_path / "opencode"
    path.write_bytes(b"not the pinned binary")
    with pytest.raises(ValueError, match="digest"):
        validate_binary(path)
