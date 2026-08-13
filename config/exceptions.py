"""
모든 4xx·5xx 를 하나의 형식으로 내보냅니다.

    {"success": false, "request_id": "...",
     "error": {"code": ..., "message": ..., "retryable": ..., "details": ...}}
"""
import logging

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404
from rest_framework import status as http
from rest_framework.exceptions import (
    APIException, AuthenticationFailed, MethodNotAllowed, NotAuthenticated,
    NotFound, PermissionDenied as DRFPermissionDenied, Throttled, ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler

from .errors import ERROR_CODES, BordoError

logger = logging.getLogger(__name__)


def error_body(request, code, message=None, details=None):
    default_status, default_message, retryable = ERROR_CODES[code]
    err = {"code": code, "message": message or default_message, "retryable": retryable}
    if details is not None:
        err["details"] = details
    return {
        "success": False,
        "request_id": getattr(request, "request_id", None) or "-",
        "error": err,
    }, default_status


def _map_drf(exc):
    """DRF 기본 예외를 우리 코드로 옮깁니다."""
    if isinstance(exc, ValidationError):
        return "VALIDATION_ERROR", exc.detail
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        detail = str(getattr(exc, "detail", "")).lower()
        if "expire" in detail or "만료" in detail:
            return "AUTH_TOKEN_EXPIRED", None
        return "AUTH_INVALID_TOKEN", None
    if isinstance(exc, (DRFPermissionDenied, PermissionDenied)):
        return "TEAM_ACCESS_DENIED", None
    if isinstance(exc, (NotFound, Http404, ObjectDoesNotExist)):
        return "STATE_NOT_FOUND", None
    if isinstance(exc, Throttled):
        return "MODEL_RATE_LIMIT", {"retry_after_seconds": exc.wait}
    return None, None


def bordo_exception_handler(exc, context):
    request = context.get("request")

    if isinstance(exc, BordoError):
        body, _ = error_body(request, exc.code, exc.message, exc.details)
        return Response(body, status=exc.status)

    code, details = _map_drf(exc)
    if code:
        body, default_status = error_body(request, code, details=details)
        st = getattr(exc, "status_code", None) or default_status
        # DRF 가 준 상태보다 카탈로그 값이 더 정확한 경우가 있어 카탈로그를 우선합니다.
        if code in ("VALIDATION_ERROR",):
            st = http.HTTP_400_BAD_REQUEST
        return Response(body, status=st)

    if isinstance(exc, MethodNotAllowed):
        body, _ = error_body(request, "VALIDATION_ERROR", str(exc.detail))
        return Response(body, status=http.HTTP_405_METHOD_NOT_ALLOWED)

    response = drf_handler(exc, context)
    if response is None:
        logger.exception("처리되지 않은 예외", exc_info=exc)
        if isinstance(exc, APIException):
            body, _ = error_body(request, "EVENT_BROKER_FAILED", str(exc))
            return Response(body, status=http.HTTP_500_INTERNAL_SERVER_ERROR)
        return None  # DEBUG 에서는 Django 기본 500 페이지로 넘겨 스택을 봅니다.

    body, _ = error_body(request, "VALIDATION_ERROR", str(response.data))
    return Response(body, status=response.status_code)
