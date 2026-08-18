"""
`POST /meetings/{id}/delegate` 는 **보낸 것만** 바꿉니다.

한 요청에 뜻이 셋 실려 있어(켜고 끄기 · 사전 지시 · 자료 범위) 하나만 바꾸려는
호출이 나머지를 지우면, 화면에는 저장됐다고 뜨는데 대리인은 그 지시를 못 받은
채 회의에 들어갑니다. 데이터가 사라지는 종류라 회귀로 되돌아오면 안 됩니다.

이 경로는 홈 팝업이 붙어 있던 옛 자리입니다. 새로 붙는 곳은 `/absence` 를
쓰지만, 엔드포인트가 살아 있는 한 여기서 막아야 합니다 — 막는 자리는
클라이언트가 아니라 서버입니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember

PROMPT = "일정 관련 결정은 제 확인을 받도록 하고, 시안 일정은 8/18을 넘기지 마세요."


class DelegatePartialUpdate(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민", timezone="Asia/Seoul")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="멋사 중앙해커톤", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.me)
        self.meeting = Meeting.objects.create(
            project=self.project, project_name=self.project.name,
            title="디자인 리뷰", status=MeetingStatus.SCHEDULED,
            scheduled_at=timezone.now() + timezone.timedelta(hours=2),
            duration_min=60, created_by=self.me)
        self.row = MeetingParticipant.objects.create(
            meeting=self.meeting, user=self.me, user_name=self.me.name)
        self.url = f"/api/v1/meetings/{self.meeting.id}/delegate"

    def post(self, body):
        return self.api.post(self.url, body, format="json")

    def fresh(self):
        self.row.refresh_from_db()
        return self.row

    # ── 지우지 않는다 ────────────────────────────────────────────

    def test_자료_범위만_바꾸면_사전_지시가_남는다(self):
        """이 이슈의 본체다. 적어 둔 지시가 자료 범위를 고쳤다고 사라지면 안 된다."""
        self.post({"enabled": True, "prompt": PROMPT, "sources": ["work", "plan"]})
        self.assertEqual(self.fresh().delegate_prompt, PROMPT)

        r = self.post({"enabled": True, "sources": ["work"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fresh().delegate_prompt, PROMPT)
        self.assertEqual(r.data["prompt"], PROMPT)
        self.assertEqual(self.fresh().allowed_sources, ["work"])

    def test_사전_지시만_바꾸면_자료_범위가_남는다(self):
        self.post({"enabled": True, "prompt": PROMPT, "sources": ["work"]})

        self.post({"enabled": True, "prompt": "짧게만 답해 주세요."})
        row = self.fresh()
        self.assertEqual(row.delegate_prompt, "짧게만 답해 주세요.")
        self.assertEqual(row.allowed_sources, ["work"])

    def test_끄고_켜도_준비한_것이_남는다(self):
        """
        껐다 켜는 사이에 지시가 사라지면, 잠깐 참석하기로 했다가 마음을 바꾼
        사람이 준비 화면을 처음부터 다시 채워야 한다.
        """
        self.post({"enabled": True, "prompt": PROMPT, "sources": ["thought"]})

        self.post({"enabled": False})
        row = self.fresh()
        self.assertFalse(row.delegated)
        self.assertEqual(row.attendance, Attendance.PENDING)
        self.assertEqual(row.delegate_prompt, PROMPT)
        self.assertEqual(row.allowed_sources, ["thought"])

        self.post({"enabled": True})
        row = self.fresh()
        self.assertTrue(row.delegated)
        self.assertEqual(row.delegate_prompt, PROMPT)

    # ── 빈 문자열은 여전히 지우는 뜻이다 ──────────────────────────

    def test_빈_문자열을_보내면_지운다(self):
        """
        **키를 안 보낸 것과 빈 문자열을 보낸 것은 다른 뜻이다.**

        지우는 길이 없으면 잘못 적은 지시를 되돌릴 방법이 없어진다.
        """
        self.post({"enabled": True, "prompt": PROMPT})
        self.post({"enabled": True, "prompt": ""})
        self.assertEqual(self.fresh().delegate_prompt, "")

    def test_빈_배열은_아무_자료도_안_쓴다는_뜻이다(self):
        """`[]` 를 「제한 없음」 으로 읽으면 전부 끈 사람의 대리인이 다 보게 된다."""
        self.post({"enabled": True, "sources": []})
        self.assertEqual(self.fresh().allowed_sources, [])

        self.post({"enabled": True, "sources": None})
        self.assertIsNone(self.fresh().allowed_sources)

    # ── 바꾸지 않은 것 ───────────────────────────────────────────

    def test_enabled_는_키가_없으면_여전히_켜기다(self):
        """
        여기만 부분 갱신에서 벗어난다.

        이 엔드포인트를 부르는 것 자체가 「맡긴다」 는 뜻으로 쓰여 왔다. 뜻을
        바꾸면 맡겼다고 생각한 회의가 조용히 안 맡겨진다 — 지금 고치려는 것보다
        나쁜 종류의 실패다.
        """
        r = self.post({"prompt": PROMPT})
        self.assertEqual(r.status_code, 200)
        row = self.fresh()
        self.assertTrue(row.delegated)
        self.assertEqual(row.attendance, Attendance.DELEGATED)

    def test_참석자가_아니면_404(self):
        """403 을 주면 '그런 회의가 있긴 하다' 가 새어 나간다."""
        other = User.objects.create_user(email="x@bordo.dev", password="x" * 10,
                                         name="최비성")
        TeamMember.objects.create(team=self.team, user=other, team_role="MEMBER")
        ProjectMember.objects.create(project=self.project, user=other)
        api = APIClient()
        api.force_authenticate(other)
        r = api.post(self.url, {"enabled": True}, format="json")
        self.assertEqual(r.status_code, 404)
