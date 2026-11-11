import pytest

from tap.interfaces.http.browser_capabilities import BrowserCapabilities


def test_capability_is_private_short_lived_and_task_bound():
    now = [10.0]
    registry = BrowserCapabilities(clock=lambda: now[0], ttl_seconds=30, capacity=2)
    token, grant = registry.issue("project", "task", "session")
    assert len(token) >= 32
    assert token not in repr(registry)
    assert registry.verify(token, "project", "task", "session") == grant
    for binding in [
        ("other", "task", "session"),
        ("project", "other", "session"),
        ("project", "task", "other"),
    ]:
        with pytest.raises(PermissionError):
            registry.verify(token, *binding)
    now[0] = 40.0
    with pytest.raises(PermissionError):
        registry.verify(token, "project", "task", "session")


def test_panels_have_distinct_controller_identity_and_revocation():
    registry = BrowserCapabilities()
    first, a = registry.issue("p", "t", "s")
    second, b = registry.issue("p", "t", "s")
    assert a.controller_id != b.controller_id
    registry.revoke_session("p", "s")
    for token in (first, second):
        with pytest.raises(PermissionError):
            registry.verify(token, "p", "t", "s")


def test_capacity_fails_closed_without_evicting_an_active_controller():
    registry = BrowserCapabilities(capacity=1)
    token, grant = registry.issue("p", "t", "s")
    with pytest.raises(RuntimeError):
        registry.issue("p", "t", "s")
    assert registry.verify(token, "p", "t", "s") == grant
