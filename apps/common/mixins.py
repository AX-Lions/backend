"""뷰에서 반복되는 것들 — 멱등 재생, 낙관적 잠금, 스코프 권한."""
from rest_framework.response import Response

from config.errors import BordoError
from .idempotency import remember, replay_or_none


class IdempotentCreateMixin:
    """POST/PATCH 결과를 Idempotency-Key 로 기억했다가 재생합니다."""

    def idempotency_scope(self, request):
        return f"{request.method} {request.resolver_match.route}"

    def replay(self, request):
        rec = replay_or_none(request, self.idempotency_scope(request))
        if rec:
            return Response(rec.response_snapshot, status=rec.status_code,
                            headers={"Idempotency-Replayed": "true"})
        return None

    def store(self, request, response):
        if 200 <= response.status_code < 300:
            remember(request, self.idempotency_scope(request),
                     response.status_code, response.data)
        return response


class OptimisticLockMixin:
    """If-Match 헤더의 version 과 실제가 다르면 409."""

    def check_version(self, request, instance):
        want = request.headers.get("If-Match")
        if want is None:
            return
        try:
            want = int(want.strip('"'))
        except ValueError:
            raise BordoError("VALIDATION_ERROR", "If-Match 는 정수 version 이어야 합니다.")
        if want != instance.version:
            raise BordoError(
                "REFERENCED_BY_OTHERS",
                "그사이 다른 사람이 수정했습니다. 다시 불러온 뒤 시도하십시오.",
                details={"your_version": want, "current_version": instance.version},
                status=409,
            )
