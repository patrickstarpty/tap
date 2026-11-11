"""Bounded, non-secret context for the loopback browser recovery and LCA assistant embed."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GatewayUnavailableContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: Literal[False] = False
    project_id: str = Field(min_length=1, max_length=128)


class GatewayContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: Literal[True] = True
    project_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    identity_mode: Literal["validation"] = "validation"
    gateway_instance_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    context_binding: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
