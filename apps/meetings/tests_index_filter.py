"""
좌측 인덱스가 판과 같은 엣지 집합을 보는가 (이슈 #87, #119).

인덱스를 누르면 판에서 그 화살표가 강조됩니다. 두 조회가 서로 다른 기준으로
엣지를 모으면 `related_edge_ids` 가 판에 없는 id 를 가리키고, 화면은
"눌렀는데 아무 일이 없다" 가 됩니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Agenda, FlowCategory, FlowContentType, FlowEdge,
                                  Meeting, MeetingStatus, Surface)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class MeetingIndexFilterTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="최비성")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="임수연")
        team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=team, team_name=team.name,
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="정기 회의",
            status=MeetingStatus.ENDED, scheduled_at=timezone.now(),
            ended_at=timezone.now(), created_by=cls.me)

        cls.opinion_agenda = Agenda.objects.create(
            meeting=cls.meeting, title="배포일", sort_order=1)
        cls.request_agenda = Agenda.objects.create(
            meeting=cls.meeting, title="시안 마감", sort_order=2)

        cls.opinion_edge = cls.edge(cls.opinion_agenda, FlowContentType.OPINION)
        cls.request_edge = cls.edge(cls.request_agenda, FlowContentType.REQUEST)

    @classmethod
    def edge(cls, agenda, content_type):
        node = {"id": str(cls.me.id), "kind": "USER", "user_id": str(cls.me.id),
                "name": cls.me.name}
        to = {"id": str(cls.mate.id), "kind": "USER", "user_id": str(cls.mate.id),
              "name": cls.mate.name}
        return FlowEdge.objects.create(
            meeting=cls.meeting, project=cls.project, category=FlowCategory.MEETING,
            content_type=content_type, surface=Surface.SERVICE,
            from_node=node, to_nodes=[to], label="라벨",
            direction_label="최비성 → 임수연",
            participant_ids=[str(cls.me.id), str(cls.mate.id)],
            agenda=agenda, occurred_at=timezone.now())

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def get(self, path, **params):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/{path}", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def board_edge_ids(self, **params):
        board = self.get("flow", **params)
        return {eid for arrow in board["arrows"]
                for slot in arrow["counts"] for eid in slot["edge_ids"]}

    def index_edge_ids(self, **params):
        return {eid for row in self.get("indexes", **params)["results"]
                for eid in row["related_edge_ids"]}

    def test_index_matches_the_board_without_a_filter(self):
        self.assertEqual(self.index_edge_ids(), self.board_edge_ids())

    def test_index_follows_the_content_filter(self):
        """필터를 걸면 인덱스도 같이 줄어야 강조가 걸립니다."""
        params = {"content_types": "OPINION"}
        self.assertEqual(self.index_edge_ids(**params), self.board_edge_ids(**params))
        self.assertEqual(self.index_edge_ids(**params), {str(self.opinion_edge.id)})

    def test_index_drops_rows_with_no_arrow_left(self):
        """빈 배열을 남기면 누를 수는 있는데 아무 일도 안 일어납니다."""
        rows = self.get("indexes", content_types="OPINION")["results"]
        self.assertEqual([r["label"] for r in rows], ["배포일"])
        self.assertTrue(all(r["related_edge_ids"] for r in rows))

    def test_index_never_points_outside_the_board(self):
        """판에 없는 id 를 가리키면 강조가 아무 데도 안 걸립니다."""
        for params in ({}, {"content_types": "OPINION"}, {"content_types": "REQUEST"},
                       {"surfaces": "SERVICE"}):
            self.assertTrue(self.index_edge_ids(**params) <= self.board_edge_ids(**params),
                            f"인덱스가 판 밖을 가리킵니다: {params}")

    def test_badge_count_matches_edge_ids(self):
        """뱃지는 3인데 패널에는 2개인 상태가 조용히 생기면 안 됩니다."""
        for arrow in self.get("flow")["arrows"]:
            self.assertEqual(arrow["total_count"],
                             sum(len(s["edge_ids"]) for s in arrow["counts"]))
            for slot in arrow["counts"]:
                self.assertEqual(slot["count"], len(slot["edge_ids"]))

    def test_every_badge_edge_is_fetchable(self):
        """하나라도 404 면 화면은 그것만 버리고 나머지를 그립니다."""
        for edge_id in self.board_edge_ids():
            r = self.client.get(f"/api/v1/flow-edges/{edge_id}")
            self.assertEqual(r.status_code, 200)

    def test_filter_options_carry_only_what_appeared(self):
        """전체 종류를 고정으로 주면 눌러도 판이 안 변하는 칸이 생깁니다."""
        body = self.get("flow", content_types="OPINION")
        self.assertEqual(body["filter_options"]["content_types"], ["OPINION"])

    def test_meeting_label_is_assembled_by_the_server(self):
        """화면 윗줄을 클라이언트가 만들면 사람마다 다른 날짜로 보입니다."""
        self.assertTrue(self.get("flow")["meeting_label"].endswith("정기 회의"))
