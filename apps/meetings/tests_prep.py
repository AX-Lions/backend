"""
회의 대리 참석 준비 — 불참 등록.

막아야 하는 것: 끝난 회의에 불참을 켜는 것, 참석자가 아닌 사람이 등록하는 것,
껐다 켤 때 적어 둔 지시가 사라지는 것.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Attendance, DebatePoint, DebateStance, Meeting,
                                  MeetingParticipant, MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민", timezone="Asia/Seoul")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="임수연")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        TeamMember.objects.create(team=cls.team, user=cls.mate, team_role="MEMBER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="멋사 중앙해커톤", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.me)

    def meeting(self, status=MeetingStatus.SCHEDULED, **kw):
        m = Meeting.objects.create(
            project=self.project, project_name=self.project.name,
            title="글로벌 회의 일정 및 개발 방향 논의", status=status,
            scheduled_at=timezone.now() + timezone.timedelta(hours=2),
            duration_min=60, created_by=self.me, **kw)
        MeetingParticipant.objects.create(meeting=m, user=self.me, user_name=self.me.name)
        return m

    def url(self, m, tail="absence"):
        return f"/api/v1/meetings/{m.id}/{tail}"


class AbsenceTest(Base):

    def test_register_turns_on_delegation(self):
        m = self.meeting()
        res = self.api.post(self.url(m))
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body["delegated"])
        self.assertIn("대리 참석 예정", body["badge"])
        self.assertIn("prep", body["prep_url"])
        p = MeetingParticipant.objects.get(meeting=m, user=self.me)
        self.assertTrue(p.delegated)
        self.assertEqual(p.attendance, Attendance.DELEGATED)

    def test_header_strings_are_server_made(self):
        m = self.meeting()
        body = self.api.post(self.url(m)).json()
        self.assertEqual(body["team_name"], "AX Lions")
        self.assertEqual(body["project_name"], "멋사 중앙해커톤")
        # `8월 18일 14:00 - 15:00` 모양
        self.assertRegex(body["when"], r"^\d+월 \d+일 \d\d:\d\d - \d\d:\d\d$")
        self.assertEqual(body["location"], "서비스")

    def test_badge_says_now_while_meeting_is_active(self):
        m = self.meeting(status=MeetingStatus.ACTIVE)
        body = self.api.post(self.url(m)).json()
        self.assertIn("대리 참석 중", body["badge"])

    def test_twice_is_not_an_error(self):
        m = self.meeting()
        self.assertEqual(self.api.post(self.url(m)).status_code, 201)
        again = self.api.post(self.url(m))
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["created"])

    def test_ended_meeting_is_locked(self):
        m = self.meeting(status=MeetingStatus.ENDED)
        res = self.api.post(self.url(m))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "MEETING_LOCKED")

    def test_cancel_keeps_what_i_wrote(self):
        """마음을 바꿔 다시 누를 때 화면을 두 번 채우게 하지 않습니다."""
        m = self.meeting()
        p = MeetingParticipant.objects.get(meeting=m, user=self.me)
        p.delegate_prompt = "일정은 확정하지 마"
        p.allowed_sources = ["work"]
        p.save()

        self.api.post(self.url(m))
        res = self.api.delete(self.url(m))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["delegated"])

        p.refresh_from_db()
        self.assertFalse(p.delegated)
        self.assertEqual(p.attendance, Attendance.PENDING)
        self.assertEqual(p.delegate_prompt, "일정은 확정하지 마")
        self.assertEqual(p.allowed_sources, ["work"])

    def test_cancel_during_active_meeting_marks_present(self):
        m = self.meeting(status=MeetingStatus.ACTIVE)
        self.api.post(self.url(m))
        self.api.delete(self.url(m))
        p = MeetingParticipant.objects.get(meeting=m, user=self.me)
        self.assertEqual(p.attendance, Attendance.PRESENT)

    def test_non_participant_gets_404_not_403(self):
        """`권한 없음` 으로 주면 초대해 달라고 해야 할 사람이 권한을 달라고 합니다."""
        m = self.meeting()
        self.api.force_authenticate(self.mate)
        res = self.api.post(self.url(m))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"]["code"], "STATE_NOT_FOUND")

    def test_outsider_cannot_see_the_meeting(self):
        m = self.meeting()
        stranger = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                            name="남")
        self.api.force_authenticate(stranger)
        self.assertEqual(self.api.post(self.url(m)).status_code, 403)


class PrepReadTest(Base):

    def setUp(self):
        super().setUp()
        self.m = self.meeting()
        self.api.post(self.url(self.m))

    def point(self, order=1, title="개발 범위를 축소할 것인가, 기존 범위를 유지할 것인가?"):
        return DebatePoint.objects.create(
            meeting=self.m, source_key=f"k{order}", order=order, title=title,
            options=[{"key": "A", "title": "핵심 기능만 구현", "description": "범위를 축소"},
                     {"key": "B", "title": "기존 기획 범위 유지", "description": "최대한 구현"}],
            rationale="이전 회의에서 범위를 축소하자는 의견이 있었어요.",
            evidence=[{"kind": "meeting", "title": "8월 15일 · 기능 구현 범위 논의"}])

    def test_one_call_fills_the_page(self):
        self.point()
        res = self.api.get(self.url(self.m, "prep"))
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(set(body), {"header", "debate", "agent_setup"})
        self.assertTrue(body["header"]["delegated"])
        self.assertEqual(body["debate"]["count"], 1)
        self.assertIn("1개의 논쟁점", body["debate"]["notice"])

    def test_point_row_shape(self):
        self.point(order=2)
        row = self.api.get(self.url(self.m, "prep")).json()["debate"]["points"][0]
        self.assertEqual(row["label"], "논쟁점 02")
        self.assertEqual(row["status_label"], "답변필요")
        self.assertIsNone(row["stance"])
        self.assertEqual(len(row["options"]), 2)
        self.assertEqual(row["evidence"][0]["kind"], "meeting")

    def test_my_stance_marks_answered(self):
        p = self.point()
        DebateStance.objects.create(point=p, user=self.me, option_key="A", body="축소가 맞아요")
        body = self.api.get(self.url(self.m, "prep")).json()
        row = body["debate"]["points"][0]
        self.assertEqual(row["status_label"], "답변완료")
        self.assertEqual(row["stance"]["body"], "축소가 맞아요")
        self.assertEqual(body["debate"]["answered_count"], 1)

    def test_other_persons_stance_is_not_mine(self):
        """입장은 사람마다 갈립니다. 남의 것이 내 화면에 뜨면 대리인이 남의 말을 합니다."""
        p = self.point()
        DebateStance.objects.create(point=p, user=self.mate, body="유지가 맞아요")
        row = self.api.get(self.url(self.m, "prep")).json()["debate"]["points"][0]
        self.assertIsNone(row["stance"])
        self.assertEqual(row["status_label"], "답변필요")

    def test_empty_notice_when_nothing_predicted(self):
        body = self.api.get(self.url(self.m, "prep")).json()
        self.assertEqual(body["debate"]["count"], 0)
        self.assertIn("아직", body["debate"]["notice"])

    def test_setup_defaults_to_standing(self):
        setup = self.api.get(self.url(self.m, "prep")).json()["agent_setup"]
        self.assertEqual(setup["mode"], "STANDING")
        self.assertEqual(setup["mode_label"], "현재 설정 사용")
        self.assertEqual(setup["overridden_keys"], [])
        # 설정 행이 없는 사용자도 화면이 서야 합니다
        self.assertIn("settings", setup)

    def test_setup_shows_override_and_standing_side_by_side(self):
        from apps.agent.models import AgentSettings
        AgentSettings.objects.create(user=self.me, disclose_work=True)
        p = MeetingParticipant.objects.get(meeting=self.m, user=self.me)
        p.settings_override = {"disclose_work": False}
        p.delegate_prompt = "이번엔 일정 얘기 하지 마"
        p.save()

        setup = self.api.get(self.url(self.m, "prep")).json()["agent_setup"]
        self.assertEqual(setup["mode"], "ONCE")
        self.assertFalse(setup["settings"]["disclose_work"])
        self.assertTrue(setup["standing_settings"]["disclose_work"])
        self.assertEqual(setup["overridden_keys"], ["disclose_work"])
        self.assertEqual(setup["extra_note"], "이번엔 일정 얘기 하지 마")


class StanceTest(Base):

    def setUp(self):
        super().setUp()
        self.m = self.meeting()
        self.api.post(self.url(self.m))
        self.p = DebatePoint.objects.create(
            meeting=self.m, source_key="k1", order=1,
            title="개발 범위를 축소할 것인가?",
            options=[{"key": "A", "title": "축소"}, {"key": "B", "title": "유지"}])

    def surl(self):
        return f"/api/v1/debate-points/{self.p.id}/stance"

    def test_save_and_overwrite(self):
        res = self.api.put(self.surl(), {"body": "축소가 맞아요", "option_key": "A"},
                           format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status_label"], "답변완료")

        again = self.api.put(self.surl(), {"body": "생각이 바뀌었어요"}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(DebateStance.objects.filter(point=self.p, user=self.me).count(), 1)
        self.assertEqual(again.json()["stance"]["body"], "생각이 바뀌었어요")
        self.assertIsNone(again.json()["stance"]["option_key"])

    def test_empty_body_is_400(self):
        res = self.api.put(self.surl(), {"body": "   "}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_unknown_option_is_400_with_choices(self):
        res = self.api.put(self.surl(), {"body": "x", "option_key": "Z"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["details"]["allowed"], ["A", "B"])

    def test_delete(self):
        self.api.put(self.surl(), {"body": "축소"}, format="json")
        self.assertEqual(self.api.delete(self.surl()).status_code, 204)
        self.assertFalse(DebateStance.objects.filter(point=self.p).exists())
        # 없는 것을 지우면 404 — 조용히 204 를 주면 저장이 안 된 것을 못 알아챕니다
        self.assertEqual(self.api.delete(self.surl()).status_code, 404)

    def test_ended_meeting_rejects_new_stance(self):
        Meeting.objects.filter(pk=self.m.pk).update(status=MeetingStatus.ENDED)
        res = self.api.put(self.surl(), {"body": "늦었어요"}, format="json")
        self.assertEqual(res.status_code, 409)

    def test_non_participant_cannot_write(self):
        self.api.force_authenticate(self.mate)
        res = self.api.put(self.surl(), {"body": "남의 회의"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_unknown_point(self):
        import uuid
        res = self.api.put(f"/api/v1/debate-points/{uuid.uuid4()}/stance",
                           {"body": "x"}, format="json")
        self.assertEqual(res.json()["error"]["code"], "DEBATE_POINT_NOT_FOUND")
