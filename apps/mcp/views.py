"""
`POST /mcp` 하나와 토큰 발급·폐기.

`/mcp` 는 DRF 인증(JWT)을 타지 않습니다 — 개인 AI 클라이언트는 `brd_` 토큰으로
옵니다. GET · DELETE 는 405 입니다 (SSE 스트림과 세션 종료는 만들지 않습니다).
"""
from __future__ import annotations

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.errors import BordoError
from config.exceptions import error_body

from . import rpc
from .auth import mcp_token_required
from .models import McpToken


@csrf_exempt
@require_POST
def mcp(request):
    try:
        return _mcp(request)
    except BordoError as e:
        # 인증·Origin·상한 — JSON-RPC 본문을 읽기 전에 결정되는 것들이라 HTTP 상태로 냅니다.
        body, _ = error_body(request, e.code, e.message, e.details)
        return JsonResponse(body, status=e.status)


@mcp_token_required
def _mcp(request):
    out = rpc.handle(request)
    if out.body is None:
        return HttpResponse(status=out.status)
    return JsonResponse(out.body, status=out.status, encoder=DjangoJSONEncoder,
                        json_dumps_params={"ensure_ascii": False})


# ─────────────────────────────────────────── 토큰 발급 · 폐기
def _public_url(request, path: str) -> str:
    base = settings.BORDO.get("PUBLIC_URL") or request.build_absolute_uri("/").rstrip("/")
    return f"{base.rstrip('/')}{path}"


@api_view(["POST", "DELETE"])
def me_mcp_token(request):
    """
    `POST` 발급(재발급) · `DELETE` 폐기.

    원문은 발급 응답에 딱 한 번 실립니다. `setup_command` 를 서버가 만들어 주는
    이유 — 사용자가 URL 과 헤더 형식을 직접 조립하면 반드시 틀립니다.
    """
    if request.method == "DELETE":
        McpToken.revoke_all(request.user)
        return Response(status=204)

    row, raw = McpToken.issue(request.user)
    url = _public_url(request, "/mcp")
    return Response({
        "token": raw,
        "issued_at": row.created_at,
        "setup_command": (f'claude mcp add --transport http bordo {url} '
                          f'--header "Authorization: Bearer {raw}"'),
    }, status=201)
