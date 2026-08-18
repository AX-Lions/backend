"""
화면에 그대로 찍히는 문자열을 서버가 만듭니다.

## 왜 서버가 만드는가

클라이언트가 ISO 문자열을 받아 직접 포맷하면 세 가지가 어긋납니다.

1. **시간대** — 팀원이 서로 다른 지역에 있는 것이 이 서비스의 전제입니다.
   브라우저 시간대로 찍으면 같은 회의를 사람마다 다른 시각으로 보게 되고,
   서머타임 경계에서는 한 시간이 통째로 밀립니다.
2. **표기** — 같은 `displayed_at` 인데 카드마다 형태가 다릅니다.
   최근 회의는 `2026.08.12 · 11:32`, 요약 카드는 `08.12 · 11:32` 입니다.
   규칙을 클라이언트에 흩어 두면 화면마다 조금씩 달라집니다.
3. **로케일** — `Intl` 은 브라우저 설정을 따라가므로 한국어 사용자가 영어
   로케일 브라우저를 쓰면 표기가 갈립니다.

`CLAUDE.md` 의 "집계는 서버가" 와 같은 이유입니다.

## `%-m` 을 쓰지 않습니다

`f"{dt:%-m/%-d}"` 는 glibc 확장이라 **Windows 에서 `ValueError` 로 죽습니다.**
서버(Linux)에서는 돌고 개발자 PC 에서만 500 이 나므로 원인을 찾기 어렵습니다.
패딩 제거는 파이썬 쪽에서 합니다.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = ZoneInfo("UTC")


def user_tz(user) -> ZoneInfo:
    """
    사용자 표시 시간대.

    값이 깨져 있어도 예외를 올리지 않습니다 — 시간대 문자열 하나 때문에 홈 화면
    전체가 500 이 되는 것보다, UTC 로라도 그려 주는 편이 낫습니다.
    """
    try:
        return ZoneInfo(getattr(user, "timezone", None) or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _local(dt, tz):
    return dt.astimezone(tz) if dt is not None else None


def full_stamp(dt, tz) -> str:
    """`2026.08.12 · 11:32` — 최근 회의 카드."""
    d = _local(dt, tz)
    return f"{d:%Y.%m.%d} · {d:%H:%M}" if d else ""


def short_stamp(dt, tz) -> str:
    """
    `08.12 · 11:32` — 요약 카드.

    연도를 빼는 이유는 카드 폭이 좁아서입니다. 같은 이름(`displayed_at`)이지만
    형태가 다르므로 상수 하나로 합치지 마십시오.
    """
    d = _local(dt, tz)
    return f"{d:%m.%d} · {d:%H:%M}" if d else ""


def time_range(start, end, tz) -> str:
    """`09:00 - 10:00` — 오늘 일정."""
    s, e = _local(start, tz), _local(end, tz)
    if not s:
        return ""
    return f"{s:%H:%M} - {e:%H:%M}" if e else f"{s:%H:%M}"


def day_label(dt, tz) -> str:
    """
    `8/13` — 플로우 상단 회의 제목 앞에 붙는 날짜.

    앞의 0 을 떼되 `%-m` 은 쓰지 않습니다(모듈 설명 참고).
    """
    d = _local(dt, tz)
    return f"{d.month}/{d.day}" if d else ""


def date_label(dt, tz) -> str:
    """
    `8월 18일` — 회의 대리 참석 준비 화면 헤더.

    `day_label`(`8/13`)과 나누는 이유는 폭입니다. 좁은 플로우 상단에는 슬래시
    표기가, 넓은 헤더에는 한국어 표기가 들어갑니다. 한 함수로 합치면 둘 중
    하나는 반드시 어색해집니다.
    """
    d = _local(dt, tz)
    return f"{d.month}월 {d.day}일" if d else ""


def meeting_when(start, end, tz) -> str:
    """`8월 18일 14:00 - 15:00` — 날짜와 시간대를 한 줄로."""
    day = date_label(start, tz)
    span = time_range(start, end, tz)
    return f"{day} {span}".strip() if day or span else ""
