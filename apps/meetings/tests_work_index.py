"""
작업 모드 좌측 인덱스.

경로에 회의 id 가 붙어 있지만 **작업 엣지에는 회의가 없습니다.** 회의로 좁히면
조건에 맞는 행이 하나도 없어 이 목록은 언제나 빈 배열이었습니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.documents.models import Document, Visibility
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

from .models import FlowCategory, Meeting, MeetingParticipant


class WorkIndexTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                          name="유수인")
        cls.mate = User.objects.create_user(email="w2@bordo.dev", password="x" * 10,
                                            name="서재민")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.mate, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                             created_by=cls.me)
        # 작업 화살표는 받는 쪽이 있어야 그려집니다(`work_flow._teammates`).
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.mate)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="프로젝트", title="회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        MeetingParticipant.objects.create(meeting=cls.meeting, user=cls.me,
                                          user_name="유수인")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _indexes(self, **params):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/indexes",
                            {"category": "WORK", **params})
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_document_shows_up_in_the_work_index(self):
        doc = Document.objects.create(project=self.project, title="API 명세",
                                      owner=self.me)
        body = self._indexes()
        self.assertEqual([r["label"] for r in body["results"]], ["API 명세"])
        self.assertEqual(str(doc.id), body["results"][0]["id"])
        # 인덱스를 누르면 이 id 로 판의 화살표를 강조합니다. 비어 있으면
        # 목록은 뜨는데 눌러도 아무 일이 없습니다.
        self.assertTrue(body["results"][0]["related_edge_ids"])

    def test_private_document_stays_hidden(self):
        """
        작업 플로우는 팀 관점 화면이라 제목만 스쳐도 비공개로 둔 뜻이 사라집니다.
        """
        Document.objects.create(project=self.project, title="비공개 메모",
                                owner=self.mate, visibility=Visibility.PRIVATE)
        self.assertEqual(self._indexes()["count"], 0)

    def test_index_period_matches_the_board(self):
        """
        인덱스와 캔버스가 다른 구간을 보면, 인덱스의 문서를 눌러도 강조될
        화살표가 판에 없습니다.
        """
        doc = Document.objects.create(project=self.project, title="지난달 문서",
                                      owner=self.me)
        old = timezone.now() - timedelta(days=30)
        doc.flow_edges.update(occurred_at=old)
        self.assertEqual(self._indexes()["count"], 0)

        params = {"from": (old - timedelta(days=1)).isoformat(),
                  "to": timezone.now().isoformat()}
        self.assertEqual(self._indexes(**params)["count"], 1)
        flow = self.client.get(f"/api/v1/projects/{self.project.id}/flow", params).data
        self.assertEqual(flow["category"], FlowCategory.WORK)
        self.assertTrue(flow["arrows"], "같은 구간인데 판에는 화살표가 없습니다")

    def test_meeting_index_is_untouched(self):
        """회의 모드는 그대로 회의 스코프입니다. 안건은 회의에 매달립니다."""
        from .models import Agenda

        Agenda.objects.create(meeting=self.meeting, title="안건 하나")
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/indexes",
                            {"category": "MEETING"})
        self.assertEqual([x["label"] for x in r.data["results"]], ["안건 하나"])
