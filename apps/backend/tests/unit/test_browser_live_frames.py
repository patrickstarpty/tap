import base64
import pytest
from tap_platform.browser_automation.live_frames import LiveFrameBroker


def frame(sequence=1):
    return {
        "frame_sequence": sequence,
        "captured_at": "2026-11-10T00:00:00Z",
        "viewport": {"width": 1280, "height": 800},
        "image_base64": base64.b64encode(b"\xff\xd8test\xff\xd9").decode(),
        "media_type": "image/jpeg",
    }


def test_live_frame_is_epoch_bound_overwritten_and_expires(tmp_path):
    clock = [10.0]
    broker = LiveFrameBroker(tmp_path / "live", clock=lambda: clock[0])
    broker.publish("s", frame(), control_epoch=1)
    assert broker.read("s", control_epoch=1)["frame_sequence"] == 1
    with pytest.raises(LookupError):
        broker.read("s", control_epoch=2)
    broker.publish("s", frame(2), control_epoch=2)
    assert len(list((tmp_path / "live").iterdir())) == 1
    clock[0] = 16
    with pytest.raises(LookupError):
        broker.read("s", control_epoch=2)
    assert not list((tmp_path / "live").iterdir())


def test_live_frame_rejects_paths_bad_bytes_and_removes_on_cancel(tmp_path):
    broker = LiveFrameBroker(tmp_path / "live")
    with pytest.raises(ValueError):
        broker.publish("../s", frame(), control_epoch=0)
    with pytest.raises(ValueError):
        broker.publish("s", {**frame(), "image_base64": "not jpeg"}, control_epoch=0)
    broker.publish("s", frame(), control_epoch=0)
    broker.delete("s")
    with pytest.raises(LookupError):
        broker.read("s", control_epoch=0)
