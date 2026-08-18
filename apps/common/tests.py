"""
표시용 문자열 테스트.

`%-m` 같은 glibc 확장을 쓰면 서버(Linux)에서는 돌고 개발자 PC(Windows)에서만
500 이 납니다. 원인이 포맷 문자열에 있다는 걸 로그만 보고는 알기 어렵습니다.
"""
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from apps.common import display
from apps.common.display import (day_label, full_stamp, short_stamp, time_range,
                                 user_tz)

KST = ZoneInfo("Asia/Seoul")
# 2026-08-13 11:32 KST
AT = datetime(2026, 8, 13, 2, 32, tzinfo=dt_timezone.utc)


class Fake:
    def __init__(self, tz):
        self.timezone = tz


class DisplayTest(SimpleTestCase):

    def test_stamps(self):
        self.assertEqual(full_stamp(AT, KST), "2026.08.13 · 11:32")
        self.assertEqual(short_stamp(AT, KST), "08.13 · 11:32")

    def test_time_range(self):
        end = AT.replace(hour=3, minute=32)
        self.assertEqual(time_range(AT, end, KST), "11:32 - 12:32")

    def test_time_range_without_end(self):
        self.assertEqual(time_range(AT, None, KST), "11:32")

    def test_day_label_drops_the_leading_zero(self):
        """`8/13` 입니다. `%-m` 은 Windows 에서 ValueError 로 죽습니다."""
        self.assertEqual(day_label(AT, KST), "8/13")

    def test_none_is_empty_not_a_crash(self):
        """일정이 비어 있다고 화면 전체가 500 이 되면 안 됩니다."""
        self.assertEqual(full_stamp(None, KST), "")
        self.assertEqual(day_label(None, KST), "")

    def test_broken_timezone_falls_back_to_utc(self):
        """시간대 문자열 하나 때문에 홈이 통째로 죽지 않아야 합니다."""
        self.assertEqual(str(user_tz(Fake("Mars/Olympus"))), "UTC")
        self.assertEqual(str(user_tz(Fake(""))), "UTC")
        self.assertEqual(str(user_tz(Fake("Asia/Seoul"))), "Asia/Seoul")


class MeetingWhenTest(SimpleTestCase):
    """헤더 한 줄 — `8월 18일 14:00 - 15:00`."""

    def test_ko_date_and_range(self):
        tz = ZoneInfo("Asia/Seoul")
        start = datetime(2026, 8, 18, 5, 0, tzinfo=ZoneInfo("UTC"))   # 한국 14:00
        end = datetime(2026, 8, 18, 6, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(display.meeting_when(start, end, tz), "8월 18일 14:00 - 15:00")

    def test_no_padding_on_month_and_day(self):
        tz = ZoneInfo("UTC")
        self.assertEqual(display.date_label(datetime(2026, 8, 3, tzinfo=tz), tz), "8월 3일")

    def test_empty_when_missing(self):
        self.assertEqual(display.meeting_when(None, None, ZoneInfo("UTC")), "")
