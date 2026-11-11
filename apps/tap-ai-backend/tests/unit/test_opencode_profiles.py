import pytest

from tap.modules.workflows.adapters.opencode_profiles import build_config


def test_frozen_roster_grants_write_to_sde_sdet_and_read_only_qae():
    request = {
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "root-r1",
            "instruction": "Guide the work.",
            "description": "Root",
        },
        "candidates": [
            {
                "agent_id": name,
                "revision_id": name + "-r1",
                "instruction": name + " instructions",
                "description": name,
            }
            for name in ("sde", "sdet", "qae")
        ],
        "requested_ids": ["sde", "sdet", "qae"],
        "model_alias": "qwen-plus",
    }
    config = build_config(request, "http://127.0.0.1:39001", "local-only", output_tokens=2048)
    assert config["model"] == config["small_model"] == "litellm/qwen-plus"
    assert config["enabled_providers"] == ["litellm"]
    assert config["agent"]["orchestrator"]["permission"]["task"] == {
        "*": "deny",
        "sde": "allow",
        "sdet": "allow",
        "qae": "allow",
    }
    assert config["agent"]["sde"]["permission"]["bash"] == "allow"
    assert config["agent"]["sdet"]["permission"]["edit"] == "allow"
    assert config["agent"]["qae"]["permission"]["bash"] == "deny"
    assert config["agent"]["qae"]["permission"]["edit"] == "deny"
    assert config["tools"]["webfetch"] is False
    assert config["tools"].get("todowrite") is not False
    assert config["tools"].get("skill") is not False
    assert config["agent"]["orchestrator"]["permission"]["tap_browser"] == "allow"
    assert config["agent"]["orchestrator"]["permission"]["todowrite"] == "allow"
    assert config["agent"]["orchestrator"]["permission"]["skill"] == "allow"
    assert "tap_browser" not in config["agent"]["sde"]["permission"]
    assert config["plugin"] == ["file:///opt/tapper/browser-tools.mjs"]
    assert config["compaction"]["auto"] is True
    assert config["provider"]["litellm"]["models"]["qwen-plus"]["limit"]["input"] == 129024
    assert config["provider"]["litellm"]["models"]["qwen-plus"]["limit"]["context"] == 129024


def test_profile_uses_configured_input_window_for_input_and_context():
    request = {
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "root-r1",
            "instruction": "Guide.",
            "description": "Root",
        },
        "candidates": [],
        "requested_ids": [],
        "model_alias": "qwen-plus",
    }
    config = build_config(
        request,
        "http://127.0.0.1:39001",
        "local-only",
        output_tokens=2048,
        input_tokens=30720,
    )
    assert config["provider"]["litellm"]["models"]["qwen-plus"]["limit"] == {
        "context": 30720,
        "input": 30720,
        "output": 2048,
    }


@pytest.mark.parametrize("input_tokens", [0, -1])
def test_profile_rejects_nonpositive_input_window(input_tokens):
    request = {
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "root-r1",
            "instruction": "Guide.",
            "description": "Root",
        },
        "candidates": [],
        "requested_ids": [],
        "model_alias": "qwen-plus",
    }
    with pytest.raises(ValueError, match="input"):
        build_config(
            request,
            "http://127.0.0.1:39001",
            "local-only",
            output_tokens=2048,
            input_tokens=input_tokens,
        )


def test_unknown_specialist_is_not_granted_permissions():
    request = {
        "root": {
            "agent_id": "orchestrator",
            "revision_id": "root-r1",
            "instruction": "Guide.",
            "description": "Root",
        },
        "candidates": [
            {"agent_id": "mystery", "revision_id": "r1", "instruction": "x", "description": "x"}
        ],
        "requested_ids": ["mystery"],
        "model_alias": "qwen-plus",
    }
    with pytest.raises(ValueError, match="specialist"):
        build_config(request, "http://127.0.0.1:39001", "local-only", output_tokens=2048)
