"""
요청 값 파싱.

`Model.objects.create(start_at="2026-09-07T10:00:00+09:00")` 는 DB 에는 잘 들어가지만
**메모리의 인스턴스는 문자열 그대로**입니다. 그 인스턴스를 바로 직렬화하면
`'str' object has no attribute 'astimezone'` 으로 터집니다. 저장 후 `refresh_from_db()`
로 덮는 방법도 있지만, 그러면 잘못된 날짜 문자열이 DB 까지 갔다가 500 으로 돌아옵니다.

들어오는 자리에서 파싱해 **400 으로 돌려주는 편**이 사용자에게도 낫습니다.
"""
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from config.errors import BordoError


def parse_dt(value, field, *, required=False):
    """
    RFC3339 문자열 → aware datetime.

    오프셋이 없으면 서버 기준 시간대로 붙입니다 — naive 로 두면 저장 때
    Django 가 경고를 뿌리고 비교 연산이 조용히 어긋납니다.
    """
    if value in (None, ""):
        if required:
            raise BordoError("VALIDATION_ERROR", f"{field} 은(는) 필수입니다.")
        return None
    if hasattr(value, "tzinfo"):
        return value if timezone.is_aware(value) else timezone.make_aware(value)

    parsed = parse_datetime(str(value))
    if parsed is None:
        raise BordoError("VALIDATION_ERROR",
                         f"{field} 은(는) RFC3339 형식이어야 합니다. "
                         f"예: 2026-09-07T10:00:00+09:00",
                         details={field: value})
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def apply_dt(data, obj, fields):
    """`data` 에 있는 날짜 필드만 파싱해 `obj` 에 세팅합니다."""
    for f in fields:
        if f in data:
            setattr(obj, f, parse_dt(data[f], f))
    return obj
