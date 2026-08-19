"""
`/internal/v1/delegate/on`·`off` 가 **알릴 스레드 목록을 함께 돌려준다.**

봇이 「어느 스레드에 알릴지」 를 자기 메모리에서 찾으면, 재시작한 뒤에는
DB 가 정확히 갱신됐는데도 회의 스레드에 아무 알림이 안 갑니다. 대리 참석은
켜졌는데 회의에 있는 사람들은 모릅니다.

`AX-Lions/discord#19` · 이슈 #97.
"""
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.discord.models import GuildLink
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

TOKEN = "test-service-token"

# 서비스 토큰은 설정에서 읽습니다. 덮어쓰지 않으면 실제 값과 달라 401 입니다.
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS)
class DelegateThreadIds(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="강다은", discord_user_id="dc-me")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="멋사 중앙해커톤", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        GuildLink.objects.create(guild_id="g-1", team=cls.team, linked_by=cls.me)

    def setUp(self):
        self.api = APIClient()

    def meeting(self, *, channel="", status=MeetingStatus.SCHEDULED):
        m = Meeting.objects.create(
            project=self.project, project_name=self.project.name,
            title="정기 팀 회의", status=status,
            scheduled_at=timezone.now() + timezone.timedelta(hours=1),
            duration_min=60, created_by=self.me, discord_channel_id=channel)
        MeetingParticipant.objects.create(meeting=m, user=self.me,
                                          user_name=self.me.name)
        return m

    def post(self, on=True):
        return self.api.post(
            f"/internal/v1/delegate/{'on' if on else 'off'}",
            {"discord_user_id": "dc-me"}, format="json",
            HTTP_X_SERVICE_TOKEN=TOKEN)

    # ── 있어야 하는 것 ───────────────────────────────────────────

    def test_스레드가_붙은_회의를_돌려준다(self):
        self.meeting(channel="thread-1")
        r = self.post(on=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["thread_ids"], ["thread-1"])

    def test_끌_때도_돌려준다(self):
        """
        켤 때만 주면 봇이 **끈 것을 회의에 못 알린다.** 회의에 있는 사람들은
        대리인이 아직 듣고 있는 줄 안다.
        """
        self.meeting(channel="thread-1")
        self.post(on=True)
        r = self.post(on=False)
        self.assertEqual(r.data["thread_ids"], ["thread-1"])

    def test_여러_회의면_모두_돌려준다(self):
        self.meeting(channel="thread-1")
        self.meeting(channel="thread-2", status=MeetingStatus.ACTIVE)
        r = self.post(on=True)
        self.assertEqual(sorted(r.data["thread_ids"]), ["thread-1", "thread-2"])

    # ── 빠져야 하는 것 ───────────────────────────────────────────

    def test_스레드가_없는_회의는_뺀다(self):
        """웹에서 만들고 아직 `/meeting-start` 를 안 친 회의. 알릴 곳이 없다."""
        self.meeting(channel="")
        r = self.post(on=True)
        self.assertEqual(r.data["thread_ids"], [])
        # 상태는 그래도 갱신된다 — 알릴 곳이 없는 것과 안 켜진 것은 다르다.
        self.assertEqual(r.data["meetings_updated"], 1)

    def test_끝난_회의는_뺀다(self):
        """
        끝난 회의 스레드에 「대리 참석으로 바꿨습니다」 가 뜨면, 어제 끝난
        회의가 지금 무슨 일이 생긴 것처럼 보인다.
        """
        self.meeting(channel="old-thread", status=MeetingStatus.ENDED)
        r = self.post(on=True)
        self.assertEqual(r.data["thread_ids"], [])

    def test_남의_회의는_안_섞인다(self):
        other = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                         name="최비성")
        TeamMember.objects.create(team=self.team, user=other, team_role="MEMBER")
        ProjectMember.objects.create(project=self.project, user=other)
        m = self.meeting(channel="mine")
        MeetingParticipant.objects.filter(meeting=m, user=self.me).delete()
        MeetingParticipant.objects.create(meeting=m, user=other, user_name=other.name)

        r = self.post(on=True)
        self.assertEqual(r.data["thread_ids"], [])

    # ── 기존 응답을 안 깬다 ──────────────────────────────────────

    def test_기존_필드가_그대로_있다(self):
        """더한 것이라 옛 응답을 읽는 봇도 그대로 돈다."""
        self.meeting(channel="thread-1")
        r = self.post(on=True)
        self.assertIn("delegated", r.data)
        self.assertIn("meetings_updated", r.data)
        self.assertTrue(r.data["delegated"])

    def test_상태도_함께_바뀐다(self):
        m = self.meeting(channel="thread-1")
        self.post(on=True)
        row = MeetingParticipant.objects.get(meeting=m, user=self.me)
        self.assertTrue(row.delegated)
        self.assertEqual(row.attendance, Attendance.DELEGATED)
