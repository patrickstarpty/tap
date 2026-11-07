import asyncio
import base64
import json
import os
import time
from pathlib import Path

import pytest

from tap.modules.workflows.adapters.coding_mailbox import CodingMailbox, MailboxViolation


def _request(
    root: Path, identifier: str, *, deadline: float | None = None, payload: dict | None = None
) -> None:
    target = root / "requests" / f"{identifier}.json"
    target.write_text(
        json.dumps(
            {
                "id": identifier,
                "kind": "browser",
                "deadline_at": deadline or time.time() + 10,
                "payload": payload or {"type": "snapshot"},
            }
        )
    )
    target.chmod(0o600)


@pytest.mark.asyncio
async def test_mailbox_preserves_concurrent_identity_and_deletes_request_body(tmp_path):
    box = CodingMailbox.create(tmp_path / "box")
    _request(box.root, "one")
    _request(box.root, "two")
    calls = []

    async def handle(item):
        calls.append(item.identifier)
        return {"value": item.identifier}

    assert await box.serve_once(handle)
    assert await box.serve_once(handle)
    assert set(calls) == {"one", "two"}
    assert not list((box.root / "requests").iterdir())
    assert {p.stem for p in (box.root / "responses").iterdir()} == {"one", "two"}
    assert json.loads((box.root / "responses" / "one.json").read_text())["value"] == "one"


@pytest.mark.asyncio
async def test_mailbox_rejects_expired_duplicate_and_symlink_request(tmp_path):
    box = CodingMailbox.create(tmp_path / "box")
    _request(box.root, "old", deadline=time.time() - 1)
    with pytest.raises(MailboxViolation, match="expired"):
        await box.serve_once(lambda _: asyncio.sleep(0))
    (box.root / "requests" / "old.json").unlink()
    _request(box.root, "one")
    await box.serve_once(lambda _: asyncio.sleep(0, result={"ok": True}))
    _request(box.root, "one")
    with pytest.raises(MailboxViolation, match="duplicate"):
        await box.serve_once(lambda _: asyncio.sleep(0))
    (box.root / "requests" / "one.json").unlink()
    os.symlink("/etc/passwd", box.root / "requests" / "escape.json")
    with pytest.raises(MailboxViolation, match="symlink"):
        await box.serve_once(lambda _: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_mailbox_rejects_path_escape_and_oversized_payload(tmp_path):
    box = CodingMailbox.create(tmp_path / "box")
    _request(box.root, "bad", payload={"path": "../../etc/passwd"})
    with pytest.raises(MailboxViolation, match="path"):
        await box.serve_once(lambda _: asyncio.sleep(0))
    (box.root / "requests" / "bad.json").unlink()
    (box.root / "requests" / "large.json").write_bytes(b"x" * 196609)
    (box.root / "requests" / "large.json").chmod(0o600)
    with pytest.raises(MailboxViolation, match="size"):
        await box.serve_once(lambda _: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_mailbox_fits_full_model_response_base64_but_rejects_larger_raw_cap(tmp_path):
    box = CodingMailbox.create(tmp_path / "box")
    _request(box.root, "full")
    full = base64.b64encode(b"x" * 1_048_576).decode()
    assert await box.serve_once(
        lambda _: asyncio.sleep(
            0, result={"status": 200, "contentType": "application/json", "bodyBase64": full}
        )
    )
    assert json.loads((box.root / "responses" / "full.json").read_text())["bodyBase64"] == full
    _request(box.root, "too_large")
    over = base64.b64encode(b"x" * 1_050_000).decode()
    with pytest.raises(MailboxViolation, match="response size"):
        await box.serve_once(
            lambda _: asyncio.sleep(
                0, result={"status": 200, "contentType": "application/json", "bodyBase64": over}
            )
        )


@pytest.mark.asyncio
async def test_mailbox_host_cleans_response_only_after_exact_read_ack(tmp_path):
    box = CodingMailbox.create(tmp_path / "box")
    _request(box.root, "one")
    assert await box.serve_once(lambda _: asyncio.sleep(0, result={"bodyBase64": "eA=="}))
    response = box.root / "responses" / "one.json"
    assert response.exists()
    ack = box.root / "acks" / "one.json"
    ack.write_text("one")
    ack.chmod(0o600)
    assert not await box.serve_once(lambda _: asyncio.sleep(0))
    assert not response.exists()
    assert not ack.exists()
