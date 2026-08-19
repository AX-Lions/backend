"""
봇이 읽는 회의 참석·대리 상태.

봇은 대리 대상자를 모듈 전역 set 에 들고 있어 재시작하면 통째로 잃습니다.
여기서 되물을 수 있어야 인메모리 상태가 필요 없어집니다.
"""
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.meetings.models import (Attendance, DebatePoint, DebateStance, Meeting,
                                  MeetingParticipant, MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

TOKEN = "test-only-token"
GUILD = "guild-1"
THREAD = "thread-9"
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS)
class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        from apps.discord.models import GuildLink as Link

        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민", discord_user_id="dc-me")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="임수연", discord_user_id="dc-mate")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u, team_role="MEMBER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="해커톤", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)
        Link.objects.create(guild_id=GUILD, team=cls.team)

        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="개발 방향 논의",
            status=MeetingStatus.SCHEDULED, scheduled_at=timezone.now(),
            discord_channel_id=THREAD, created_by=cls.me)
        cls.p_me = MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.me, user_name=cls.me.name,
            delegated=True, attendance=Attendance.DELEGATED)
        cls.p_mate = MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.mate, user_name=cls.mate.name)

    def get(self, path, params=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token else {}
        return self.client.get(f"/internal/v1{path}", params or {}, **headers)

    def post(self, path, payload=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token else {}
        return self.client.post(f"/internal/v1{path}", payload or {},
                                content_type="application/json", **headers)


class ParticipantsTest(Base):

    def test_bot_can_ask_who_is_delegating(self):
        res = self.get("/meetings/participants", {"thread_id": THREAD})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["meeting_id"], str(self.meeting.id))
        self.assertEqual(body["count"], 2)

        rows = {r["name"]: r for r in body["results"]}
        self.assertTrue(rows["서재민"]["delegated"])
        self.assertTrue(rows["서재민"]["will_speak"])
        self.assertEqual(rows["서재민"]["discord_user_id"], "dc-me")
        self.assertEqual(rows["서재민"]["agent_name"], "서재민의 Bordo")
        self.assertFalse(rows["임수연"]["delegated"])
        self.assertFalse(rows["임수연"]["will_speak"])

    def test_asked_marks_who_still_needs_the_popup(self):
        rows = {r["name"]: r for r in
                self.get("/meetings/participants", {"thread_id": THREAD}).json()["results"]}
        self.assertTrue(rows["서재민"]["asked"])       # 이미 불참을 정함
        self.assertFalse(rows["임수연"]["asked"])      # 아직 PENDING — 물어봐야 함

    def test_will_speak_matches_targeting(self):
        """봇이 자기 규칙으로 판단하면 대신 참석한다고 알렸는데 대리인이 안 깨어납니다."""
        from apps.agent.services import targeting
        from apps.meetings.models import Utterance

        u = Utterance.objects.create(meeting=self.meeting, participant=self.mate,
                                     participant_name="임수연", body="재민님 이거 되나요?")
        server_side = {str(p.user_id) for p in targeting.candidates(u)}
        api_side = {r["user_id"] for r in
                    self.get("/meetings/participants", {"thread_id": THREAD}).json()["results"]
                    if r["will_speak"] and r["name"] != "임수연"}
        self.assertEqual(server_side, api_side)

    def test_prepared_counts(self):
        point = DebatePoint.objects.create(meeting=self.meeting, source_key="k1",
                                           order=1, title="쟁점?")
        DebateStance.objects.create(point=point, user=self.me, body="내 입장")
        rows = {r["name"]: r for r in
                self.get("/meetings/participants", {"thread_id": THREAD}).json()["results"]}
        self.assertEqual(rows["서재민"]["prepared"], {"points": 1, "stances": 1})
        self.assertEqual(rows["임수연"]["prepared"], {"points": 1, "stances": 0})

    def test_unknown_thread(self):
        res = self.get("/meetings/participants", {"thread_id": "nope"})
        self.assertEqual(res.json()["error"]["code"], "MEETING_NOT_FOUND")

    def test_empty_thread_is_400_not_random_meeting(self):
        """빈 값으로 조회하면 웹에서 만든 회의(discord_channel_id='')가 잡힙니다."""
        res = self.get("/meetings/participants", {"thread_id": ""})
        self.assertEqual(res.status_code, 400)

    def test_needs_service_token(self):
        self.assertEqual(
            self.get("/meetings/participants", {"thread_id": THREAD}, token=None).status_code,
            401)


class AbsenceTest(Base):

    def test_popup_absence_touches_only_this_meeting(self):
        """delegate/on 은 그 사람의 회의를 전부 뒤집습니다 — 팝업 한 번에 오늘이 다 넘어갑니다."""
        other = Meeting.objects.create(
            project=self.project, project_name=self.project.name, title="다른 회의",
            status=MeetingStatus.SCHEDULED, scheduled_at=timezone.now(),
            created_by=self.me)
        other_p = MeetingParticipant.objects.create(
            meeting=other, user=self.mate, user_name=self.mate.name)

        res = self.post("/meetings/absence",
                        {"thread_id": THREAD, "discord_user_id": "dc-mate"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()["delegated"])

        self.p_mate.refresh_from_db(); other_p.refresh_from_db()
        self.assertEqual(self.p_mate.attendance, Attendance.DELEGATED)
        self.assertEqual(other_p.attendance, Attendance.PENDING)

    def test_same_state_as_the_web_path(self):
        """봇으로 들어왔다고 다른 상태가 되면 뱃지 문구가 경로에 따라 갈립니다."""
        self.post("/meetings/absence", {"thread_id": THREAD, "discord_user_id": "dc-mate"})
        self.p_mate.refresh_from_db()
        self.assertTrue(self.p_mate.delegated)
        self.assertEqual(self.p_mate.attendance, Attendance.DELEGATED)

    def test_cancel(self):
        res = self.post("/meetings/absence",
                        {"thread_id": THREAD, "discord_user_id": "dc-me",
                         "delegated": False})
        self.assertFalse(res.json()["delegated"])
        self.p_me.refresh_from_db()
        self.assertEqual(self.p_me.attendance, Attendance.PENDING)

    def test_unlinked_account(self):
        res = self.post("/meetings/absence",
                        {"thread_id": THREAD, "discord_user_id": "dc-nobody"})
        self.assertEqual(res.json()["error"]["code"], "USER_NOT_FOUND")


class DelegateOnBuildsDebateTest(Base):
    """
    Discord 로 대리 참석을 켜도 준비 화면이 채워지는가.

    상태만 웹과 맞추고 예측 파이프라인을 웹 경로에만 걸어 두면, 지금 살아 있는
    대리 참석 경로 대부분이 준비 화면을 빈 칸으로 봅니다.
    """

    def test_delegate_on_queues_prediction(self):
        from unittest.mock import patch

        with patch("apps.agent.tasks.build_debate_points.delay") as spy:
            r = self.post("/delegate/on", {"discord_user_id": "dc-mate",
                                           "scope": "전체"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(spy.called, "예측이 안 걸리면 준비 화면이 빈 칸입니다")
        queued = {c.args[0] for c in spy.call_args_list}
        self.assertIn(str(self.meeting.id), queued)

    def test_internal_absence_queues_too(self):
        from unittest.mock import patch

        with patch("apps.agent.tasks.build_debate_points.delay") as spy:
            self.post("/meetings/absence", {"thread_id": THREAD,
                                            "discord_user_id": "dc-mate"})
        self.assertTrue(spy.called)

    def test_delegate_off_does_not_queue(self):
        from unittest.mock import patch

        with patch("apps.agent.tasks.build_debate_points.delay") as spy:
            self.post("/delegate/off", {"discord_user_id": "dc-me"})
        self.assertFalse(spy.called)

    def test_queue_failure_does_not_break_the_toggle(self):
        """여기서 터지면 봇은 `안 켜졌다` 로 읽고 사용자에게 다시 하라고 합니다."""
        from unittest.mock import patch

        with patch("apps.agent.tasks.build_debate_points.delay",
                   side_effect=RuntimeError("no broker")):
            r = self.post("/delegate/on", {"discord_user_id": "dc-mate",
                                           "scope": "전체"})
        self.assertEqual(r.status_code, 200)
        self.p_mate.refresh_from_db()
        self.assertTrue(self.p_mate.delegated)
