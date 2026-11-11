"""Presentation-only source URL redaction; raw receipts remain proof authority."""

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SOURCE_KEYS = ("url", "source_url", "source_frame_url", "final_url")


def model_source_url(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        parts = urlsplit(value)
        if parts.scheme == "about" and parts.path in {"blank", "srcdoc"}:
            return "about:" + parts.path
        if parts.scheme in {"http", "https"} and parts.hostname:
            host = parts.hostname
            if ":" in host:
                host = f"[{host}]"
            if parts.port is not None:
                host += f":{parts.port}"
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        pass
    return "[redacted URL]"


def model_observation(observation: dict[str, Any]) -> dict[str, Any]:
    view = deepcopy(observation)

    def clean(record: Any, keys: tuple[str, ...]) -> None:
        if isinstance(record, dict):
            for key in keys:
                if key in record:
                    record[key] = model_source_url(record[key])

    def provenance(record: Any) -> None:
        if isinstance(record, dict) and isinstance(record.get("provenance"), dict):
            for source in record["provenance"].values():
                clean(source, ("source_url", "source_frame_url"))

    def outputs(record: Any) -> None:
        if isinstance(record, dict) and isinstance(record.get("outputs"), list):
            for output in record["outputs"]:
                # An output named url is business data, not source metadata.
                clean(output, ("final_url", "source_url", "source_frame_url"))
                provenance(output)

    clean(view, _SOURCE_KEYS)
    for document in view.get("documents") or []:
        clean(document, ("url",))
    for field in view.get("fields") or []:
        clean(field, ("href",))
    clean(view.get("extracted"), ("source_url", "source_frame_url"))
    provenance(view)
    outputs(view)
    outputs(view.get("result"))
    outputs(view.get("result_data"))
    return view


def model_browser_text(text: str) -> str:
    """Strip only SDK-rendered tab source URLs, never user requests or DOM values."""

    def clean_state(match: re.Match[str]) -> str:
        return re.sub(
            r"(?m)^(Tab [^:\n]+: )(\S+)",
            lambda tab: tab[1] + str(model_source_url(tab[2])),
            match[0],
        )

    return re.sub(r"<browser_state>.*?</browser_state>", clean_state, text, flags=re.DOTALL)
