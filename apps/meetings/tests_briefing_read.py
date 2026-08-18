"""
브리핑 조회가 읽음까지 찍던 문제.

플로우 화면은 브리핑 패널을 열든 말든 회의를 열 때 브리핑을 부릅니다. 그래서
회의 화면에 잠깐 들른 것만으로 홈의 `Bordo 브리핑 보러가기` 가 사라졌습니다 —
사용자는 읽은 적이 없는데 읽은 것이 됩니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

from .models import AiBriefing, Meeting


class BriefingReadTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="b@bordo.dev", password="x" * 10,
                                          name="유수인")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                         created_by=cls.me)
        ProjectMember.objects.create(project=project, user=cls.me)
        cls.meeting = Meeting.objects.create(
            project=project, project_name="프로젝트", title="회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AiBriefing.objects.create(meeting=cls.meeting, user=cls.me,
                                  narrative="정리해 두었습니다.")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _pending(self):
        return self.client.get("/api/v1/home").data["briefing_pending"]["exists"]

    def test_mark_read_false_keeps_the_home_button(self):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing",
                            {"mark_read": "false"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._pending(), "보지도 않았는데 홈 버튼이 사라졌습니다")

    def test_default_still_marks_read(self):
        """
        기본을 끄면 이 값을 안 보내는 쪽에서는 브리핑이 영영 안 읽힌 상태로
        남아 홈 팝업이 매번 뜹니다.
        """
        self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing")
        self.assertFalse(self._pending())
