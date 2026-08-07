import logging
from typing import Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.infrastructure.readiness import ReadinessProbe


router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]


@router.get("/live", response_model=HealthResponse)
async def live_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def ready_health(request: Request) -> ReadinessResponse | JSONResponse:
    probe: ReadinessProbe = request.app.state.readiness_probe
    result = probe.check()
    if result.is_ready:
        return ReadinessResponse(status="ok")

    request_id = str(request.state.request_id)
    for dependency in result.unavailable_dependencies:
        logger.warning(
            "readiness_dependency_unavailable request_id=%s dependency=%s "
            "error_category=unavailable",
            request_id,
            dependency,
            extra={
                "dependency": dependency,
                "error_category": "unavailable",
                "request_id": request_id,
            },
        )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "unavailable"},
    )
