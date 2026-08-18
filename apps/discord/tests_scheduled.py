"""
웹에서 만든 회의에 Discord 스레드를 붙이는 흐름 (이슈 #89).

막아야 하는 것 — `meeting_id` 를 보냈는데 **조용히 즉석 생성으로 빠지는 것.**
그러면 봇의 버그와 정상 동작이 구별되지 않고, 아무 프로젝트에 제목
`Discord 회의` 짜리 빈 회의가 쌓입니다.
"""
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.discord.models import GuildLink
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

TOKEN = "test-only-token"
GUILD = "guild-1"
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS)
class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민", discord_user_id="dc-me")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="유수인", discord_user_id="dc-mate")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u,
                                      team_role="OWNER" if u == cls.me else "MEMBER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="멋사 중앙해커톤", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def get(self, path, params=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token else {}
        return self.client.get(f"/internal/v1{path}", params or {}, **headers)

    def post(self, path, payload=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token else {}
        return self.client.post(f"/internal/v1{path}", payload or {},
                                content_type="application/json", **headers)

    def link(self):
        GuildLink.objects.get_or_create(guild_id=GUILD,
                                        defaults={"team": self.team})

    def meeting(self, title="글로벌 회의 일정 논의", status=MeetingStatus.SCHEDULED,
                hours=1, thread="", project=None, members=True):
        m = Meeting.objects.create(
            project=project or self.project,
            project_name=(project or self.project).name, title=title, status=status,
            scheduled_at=timezone.now() + timezone.timedelta(hours=hours),
            discord_channel_id=thread, created_by=self.me)
        if members:
            for u in (self.me, self.mate):
                MeetingParticipant.objects.create(meeting=m, user=u, user_name=u.name)
        return m


class ScheduledListTest(Base):

    def setUp(self):
        self.link()

    def test_lists_meetings_without_a_thread(self):
        m = self.meeting()
        res = self.get("/meetings/scheduled", {"guild_id": GUILD})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["count"], 1)
        row = body["results"][0]
        self.assertEqual(row["meeting_id"], str(m.id))
        self.assertEqual(row["project_name"], "멋사 중앙해커톤")
        self.assertEqual(row["title"], "글로벌 회의 일정 논의")
        names = {p["user_name"] for p in row["participants"]}
        self.assertEqual(names, {"서재민", "유수인"})
        self.assertEqual(row["participants"][0]["discord_user_id"][:3], "dc-")

    def test_confirmed_meetings_are_included(self):
        """캘린더에서 일정을 확정하면 CONFIRMED 가 됩니다 — 가장 흔한 경로입니다."""
        self.meeting(status=MeetingStatus.CONFIRMED)
        self.assertEqual(self.get("/meetings/scheduled",
                                  {"guild_id": GUILD}).json()["count"], 1)

    def test_already_attached_is_excluded(self):
        self.meeting(thread="thread-1")
        self.assertEqual(self.get("/meetings/scheduled",
                                  {"guild_id": GUILD}).json()["count"], 0)

    def test_started_or_ended_are_excluded(self):
        for st in (MeetingStatus.ACTIVE, MeetingStatus.ENDED):
            self.meeting(status=st)
        self.assertEqual(self.get("/meetings/scheduled",
                                  {"guild_id": GUILD}).json()["count"], 0)

    def test_far_away_meetings_are_excluded(self):
        """지난달 회의까지 뜨면 자동완성에서 고를 수가 없습니다."""
        self.meeting(hours=48)
        self.meeting(hours=-72)
        self.assertEqual(self.get("/meetings/scheduled",
                                  {"guild_id": GUILD}).json()["count"], 0)

    def test_sorted_by_distance_from_now(self):
        """오름차순으로 두면 한참 전에 잡아 둔 회의가 목록 맨 앞을 차지합니다."""
        self.meeting(title="5시간 뒤", hours=5)
        self.meeting(title="30분 전", hours=-0.5)
        self.meeting(title="1시간 뒤", hours=1)
        titles = [r["title"] for r in
                  self.get("/meetings/scheduled", {"guild_id": GUILD}).json()["results"]]
        self.assertEqual(titles, ["30분 전", "1시간 뒤", "5시간 뒤"])

    def test_other_teams_meeting_is_not_listed(self):
        other_team = Team.objects.create(name="다른 팀", created_by=self.mate)
        other = Project.objects.create(team=other_team, team_name="다른 팀",
                                       name="남의 것", created_by=self.mate)
        self.meeting(project=other, members=False)
        self.assertEqual(self.get("/meetings/scheduled",
                                  {"guild_id": GUILD}).json()["count"], 0)

    def test_unlinked_guild(self):
        res = self.get("/meetings/scheduled", {"guild_id": "nope"})
        self.assertEqual(res.json()["error"]["code"], "TEAM_NOT_FOUND")

    def test_needs_service_token(self):
        self.assertEqual(self.get("/meetings/scheduled", {"guild_id": GUILD},
                                  token=None).status_code, 401)


