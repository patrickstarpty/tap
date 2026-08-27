"""Liveness and dependency-readiness contracts."""

from __future__ import annotations

from fastapi import APIRouter

from tap.contracts.http import (
    HealthComponent,
    HealthComponentName,
    HealthComponentState,
    HealthRemediationCode,
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
            HealthComponent(name=name, state=HealthComponentState.FAILED, remediation_code=code)
            for name, code in (
                (HealthComponentName.MYSQL, HealthRemediationCode.START_MYSQL),
                (HealthComponentName.REDIS, HealthRemediationCode.START_REDIS),
                (HealthComponentName.BLOB, HealthRemediationCode.START_BLOB),
                (HealthComponentName.MILVUS, HealthRemediationCode.START_MILVUS),
                (HealthComponentName.MODELS, HealthRemediationCode.CONFIGURE_MODELS),
            )
        ],
    )
