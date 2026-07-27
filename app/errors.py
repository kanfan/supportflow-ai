from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


SENSITIVE_FIELD_MARKERS = ("password", "secret", "token")


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    safe_errors: list[dict[str, Any]] = []
    for error in exc.errors():
        safe_error = dict(error)
        location = safe_error.get("loc", ())
        if any(
            marker in str(part).lower()
            for part in location
            for marker in SENSITIVE_FIELD_MARKERS
        ):
            safe_error.pop("input", None)
        safe_errors.append(safe_error)
    return safe_errors


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details)
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload.model_dump()),
        headers=headers,
    )


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc

    error_codes = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
    }
    detail = exc.detail
    if (
        isinstance(detail, dict)
        and isinstance(detail.get("code"), str)
        and isinstance(detail.get("message"), str)
    ):
        detail_payload = cast(dict[str, Any], detail)
        code = str(detail_payload["code"])
        message = str(detail_payload["message"])
        details = detail_payload.get("details")
    else:
        code = error_codes.get(exc.status_code, "http_error")
        message = detail if isinstance(detail, str) else "Request failed"
        details = None if isinstance(detail, str) else detail
    return _error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        details=details,
        headers=exc.headers,
    )


async def validation_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc

    return _error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=_safe_validation_errors(exc),
    )


def install_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