class AttachThreadTest(Base):

    def setUp(self):
        self.link()

    def test_attaches_instead_of_creating(self):
        m = self.meeting()
        before = Meeting.objects.count()
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(m.id)})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Meeting.objects.count(), before, "새 회의를 만들면 안 됩니다")

        m.refresh_from_db()
        self.assertEqual(m.discord_channel_id, "th-1")
        self.assertEqual(m.status, MeetingStatus.ACTIVE)
        self.assertIsNotNone(m.started_at)

        body = res.json()
        self.assertEqual(body["title"], "글로벌 회의 일정 논의")
        self.assertEqual(len(body["participants"]), 2)

    def test_participants_are_not_replaced(self):
        """웹에서 만들 때 프로젝트 참여자 전원이 이미 등록돼 있습니다."""
        m = self.meeting()
        self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                      "meeting_id": str(m.id),
                                      "participants": [{"discord_user_id": "dc-me"}]})
        self.assertEqual(MeetingParticipant.objects.filter(meeting=m).count(), 2)

    def test_delegated_flag_survives(self):
        m = self.meeting()
        MeetingParticipant.objects.filter(meeting=m, user=self.mate).update(
            delegated=True, attendance=Attendance.DELEGATED)
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(m.id)})
        rows = {p["user_name"]: p for p in res.json()["participants"]}
        self.assertTrue(rows["유수인"]["delegated"])
        self.assertFalse(rows["서재민"]["delegated"])

    def test_confirmed_meeting_can_start(self):
        m = self.meeting(status=MeetingStatus.CONFIRMED)
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(m.id)})
        self.assertEqual(res.status_code, 200, res.content)

    def test_already_started_meeting(self):
        m = self.meeting(status=MeetingStatus.ACTIVE)
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(m.id)})
        self.assertEqual(res.json()["error"]["code"], "MEETING_ALREADY_STARTED")

    def test_unknown_meeting_id_never_falls_back(self):
        """조용히 즉석 생성으로 빠지면 봇의 버그와 정상 동작이 구별되지 않습니다."""
        import uuid
        before = Meeting.objects.count()
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(uuid.uuid4())})
        self.assertEqual(res.json()["error"]["code"], "MEETING_NOT_FOUND")
        self.assertEqual(Meeting.objects.count(), before)

    def test_other_teams_meeting_is_not_found(self):
        """없는 것과 남의 것을 같은 오류로 답합니다 — 다르면 존재 여부가 새어 나갑니다."""
        other_team = Team.objects.create(name="다른 팀", created_by=self.mate)
        other = Project.objects.create(team=other_team, team_name="다른 팀",
                                       name="남의 것", created_by=self.mate)
        m = self.meeting(project=other, members=False)
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                            "meeting_id": str(m.id)})
        self.assertEqual(res.json()["error"]["code"], "MEETING_NOT_FOUND")

    def test_retry_on_same_thread_is_safe(self):
        m = self.meeting()
        self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                      "meeting_id": str(m.id)})
        again = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-1",
                                              "meeting_id": str(m.id)})
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(len(again.json()["participants"]), 2)

    def test_legacy_path_still_creates(self):
        """봇이 새 방식으로 배포되기 전에 지우면 그 사이 회의 시작이 통째로 막힙니다."""
        res = self.post("/meetings/start", {"guild_id": GUILD, "thread_id": "th-9",
                                            "agenda": "즉석 회의"})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["title"], "즉석 회의")
