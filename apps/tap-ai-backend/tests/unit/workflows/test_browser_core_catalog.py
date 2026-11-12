"""Catalog acceptance setup/response parsing only; no provider or browser I/O."""

import importlib.util
import json
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[5] / "scripts/run-browser-core-e2e.py"
    spec = importlib.util.spec_from_file_location("catalog_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    rows = [
        {
            "selector": f"css=button:nth-of-type({n})",
            "tag": "button",
            "label": "Check request" if n == 8 else f"Option {n}",
        }
        for n in range(1, 9)
    ]
    request = {
        "tools": [
            {
                "function": {
                    "name": "read",
                    "parameters": {"properties": {"filePath": {}, "offset": {}, "limit": {}}},
                }
            }
        ],
        "messages": [
            {
                "role": "tool",
                "tool_call_id": f"page-{page}",
                "content": "<content>\n"
                + "\n".join(
                    f"{index}: {json.dumps(row)}"
                    for index, row in enumerate(
                        rows[(page - 1) * 4 : page * 4], start=(page - 1) * 4 + 1
                    )
                )
                + "\n</content>",
            }
            for page in (1, 2)
        ],
    }
    observed = {
        "sequence": 7,
        "control_catalog": {
            "file": "/private/catalog.jsonl",
            "page_size": 4,
            "line_count": 8,
            "catalog_count": 8,
            "omitted": 0,
        },
        "related_fields": [],
    }
    return request, observed


def test_native_pages_use_actual_metadata_and_preserve_exact_target():
    module = runner()
    request, observed = fixture()
    kind, args, proof = module.catalog_fixture_tool(
        request, observed, ["page-1", "page-2"], offset=1
    )
    assert kind == "read" and args == {
        "filePath": "/private/catalog.jsonl",
        "offset": 1,
        "limit": 4,
    }
    kind, args, proof = module.catalog_fixture_tool(request, observed, ["page-1", "page-2"])
    assert kind == "tap_browser" and args == {"selector": "css=button:nth-of-type(8)"}
    assert (
        proof["native_page_record_counts"] == [4, 4] and proof["selector_from_native_read"] is True
    )


@pytest.mark.parametrize(
    "failure", ["missing_page", "wrong_metadata", "duplicate_target", "inline_target"]
)
def test_directory_recovery_fails_closed_without_observed_proof(failure):
    request, observed = fixture()
    if failure == "missing_page":
        request["messages"].pop()
    elif failure == "wrong_metadata":
        observed["control_catalog"]["line_count"] = 7
    elif failure == "duplicate_target":
        request["messages"][0]["content"] = request["messages"][0]["content"].replace(
            "Option 1", "Check request"
        )
    else:
        observed["related_fields"] = [{"label": "Check request"}]
    with pytest.raises(ValueError):
        runner().catalog_fixture_tool(request, observed, ["page-1", "page-2"])


def test_catalog_flag_requires_explicit_owned_synthetic_form_bridge(monkeypatch, tmp_path):
    module = runner()
    binary = tmp_path / "opencode"
    binary.touch()
    monkeypatch.setattr("sys.argv", ["runner", "--lca-catalog"])
    with pytest.raises(SystemExit):
        module.parse_args()
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--lca-catalog",
            "--lca-bridge",
            "--mode",
            "synthetic",
            "--execution-mode",
            "local",
            "--case",
            "form",
            "--opencode-binary",
            str(binary),
        ],
    )
    assert module.parse_args().lca_catalog


def test_form_catalog_batches_two_reads_in_one_model_response_then_observed_click():
    import io

    module = runner()
    request, observed = fixture()
    request["tools"].append({"function": {"name": "tap_browser"}})
    request["messages"].insert(
        0, {"role": "user", "content": "Core form: account=AC-42; amount=120"}
    )
    request["messages"].insert(1, {"role": "tool", "content": json.dumps(observed)})
    model = module.SyntheticModel
    model.reviewed_bridge = model.catalog_bridge = True
    model.stages = {"form": 6}
    model.calls = {}
    model.observed = set()
    model.catalog_evidence = {}

    def call():
        raw = json.dumps(request).encode()
        handler = object.__new__(model)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.send_error = lambda *args: pytest.fail("fixture rejected valid evidence")
        handler.do_POST()
        chunk = json.loads(handler.wfile.getvalue().decode().split("\n")[0][6:])
        return chunk["choices"][0]["delta"]["tool_calls"]

    first = call()
    assert len(first) == 2 and all(tool["function"]["name"] == "read" for tool in first)
    assert [json.loads(tool["function"]["arguments"])["offset"] for tool in first] == [1, 5]
    for old_id, tool in zip(("page-1", "page-2"), first, strict=True):
        next(message for message in request["messages"] if message.get("tool_call_id") == old_id)[
            "tool_call_id"
        ] = tool["id"]
    second = call()
    assert len(second) == 1 and second[0]["function"]["name"] == "tap_browser"
    assert (
        json.loads(second[0]["function"]["arguments"])["action"]["selector"]
        == "css=button:nth-of-type(8)"
    )
    assert model.calls["form"] == 2 and model.catalog_evidence["native_read_batches"] == 1
