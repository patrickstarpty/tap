"""Strict recoverable Server-Sent Event rendering."""

import json
from collections.abc import Iterable, Mapping


def encode_sse(events: Iterable[Mapping[str, object]], *, last_event_id: str | None = None) -> str:
    try:
        resume = 0 if last_event_id is None else int(last_event_id)
    except ValueError as error:
        raise ValueError("Last-Event-ID must be an integer sequence") from error
    if resume < 0:
        raise ValueError("Last-Event-ID must be nonnegative")
    parts = []
    for event in events:
        sequence = event["sequence"]
        if type(sequence) is not int or sequence <= resume:
            continue
        event_type = event["eventType"]
        data = json.dumps(event["payload"], separators=(",", ":"))
        parts.append(f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n")
    return "".join(parts)
