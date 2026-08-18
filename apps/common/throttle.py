"""
캐시 기반 호출 상한.

`(키, 분당 상한)` 하나로 씁니다. DRF 의 throttle 은 인증 클래스에 매여 있어
서비스 토큰·MCP 토큰처럼 DRF 인증을 안 타는 진입점에서는 쓸 수 없습니다.
"""
from django.core.cache import cache

from config.errors import BordoError


def check_rate(key: str, limit_per_minute: int, *, code: str = "MODEL_RATE_LIMIT") -> None:
    """상한을 넘기면 `code` 로 `BordoError`. 만료 직후 경합은 새 창으로 봅니다."""
    cache_key = f"rate:{key}"
    if cache.add(cache_key, 1, timeout=60):
        return
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.add(cache_key, 1, timeout=60)
        return
    if count > limit_per_minute:
        raise BordoError(code, details={"limit_per_minute": limit_per_minute})
