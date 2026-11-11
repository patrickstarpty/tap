"""Synthetic decisions for the owned acceptance site, never production routing."""

import json
import re
from typing import Any

from tap.modules.ai.domain.models import ModelRequest


def browser_fixture(request: ModelRequest) -> dict[str, Any] | None:
    if not any(t.tool_id == "browser.step" for t in request.tools):
        return None
    if request.tools[0].parameters.get("title") in {"Plan", "AgentOutput"}:
        return browser_use_fixture(request, request.context)
    context = json.loads(request.context)
    initial = context["task"]
    match = re.search(r"Core ([a-z-]+):", initial["goal"])
    if match is None or "[e2e-workflow:browser]" not in initial["goal"]:
        return None
    case = match[1]
    history = [
        json.loads(m.tool_calls[0].arguments_json)
        for m in request.messages
        if m.role == "assistant"
    ]
    last = request.messages[-1].content if request.messages else ""
    observation = json.loads(last.split(": ", 1)[1]) if last.startswith("Tool result") else {}
    controls = observation.get("controls", [])

    def target(**criteria: str) -> str:
        return next(c["ref"] for c in controls if all(c.get(k) == v for k, v in criteria.items()))

    action: dict[str, Any]
    phase = len(history)
    if phase == 0:
        action = {
            "type": "plan",
            "steps": [
                {"id": "open", "keyword": "Given", "title": "Open the requested page"},
                {"id": "operate", "keyword": "When", "title": "Apply the supplied details"},
                {"id": "verify", "keyword": "Then", "title": "Read and verify the actual result"},
            ],
        }
    elif phase == 1:
        action = {"type": "navigate", "step": "open", "url": initial["start_url"]}
    elif case == "missing-input":
        action = {
            "type": "request_input",
            "reason": "missing_input",
            "question": "Which account PIN should be used?",
        }
    elif case == "cancel":
        action = {"type": "snapshot", "step": "operate"}
    elif case == "multipage" and phase in (2, 3):
        action = {
            "type": "click",
            "step": "operate",
            "target": target(label="See details" if phase == 2 else "Finish review"),
        }
    elif case in {"lookup", "wrong-expected", "form"} and phase in (2, 3):
        is_form = case == "form"
        name = (
            ("account" if is_form else "customer")
            if phase == 2
            else ("amount" if is_form else "region")
        )
        value = re.search(rf"{name}=([A-Za-z0-9-]+)", initial["goal"])
        assert value is not None
        action = {
            "type": "select" if name == "region" else "fill",
            "step": "operate",
            "target": target(name=name),
            "value": value[1],
        }
    elif case in {"lookup", "wrong-expected", "form"} and phase == 4:
        action = {
            "type": "click",
            "step": "operate",
            "target": target(label="Check request" if case == "form" else "Find order"),
        }
    elif (case == "multipage" and phase == 4) or (case != "multipage" and phase == 5):
        action = {
            "type": "read",
            "step": "verify",
            "target": target(id="journey-result" if case == "multipage" else "result"),
            "name": "result",
            "nonempty": True,
        }
        if case == "wrong-expected":
            action["contains"] = "ORDER-WRONG"
    else:
        action = {"type": "finish"}
    return {
        "model": "fake/deterministic-chat-v1",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"browser-fixture-{phase}",
                            "function": {"name": "browser_step", "arguments": json.dumps(action)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": len(request.context.split()), "completion_tokens": 100},
    }


def browser_use_fixture(request: ModelRequest, text: str) -> dict[str, Any] | None:
    """Drive the same owned scenarios through the real browser-use SDK protocol."""
    match = re.search(r"Core ([a-z-]+):", text)
    if match is None or "[e2e-workflow:browser]" not in text:
        return None
    case = match[1]
    step = re.search(r"<step_info>Step(\d+) maximum:", text)
    if request.tools[0].parameters.get("title") == "Plan":
        phase = 1
    else:
        assert step is not None
        phase = int(step[1]) + 1
    state = text

    def index(tag: str, content: str) -> int:
        found = re.search(r"\[(\d+)\]<" + tag + r"\b[^\n]*" + content, state)
        if found is None:
            found = re.search(r"\[(\d+)\]<" + tag + r"\b[^>]*>\s*" + content, state)
        assert found is not None, f"Owned fixture element unavailable: {tag} {content}"
        return int(found[1])

    actions: list[dict[str, Any]]
    if phase == 1:
        decision = {
            "steps": [
                {"id": "open", "keyword": "Given", "title": "Open the requested page"},
                {"id": "operate", "keyword": "When", "title": "Apply the supplied details"},
                {"id": "verify", "keyword": "Then", "title": "Read and verify the actual result"},
            ]
        }
    else:
        if phase == 2:
            path = (
                "/journey"
                if case == "multipage"
                else "/review"
                if case in {"form", "missing-input"}
                else "/"
            )
            actions = [{"navigate": {"url": "http://example.test" + path}}]
        elif case == "missing-input":
            actions = [
                {
                    "tap_request_input": {
                        "reason": "missing_input",
                        "question": "Which account PIN should be used?",
                    }
                }
            ]
        elif case == "cancel":
            actions = [{"wait": {"seconds": 5}}]
        elif case == "multipage" and phase in (3, 4):
            actions = [
                {"tap_step": {"step": "operate"}},
                {"click": {"index": index("a", "See details" if phase == 3 else "Finish review")}},
            ]
        elif case != "multipage" and phase in (3, 4):
            field = (
                ("account" if case == "form" else "customer")
                if phase == 3
                else ("amount" if case == "form" else "region")
            )
            value = re.search(rf"{field}=([A-Za-z0-9-]+)", text)
            assert value is not None
            operation = "select_dropdown" if field == "region" else "input"
            actions = [
                {"tap_step": {"step": "operate"}},
                {
                    operation: {
                        "index": index("select" if field == "region" else "input", "name=" + field),
                        "text": value[1].title() if field == "region" else value[1],
                    }
                },
            ]
        elif case != "multipage" and phase == 5:
            actions = [
                {
                    "click": {
                        "index": index(
                            "button", "Check request" if case == "form" else "Find order"
                        )
                    }
                }
            ]
        elif phase == (5 if case == "multipage" else 6):
            actions = [
                {
                    "tap_verify": {
                        "step": "operate",
                        "name": "page",
                        "selector": "h1",
                        "nonempty": True,
                    }
                },
                {
                    "tap_verify": {
                        "step": "verify",
                        "name": "result",
                        "selector": "#journey-result" if case == "multipage" else "#result",
                        "nonempty": True,
                        **({"contains": "ORDER-WRONG"} if case == "wrong-expected" else {}),
                    }
                },
            ]
        else:
            actions = [{"done": {"text": "Verified the actual result", "success": True}}]
        decision = {"action": actions}
    return {
        "model": "fake/deterministic-chat-v1",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"browser-fixture-{phase}",
                            "function": {"name": "browser_step", "arguments": json.dumps(decision)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": len(request.context.split()), "completion_tokens": 100},
    }
