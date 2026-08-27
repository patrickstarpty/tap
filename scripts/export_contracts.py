"""Deterministically export TAP's public OpenAPI and SSE JSON Schema artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SOURCE = REPOSITORY_ROOT / "apps" / "backend" / "src"

if str(BACKEND_SOURCE) not in sys.path:
    sys.path.insert(0, str(BACKEND_SOURCE))

from tap.contracts.chat_stream import ChatEventEnvelope  # noqa: E402
from tap.contracts.http import (  # noqa: E402
    CitationPreview,
    DocumentAccepted,
    DocumentDetail,
    DocumentPage,
    LiveHealth,
    ReadyHealth,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from tap.interfaces.http.app import create_app  # noqa: E402

KNOWLEDGE_HTTP_MODELS: tuple[
    tuple[type[BaseModel], Literal["validation", "serialization"]], ...
] = (
    (DocumentAccepted, "serialization"),
    (DocumentPage, "serialization"),
    (DocumentDetail, "serialization"),
    (CitationPreview, "serialization"),
    (LiveHealth, "serialization"),
    (ReadyHealth, "serialization"),
    (RetrievalSearchRequest, "validation"),
    (RetrievalSearchResponse, "serialization"),
    (RetrievalAnswerRequest, "validation"),
    (RetrievalAnswerResponse, "serialization"),
)


def canonical_json(value: object) -> bytes:
    """Serialize JSON with repository-wide deterministic formatting."""
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def generated_contracts() -> dict[Path, bytes]:
    """Return the two public contract artifacts without writing to disk."""
    event_schema: dict[str, Any] = ChatEventEnvelope.model_json_schema(by_alias=True)
    event_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    openapi = create_app().openapi()
    _, knowledge_schema = models_json_schema(
        KNOWLEDGE_HTTP_MODELS,
        by_alias=True,
        ref_template="#/components/schemas/{model}",
    )
    openapi["components"]["schemas"].update(knowledge_schema["$defs"])
    return {
        Path("openapi/api.json"): canonical_json(openapi),
        Path("events/chat-stream.schema.json"): canonical_json(event_schema),
    }


def write_or_check(output_directory: Path, *, check: bool) -> int:
    """Write artifacts, or confirm the destination already contains the exact bytes."""
    expected_files = generated_contracts()
    mismatches: list[Path] = []

    for relative_path, expected in expected_files.items():
        destination = output_directory / relative_path
        if check:
            if not destination.is_file() or destination.read_bytes() != expected:
                mismatches.append(relative_path)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(expected)

    if mismatches:
        print("Contract artifacts are out of date:", file=sys.stderr)
        for relative_path in mismatches:
            print(f"  {relative_path}", file=sys.stderr)
        return 1
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "contracts",
        help="directory containing openapi/ and events/ artifacts",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the destination differs from deterministic output",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    return write_or_check(arguments.output_dir, check=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
