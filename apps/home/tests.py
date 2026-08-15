"""
홈 응답 테스트.

화면이 그대로 찍는 문자열을 서버가 만드는지 봅니다. ISO 문자열만 내려주고
클라이언트가 포맷하게 두면 팀원이 서로 다른 지역에 있을 때 같은 회의를 다른
시각으로 보게 되고, 카드마다 표기가 조금씩 갈립니다.
"""
from datetime import datetime, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus, MeetingSummary)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

# 2026-08-12 11:32 KST. UTC 로 적어 두고 환산 결과를 봅니다 —
# 로컬 시간대로 적으면 테스트가 실행 환경을 따라 흔들립니다.
KST_1132 = datetime(2026, 8, 12, 2, 32, tzinfo=dt_timezone.utc)


class HomeDisplayTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="유수인", timezone="Asia/Seoul")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        cls.project = Project.objects.create(team=team, team_name="팀",
                                             name="멋사 중앙해커톤", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)

        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="멋사 중앙해커톤",
            title="글로벌 회의 일정 및 개발 방향 논의", scheduled_at=KST_1132,
            duration_min=60, discord_channel_id="ch-1", created_by=cls.me)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _get(self):
        r = self.client.get("/api/v1/home")
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_recent_meeting_has_a_printable_stamp(self):
        """카드에 `2026.08.12 · 11:32` 그대로 찍힙니다."""
        card = self._get()["recent_meetings"][0]
        self.assertEqual(card["displayed_at"], "2026.08.12 · 11:32")

    def test_stamp_follows_the_users_timezone(self):
        """
        브라우저 시간대로 찍으면 시차가 있는 팀원끼리 같은 회의를 다르게 봅니다.

        서머타임 경계에서는 한 시간이 통째로 밀립니다.
        """
        self.me.timezone = "UTC"
        self.me.save(update_fields=["timezone"])
        card = self._get()["recent_meetings"][0]
        self.assertEqual(card["displayed_at"], "2026.08.12 · 02:32")

    def test_today_schedule_has_a_time_range_and_location(self):
        Meeting.objects.filter(pk=self.meeting.pk).update(scheduled_at=timezone.now())
        row = self._get()["today_schedule"][0]
        self.assertRegex(row["time_range"], r"^\d{2}:\d{2} - \d{2}:\d{2}$")
        self.assertEqual(row["location"], "Discord")

    def test_summary_card_uses_the_short_stamp_and_zero_summary(self):
        """
        요약 카드는 폭이 좁아 연도를 뺍니다. 이름은 같아도 형태가 다릅니다.

        `Zero 요약` 은 화면 라벨이라 필드 이름도 그쪽에 맞춥니다.
        """
        Meeting.objects.filter(pk=self.meeting.pk).update(
            status=MeetingStatus.ENDED, ended_at=KST_1132)
        MeetingSummary.objects.create(meeting=self.meeting,
                                      one_line="디자인을 먼저 진행하기로 했어요.")
        MeetingParticipant.objects.create(meeting=self.meeting, user=self.me,
                                          user_name="유수인",
                                          attendance=Attendance.ABSENT)
        card = self._get()["recent_meeting_summary"]
        self.assertEqual(card["displayed_at"], "08.12 · 11:32")
        self.assertEqual(card["zero_summary"], "디자인을 먼저 진행하기로 했어요.")

    def test_missed_badge_text_comes_from_the_server(self):
        """
        불리언만 주면 클라이언트마다 다른 낱말을 쓰고, 라벨을 바꿀 때 배포가
        두 번 필요합니다.
        """
        Meeting.objects.filter(pk=self.meeting.pk).update(
            status=MeetingStatus.ENDED, ended_at=KST_1132)
        MeetingParticipant.objects.create(meeting=self.meeting, user=self.me,
                                          user_name="유수인",
                                          attendance=Attendance.DELEGATED)
        self.assertEqual(self._get()["recent_meeting_summary"]["status"], "불참한 회의")
