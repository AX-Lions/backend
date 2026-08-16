"""
DB 헬퍼.

## 왜 `get_or_create()` 를 그냥 쓰지 않는가

대부분은 그냥 쓰면 됩니다. Django 의 `get_or_create()` 는 이미
**세이브포인트 안에서 만들고 `IntegrityError` 가 나면 다시 읽는** 동작이라,
유니크 제약과 짝을 이루면 경합이 닫힙니다. 손으로 짤 이유가 없습니다.

**소프트 삭제 모델만 예외입니다.** 기본 매니저(`objects`)가 살아 있는 행만
보여주기 때문에, 지워진 행이 유니크 제약을 그대로 차지하고 있으면 이렇게 됩니다.

    objects.get()      → 없음 (지워진 행이라 안 보임)
    objects.create()   → IntegrityError (제약은 살아 있음)
    objects.get()      → 또 없음 → 예외가 그대로 올라감

`chat.ChatRoom` 이 이 경우입니다. `DIRECT` · `PEER_AGENT` 방은 한쪽이 지워도
상대 기록이 남아야 해서 "내 목록에서만 숨김" 으로 처리하는데, 그 숨은 방이
`(type, dedupe_key)` 유니크를 잡고 있습니다.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction


def ensure_row(model, *, defaults: dict | None = None, **lookup):
    """
    소프트 삭제된 행까지 포함해 찾고, 없으면 만듭니다.

    `all_objects` 로 찾고 `objects` 로 만듭니다 — 지워진 행이 있으면 그것을
    돌려주는 것이 맞습니다. 새로 만들면 유니크 제약에 걸리고, 억지로 우회하면
    같은 대화가 방 두 개로 갈라집니다.

    경합은 `IntegrityError` 재조회로 닫습니다. 세이브포인트가 있어야 바깥
    트랜잭션이 안 깨집니다 — 이 함수는 트랜잭션 안에서도 불립니다.

    `(행, 새로 만들었는지)` 를 돌려줍니다.
    """
    manager = getattr(model, "all_objects", model.objects)

    row = manager.filter(**lookup).first()
    if row is not None:
        return row, False

    try:
        with transaction.atomic():
            return model.objects.create(**{**lookup, **(defaults or {})}), True
    except IntegrityError:
        # 그 사이 다른 쪽이 만들었습니다. 여기서 못 찾으면 제약 위반이 정말로
        # 다른 이유라는 뜻이라, 삼키지 않고 올립니다.
        row = manager.filter(**lookup).first()
        if row is None:
            raise
        return row, False
