"""Liveness and dependency-readiness contracts."""

from __future__ import annotations

from fastapi import APIRouter

from tap.contracts.http import (
    HealthComponent,
    HealthComponentName,
    HealthComponentState,
    LiveHealth,
    ReadyHealth,
)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", operation_id="health_get_live", response_model=LiveHealth)
async def get_live_health() -> LiveHealth:
    return LiveHealth(status="ok")


@router.get("/ready", operation_id="health_get_ready", response_model=ReadyHealth)
async def get_ready_health() -> ReadyHealth:
    return ReadyHealth(
        status="unready",
        components=[
            HealthComponent(
                name=name,
                state=HealthComponentState.FAILED,
                remediation_code="runtime-unconfigured",
            )
            for name in (
                HealthComponentName.MYSQL,
                HealthComponentName.REDIS,
                HealthComponentName.BLOB,
                HealthComponentName.MILVUS,
                HealthComponentName.MODELS,
            )
        ],
    )
