"""
서비스 토큰 인증.

`/internal/v1` 은 사람이 아니라 **봇이** 부릅니다. JWT 가 아니라 공유 비밀
(`X-Service-Token`)로 확인합니다.

## 왜 별도 인증인가

봇은 특정 사용자로 로그인하지 않습니다. 여러 사람의 발언을 대신 넘기는 주체라,
어떤 사용자의 JWT 를 쓰더라도 그 사람 권한으로 남의 데이터를 건드리게 됩니다.

## 비교를 상수 시간으로 합니다

`==` 로 비교하면 앞자리부터 틀리는 위치에 따라 응답 시간이 달라져, 토큰을 한 글자씩
알아낼 수 있습니다. 실제로 악용하기는 어렵지만 막는 비용이 한 줄입니다.
"""
from __future__ import annotations

import hmac
from functools import wraps

from django.conf import settings

from config.errors import BordoError

HEADER = "HTTP_X_SERVICE_TOKEN"


def service_token_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        expected = settings.BORDO.get("SERVICE_TOKEN") or ""
        given = request.META.get(HEADER, "")
        # 바이트로 비교합니다. compare_digest 는 비ASCII 문자열을 받으면 TypeError 를
        # 냅니다 — 잘못된 토큰이 한글이면 401 대신 500 이 나갑니다.
        if not expected or not hmac.compare_digest(
                str(given).encode("utf-8"), str(expected).encode("utf-8")):
            raise BordoError("AUTH_SERVICE_TOKEN_INVALID",
                             "서비스 토큰이 올바르지 않습니다.")
        return view(request, *args, **kwargs)
    return wrapper
