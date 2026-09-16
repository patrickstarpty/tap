from pathlib import Path

from scripts.check_brand_namespace import scan_tracked_files


def retired_name() -> str:
    return bytes((65, 116, 104, 101, 110, 97)).decode("ascii")


def test_scan_reports_case_insensitive_path_and_content(tmp_path: Path) -> None:
    token = retired_name()
    path_violation = f"src/{token.lower()}_runtime.py"
    content_violation = "src/runtime.py"
    (tmp_path / "src").mkdir()
    (tmp_path / path_violation).write_text("clean", encoding="utf-8")
    (tmp_path / content_violation).write_text(token.upper(), encoding="utf-8")

    violations = scan_tracked_files(tmp_path, (path_violation, content_violation))

    assert {(item.kind, item.path) for item in violations} == {
        ("path", path_violation),
        ("content", content_violation),
    }


def test_scan_reads_binary_bytes_and_ignores_untracked_files(tmp_path: Path) -> None:
    token = retired_name().encode("ascii")
    tracked = "assets/reference.bin"
    untracked = "assets/local.bin"
    (tmp_path / "assets").mkdir()
    (tmp_path / tracked).write_bytes(b"\x00" + token + b"\xff")
    (tmp_path / untracked).write_bytes(token)

    assert [(item.kind, item.path) for item in scan_tracked_files(tmp_path, (tracked,))] == [
        ("content", tracked),
    ]
