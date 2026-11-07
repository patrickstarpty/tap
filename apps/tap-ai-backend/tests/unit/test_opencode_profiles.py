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
