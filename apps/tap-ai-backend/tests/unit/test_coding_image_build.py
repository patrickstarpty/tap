from pathlib import Path

import pytest
from scripts.build_coding_image import validate_binary, validate_ripgrep_archive


def test_pinned_binary_rejects_untrusted_bytes(tmp_path: Path):
    path = tmp_path / "opencode"
    path.write_bytes(b"not the pinned binary")
    with pytest.raises(ValueError, match="digest"):
        validate_binary(path)


def test_pinned_ripgrep_archive_rejects_untrusted_bytes(tmp_path: Path):
    path = tmp_path / "ripgrep.tar.gz"
    path.write_bytes(b"not the pinned archive")
    with pytest.raises(ValueError, match="digest"):
        validate_ripgrep_archive(path)
