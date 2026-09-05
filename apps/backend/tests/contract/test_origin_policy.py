"""Exact configured Origin is mandatory for every mutation, before I/O."""

import pytest
from fastapi.testclient import TestClient
from test_validation_scope_http import ORIGIN, PATH, scoped_app


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:15174"},
        {"Origin": ORIGIN + "/"},
        [("Origin", ORIGIN), ("Origin", ORIGIN)],
        {"Host": "evil.example", "Origin": "http://evil.example"},
        {"User-Agent": "curl", "Sec-Fetch-Site": "same-origin"},
    ],
)
def test_bad_origin_rejected_with_correlated_problem_before_io(headers):
    app, authority, knowledge = scoped_app()
    response = TestClient(app).delete(PATH + "/doc", headers=headers)
    assert response.status_code == 403
    assert response.json()["type"] == "https://tap.example/problems/authorization-denied"
    assert response.json()["correlationId"] == response.headers["X-Correlation-ID"]
    assert authority.calls == knowledge.calls == []


def test_exact_origin_allows_mutation_and_read_needs_no_origin():
    app, authority, knowledge = scoped_app()
    client = TestClient(app)
    assert client.delete(PATH + "/doc", headers={"Origin": ORIGIN}).status_code == 204
    assert authority.calls == ["scope", "knowledge.delete"]
    assert knowledge.calls == ["delete"]
    assert client.get(PATH).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://127.0.0.1",
        "http://127.0.0.1:15175/",
        "http://user@localhost:15175",
        "http://127.0.0.1:15175?foo=bar",
        "null",
    ],
)
def test_untrusted_origin_configuration_is_rejected(origin):
    from tap.interfaces.http.app import create_app

    with pytest.raises(ValueError):
        create_app(allowed_origins=frozenset({origin}))
