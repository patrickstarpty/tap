"""Liveness and dependency-readiness contracts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from tap.contracts.http import LiveHealth, ReadyHealth
from tap.interfaces.http.dependencies import ReadinessHttpService, readiness_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", operation_id="health_get_live", response_model=LiveHealth)
async def get_live_health() -> LiveHealth:
    return LiveHealth(status="ok")


@router.get("/ready", operation_id="health_get_ready", response_model=ReadyHealth)
async def get_ready_health(
    service: Annotated[ReadinessHttpService, Depends(readiness_service)],
) -> ReadyHealth:
    return await service.check()
