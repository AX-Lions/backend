"""
회의 대리 참석 준비 — 불참 등록.

막아야 하는 것: 끝난 회의에 불참을 켜는 것, 참석자가 아닌 사람이 등록하는 것,
껐다 켤 때 적어 둔 지시가 사라지는 것.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus)
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
