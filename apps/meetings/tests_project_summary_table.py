"""
플로우 작업 모드 요약표(`project_summary_table`). (#148)

회의 모드에는 있고 작업 모드에는 없어서 화면이 "아직 서버에 없습니다"를
고정으로 띄우고 있었다. 응답 모양은 회의 모드(`MeetingSummarySerializer`)와
맞춘다 — 화면의 `toSummaryColumns`가 그 모양을 그대로 읽는다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import FlowCategory, FlowEdge, FlowContentType, WorkSummary
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class ProjectSummaryTableTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.me)

    def _edge(self, label, *, days_ago):
        return FlowEdge.objects.create(
            project=self.project, category=FlowCategory.WORK,
            content_type=FlowContentType.WORK,
            from_node={"id": str(self.me.id), "kind": "USER",
                      "user_id": str(self.me.id), "name": "서재민"},
            to_nodes=[], participant_ids=[str(self.me.id)], label=label,
            occurred_at=timezone.now() - timedelta(days=days_ago))

    def _get(self, **params):
        return self.api.get(f"/api/v1/projects/{self.project.id}/summary-table", params)

    def test_no_summary_yet_returns_empty_columns_not_404(self):
        """행이 아직 없어도 get_or_create로 빈 표를 준다 — 없다고 막지 않는다."""
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["discovered_issues"], [])

    def test_plain_string_items_pass_through(self):
        WorkSummary.objects.create(project=self.project, one_line="한 줄 요약",
                                   next_plans=["다음 계획입니다."])
        data = self._get().json()
        self.assertEqual(data["one_line"], "한 줄 요약")
        self.assertEqual(data["next_plans"], ["다음 계획입니다."])

    def test_related_edge_ids_outside_the_period_are_clipped(self):
        """
        판에 없는 화살표 id를 그대로 내려주면 눌러도 강조가 안 되는데
        오류도 안 나서 알아챌 방법이 없다(회의 모드에서 한 번 어긋났던 자리).
        """
        old_edge = self._edge("오래된 작업", days_ago=30)
        WorkSummary.objects.create(project=self.project, changes=[
            {"text": "무언가 바뀌었습니다.", "context": "맥락",
             "related_edge_ids": [str(old_edge.id)]},
        ])

        narrow = self._get(**{"from": (timezone.now() - timedelta(days=7)).isoformat(),
                              "to": timezone.now().isoformat()}).json()
        self.assertEqual(narrow["changes"][0]["related_edge_ids"], [])

        wide = self._get(**{"from": (timezone.now() - timedelta(days=31)).isoformat(),
                            "to": timezone.now().isoformat()}).json()
        self.assertEqual(wide["changes"][0]["related_edge_ids"], [str(old_edge.id)])

    def test_clipping_does_not_drop_the_item_itself(self):
        """
        related_edge_ids가 비어도 text·context는 그대로 남는다 — 화면의
        hasDetail은 related_edge_ids가 아니라 context·debates·resolution만
        본다.
        """
        old_edge = self._edge("오래된 작업", days_ago=30)
        WorkSummary.objects.create(project=self.project, discovered_issues=[
            {"text": "문제였습니다.", "context": "맥락",
             "related_edge_ids": [str(old_edge.id)]},
        ])
        narrow = self._get(**{"from": timezone.now().isoformat(),
                              "to": timezone.now().isoformat()}).json()
        self.assertEqual(len(narrow["discovered_issues"]), 1)
        self.assertEqual(narrow["discovered_issues"][0]["text"], "문제였습니다.")
        self.assertEqual(narrow["discovered_issues"][0]["context"], "맥락")

    def test_outsider_cannot_read(self):
        outsider = User.objects.create_user(email="out@bordo.dev", password="x" * 10,
                                            name="남")
        self.api.force_authenticate(user=outsider)
        r = self._get()
        self.assertIn(r.status_code, (403, 404))
