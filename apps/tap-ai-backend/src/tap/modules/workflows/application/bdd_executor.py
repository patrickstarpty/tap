"""Frozen browser contracts and receipt-driven sequencing, independent of any agent SDK."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Assertion(FrozenModel):
    kind: Literal["equals", "contains", "nonempty", "positive"]
    expected: str | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.kind in {"equals", "contains"}:
            if self.expected is None or (self.kind == "contains" and not self.expected):
                raise ValueError("comparison requires an expected value")
        elif self.expected is not None:
            raise ValueError("this comparison does not accept an expected value")
        return self

    def matches(self, value: str) -> bool:
        if self.kind == "equals":
            return value == self.expected
        if self.kind == "contains":
            return str(self.expected) in value
        if self.kind == "nonempty":
            return bool(value.strip())
        numbers = re.findall(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return len(numbers) == 1 and Decimal(numbers[0]) > 0


class Requirement(FrozenModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["input", "result"]
    source: str = Field(min_length=1, max_length=500)
    expected: str = Field(max_length=4000)
    readback_expected: str | None = Field(default=None, max_length=4000)


class BDDStep(FrozenModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    keyword: Literal["Given", "When", "Then"]
    title: str = Field(min_length=1, max_length=200)
    operation: Literal["navigate", "click", "fill", "select", "assert"]
    requirement: str | None = None
    target_texts: tuple[str, ...] = Field(default=(), max_length=6)
    scope_text: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=4000)
    attribute: Literal["text", "value", "checked", "aria-pressed", "aria-valuenow"] = "text"
    assertion: Assertion | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.operation == "navigate":
            if self.keyword != "Given" or self.requirement or self.target_texts:
                raise ValueError("only initial Given may navigate to the frozen start URL")
        else:
            if not self.target_texts or any(
                not s.strip() or len(s) > 200 for s in self.target_texts
            ):
                raise ValueError("observed target must match a bounded business label")
        if self.operation == "assert":
            if (
                self.keyword not in {"Given", "Then"}
                or not self.assertion
                or self.value is not None
            ):
                raise ValueError("assert requires a frozen Given/Then comparison")
        elif self.assertion is not None or self.keyword == "Then":
            raise ValueError("Then is read-only; writes cannot carry mutable assertions")
        if self.operation in {"click", "fill", "select"} and self.keyword != "When":
            raise ValueError("business writes require When")
        if self.operation in {"fill", "select"} and self.value is None:
            raise ValueError("input operation requires a frozen value")
        if self.operation not in {"fill", "select"} and self.value is not None:
            raise ValueError("unexpected input value")
        return self


class NavigateStep(BDDStep):
    keyword: Literal["Given"] = "Given"
    operation: Literal["navigate"] = "navigate"
    requirement: None = None
    target_texts: tuple[str, ...] = Field(default=(), max_length=0)
    scope_text: None = None
    value: None = None
    assertion: None = None


class ClickStep(BDDStep):
    keyword: Literal["When"] = "When"
    operation: Literal["click"] = "click"
    requirement: None = None
    target_texts: tuple[str, ...] = Field(min_length=1, max_length=6)
    value: None = None
    assertion: None = None


class InputStep(BDDStep):
    keyword: Literal["When"] = "When"
    operation: Literal["fill", "select"]
    requirement: str
    target_texts: tuple[str, ...] = Field(min_length=1, max_length=6)
    value: str = Field(max_length=4000)
    assertion: None = None


class AssertionStep(BDDStep):
    keyword: Literal["Given", "Then"]
    operation: Literal["assert"] = "assert"
    target_texts: tuple[str, ...] = Field(min_length=1, max_length=6)
    value: None = None
    assertion: Assertion


ContractStep = Annotated[
    NavigateStep | ClickStep | InputStep | AssertionStep, Field(discriminator="operation")
]


class BDDPlan(FrozenModel):
    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=20)
    steps: tuple[ContractStep, ...] = Field(min_length=2, max_length=24)

    @model_validator(mode="after")
    def valid(self) -> Self:
        requirements = {r.id: r for r in self.requirements}
        if len(requirements) != len(self.requirements) or len({s.id for s in self.steps}) != len(
            self.steps
        ):
            raise ValueError("duplicate requirement or step ID")
        if self.steps[0].operation != "navigate" or any(
            s.operation == "navigate" for s in self.steps[1:]
        ):
            raise ValueError("exactly one initial navigation is required")
        verified: Counter[str] = Counter()
        written: Counter[str] = Counter()
        for step in self.steps:
            req = requirements.get(step.requirement or "")
            if step.requirement and not req:
                raise ValueError("unknown requirement")
            if step.keyword == "Then":
                if req is None:
                    raise ValueError("Then must verify a declared requirement")
                verified[req.id] += 1
                if req.kind == "input":
                    if written[req.id] != 1:
                        raise ValueError("input verification must follow its When")
                    check = step.assertion
                    assert check is not None
                    expected = (
                        req.readback_expected
                        if req.readback_expected is not None
                        else "true"
                        if step.attribute in {"checked", "aria-pressed"}
                        else req.expected
                    )
                    if check.kind != "equals" or check.expected != expected:
                        raise ValueError("input assertion cannot weaken the requested value")
                    if step.attribute in {
                        "checked",
                        "aria-pressed",
                    } and req.expected.casefold() not in [t.casefold() for t in step.target_texts]:
                        raise ValueError(
                            "selected-state assertion must identify the requested option"
                        )
            elif step.keyword == "When" and req:
                if req.kind != "input" or verified[req.id]:
                    raise ValueError("cannot change a result or an already verified input")
                written[req.id] += 1
                if step.operation not in {"fill", "select"} or step.value != req.expected:
                    raise ValueError("input action must use the declared frozen value")
            elif step.operation in {"fill", "select"}:
                raise ValueError("every input write must reference a requirement")
        if any(
            verified[r.id] != 1 or (r.kind == "input" and written[r.id] != 1)
            for r in self.requirements
        ):
            raise ValueError(
                "each requirement needs exactly one verification and each input one write"
            )
        if not any(r.kind == "result" for r in self.requirements):
            raise ValueError("at least one actual business result is required")
        return self

    def validate_acceptance(self, contract: BrowserAcceptanceContract | None) -> None:
        if contract is None:
            if any(req.readback_expected is not None for req in self.requirements):
                raise ValueError("explicit readback format requires a reviewed input contract")
            return
        reviewed_ids = {field.id for field in contract.fields}
        if any(
            req.readback_expected is not None and req.id not in reviewed_ids
            for req in self.requirements
        ):
            raise ValueError("unreviewed input cannot override its readback format")
        requirements = {r.id: r for r in self.requirements}
        for field in contract.fields:
            req = requirements.get(field.id)
            if req is None or req.kind != "input" or req.expected != field.value:
                raise ValueError("BDD plan omitted or changed a reviewed input")
            writes = [s for s in self.steps if s.requirement == field.id and s.keyword == "When"]
            reads = [s for s in self.steps if s.requirement == field.id and s.keyword == "Then"]
            if len(writes) != 1 or len(reads) != 1:
                raise ValueError("BDD plan must write and verify each reviewed input")
            write, read = writes[0], reads[0]
            if (
                write.value != field.value
                or not field.matches_target(write.model_dump())
                or not field.matches_target(read.model_dump())
                or read.attribute != field.attribute
                or read.assertion is None
                or read.assertion.kind != "equals"
                or read.assertion.expected != field.readback_value
            ):
                raise ValueError("BDD plan changed a reviewed input target or readback")

    def validate_sources(self, goal: str) -> None:
        if any(r.source not in goal for r in self.requirements):
            raise ValueError("requirement source must quote the frozen user request")


class BDDExecutor:
    max_observations = 6

    def __init__(
        self, plan: BDDPlan, start_url: str, acceptance: BrowserAcceptanceContract | None = None
    ):
        plan.validate_acceptance(acceptance)
        self.plan, self.start_url = plan, start_url
        self.digest = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
        self.position = 0
        self.outputs: dict[str, str] = {}
        self.actions: list[dict[str, Any]] = []
        self.failures: Counter[str] = Counter()
        self.observations = 0
        self.attempts = 0
        self.error = ""

    @property
    def current(self) -> BDDStep | None:
        return self.plan.steps[self.position] if self.position < len(self.plan.steps) else None

    def authorize(self, step_id: str, digest: str) -> BDDStep:
        if self.error:
            raise ValueError("execution stopped: " + self.error)
        current = self.current
        if not current or step_id != current.id or digest != self.digest:
            raise ValueError("decision does not match the current step and frozen plan")
        return current

    def observe(self) -> None:
        if self.error:
            raise ValueError("execution stopped: " + self.error)
        self.observations += 1
        if self.observations > self.max_observations:
            self.error = "no business progress within observation limit"
            raise ValueError(self.error)

    def failed(self, signature: str, error: str) -> None:
        self.failures[signature] += 1
        self.attempts += 1
        if self.failures[signature] >= 3 or self.attempts >= 6:
            self.error = "bounded action failures exhausted: " + error[:300]

    def action(self, selector: str | None = None) -> dict[str, Any]:
        step = self.current
        if step is None:
            raise ValueError("all steps completed")
        self.authorize(step.id, self.digest)
        if step.operation == "navigate":
            return {"type": "navigate", "url": self.start_url}
        if not selector:
            raise ValueError("current observed locator is required")
        action: dict[str, Any] = {
            "type": "read" if step.operation == "assert" else step.operation,
            "selector": selector,
            "target_texts": list(step.target_texts),
        }
        if step.scope_text:
            action["scope_text"] = step.scope_text
        if step.value is not None:
            action["value"] = step.value
        if step.assertion:
            action.update(name=step.requirement or step.id, attribute=step.attribute)
            key = step.assertion.kind
            action[key] = step.assertion.expected if key in {"equals", "contains"} else True
        return action

    def accept(self, action: dict[str, Any], receipt: dict[str, Any]) -> None:
        step = self.current
        if step is None or action != self.action(action.get("selector")):
            raise ValueError("action differs from frozen contract")
        observation = receipt.get("observation") or {}
        if receipt.get("status") != "completed":
            self.error = "unknown action outcome; do not retry"
            raise ValueError(self.error)
        if observation.get("error"):
            raise ValueError(str(observation["error"]))
        if step.operation == "navigate" and observation.get("url", "").rstrip(
            "/"
        ) != self.start_url.rstrip("/"):
            raise ValueError("navigation did not confirm the requested URL")
        if step.assertion:
            fact = observation.get("extracted") or {}
            value = fact.get("value")
            if (
                fact.get("name") != action["name"]
                or not isinstance(value, str)
                or not step.assertion.matches(value)
            ):
                raise ValueError("frozen DOM assertion failed")
            self.outputs[action["name"]] = value
        self.actions.append(
            {
                **action,
                "step": step.id,
                "plan_digest": self.digest,
                "action_id": receipt.get("action_id"),
                "url": observation.get("url"),
            }
        )
        self.position += 1
        self.observations = self.attempts = 0

    def progress(self) -> dict[str, Any]:
        return {
            "plan_digest": self.digest,
            "active_step": self.current.model_dump() if self.current else None,
            "completed": [s.id for s in self.plan.steps[: self.position]],
            "verified_outputs": dict(self.outputs),
            "error": self.error,
        }

    def display_steps(self) -> dict[str, dict[str, str]]:
        return {
            s.id: {
                "id": s.id,
                "keyword": s.keyword,
                "title": s.title,
                "expected_result": s.assertion.model_dump_json() if s.assertion else s.operation,
                **(
                    {
                        "verification_kind": next(
                            r.kind for r in self.plan.requirements if r.id == s.requirement
                        )
                    }
                    if s.keyword == "Then"
                    else {}
                ),
            }
            for s in self.plan.steps
        }
