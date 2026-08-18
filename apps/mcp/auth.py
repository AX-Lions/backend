"""
`/mcp` 인증.

`Authorization: Bearer brd_...` 하나로 **누구인지**를 정합니다. 팀·프로젝트 권한은
그 사용자로 `project_membership()` 을 그대로 통과시킵니다 — MCP 라고 예외를 두지
않습니다.

인증 실패는 JSON-RPC 본문을 읽기 **전에** 결정되므로 HTTP 401 입니다. 본문이 깨져
있어도 401 이 나가야 합니다.
"""
from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings

from apps.common.throttle import check_rate as throttle
from config.errors import BordoError

from .models import McpToken

RATE_LIMIT_PER_MINUTE = 120


def _bearer(request) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def check_origin(request) -> None:
    """
    `Origin` 이 오면 우리 도메인이어야 합니다 (DNS rebinding 차단).

    개인 AI 클라이언트는 보통 Origin 을 안 보냅니다. 브라우저에서 온 요청만
    걸러내면 되므로 **없으면 통과**, 있으면 CORS 허용 목록과 맞춥니다.
    """
    origin = request.META.get("HTTP_ORIGIN")
    if not origin:
        return
    if getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False):
        return
    allowed = set(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    host = urlsplit(origin).netloc
    if origin in allowed or host == request.get_host():
        return
    raise BordoError("MCP_ORIGIN_FORBIDDEN", details={"origin": origin})


def check_rate(user_id) -> None:
    """
    사용자당 분당 상한.

    개인 AI 가 루프에 빠지면 순식간에 수백 건이 들어옵니다. 상한을 두지 않으면
    한 사람의 잘못된 프롬프트가 팀 전체 응답 시간을 잡아먹습니다.
    """
    throttle(f"mcp:{user_id}", RATE_LIMIT_PER_MINUTE, code="MCP_RATE_LIMITED")


def mcp_token_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        check_origin(request)
        token = McpToken.authenticate(_bearer(request))
        if token is None:
            raise BordoError("AUTH_MCP_TOKEN_INVALID")
        check_rate(token.user_id)
        token.touch()
        request.user = token.user
        request.mcp_token = token
        return view(request, *args, **kwargs)
    return wrapper
