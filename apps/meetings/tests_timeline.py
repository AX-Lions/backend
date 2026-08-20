"""
시간순 인덱스 (이슈 #134).

판은 "누가 누구에게" 는 보여 주는데 "언제, 몇 번째로" 는 말하지 못했습니다.
이 목록이 그 자리를 채우고 맥락 재생의 대본이 됩니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Agenda, FlowCategory, FlowContentType, FlowEdge,
                                  Meeting, MeetingStatus, Surface)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class MeetingTimelineTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="최비성", timezone="Asia/Seoul")
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
        cls.agenda = Agenda.objects.create(meeting=cls.meeting, title="배포일",
                                           sort_order=1)

        base = timezone.now() - timedelta(hours=1)
        cls.second = cls.edge(FlowContentType.REQUEST, base + timedelta(minutes=10))
        cls.first = cls.edge(FlowContentType.OPINION, base, agenda=cls.agenda)

    @classmethod
    def edge(cls, content_type, at, agenda=None):
        node = {"id": str(cls.me.id), "kind": "USER", "user_id": str(cls.me.id),
                "name": cls.me.name}
        to = {"id": str(cls.mate.id), "kind": "USER", "user_id": str(cls.mate.id),
              "name": cls.mate.name}
        return FlowEdge.objects.create(
            meeting=cls.meeting, project=cls.project, category=FlowCategory.MEETING,
            content_type=content_type, surface=Surface.DISCORD,
            from_node=node, to_nodes=[to], label="라벨",
            direction_label="최비성 → 임수연",
            participant_ids=[str(cls.me.id), str(cls.mate.id)],
            agenda=agenda, occurred_at=at)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def get(self, **params):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/timeline", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_sorted_by_time_with_seq_from_one(self):
        rows = self.get()["results"]
        self.assertEqual([x["seq"] for x in rows], [1, 2])
        self.assertEqual(rows[0]["edge_id"], str(self.first.id))

    def test_same_instant_keeps_a_stable_order(self):
        """조회할 때마다 순서가 흔들리면 재생이 매번 다른 이야기를 합니다."""
        at = timezone.now()
        a, b = self.edge(FlowContentType.ETC, at), self.edge(FlowContentType.ETC, at)
        expected = sorted([str(a.id), str(b.id)])
        for _ in range(3):
            ids = [x["edge_id"] for x in self.get()["results"]][-2:]
            self.assertEqual(ids, expected)

    def test_title_falls_back_to_the_content_label(self):
        rows = {x["edge_id"]: x for x in self.get()["results"]}
        self.assertEqual(rows[str(self.first.id)]["title"], "배포일")
        self.assertEqual(rows[str(self.second.id)]["title"], "요청사항")

    def test_server_finishes_the_clock_label(self):
        """클라이언트가 찍으면 같은 발언을 사람마다 다른 시각으로 봅니다."""
        row = self.get()["results"][0]
        expected = f"{timezone.localtime(self.first.occurred_at, timezone.get_fixed_timezone(540)):%H:%M}"
        self.assertEqual(row["at_label"], expected)

    def test_carries_the_board_fields(self):
        row = self.get()["results"][0]
        self.assertEqual(row["direction_label"], "최비성 → 임수연")
        self.assertEqual(row["from_node_id"], str(self.me.id))
        self.assertEqual(row["to_node_ids"], [str(self.mate.id)])
        self.assertEqual(row["surface"], "DISCORD")
        self.assertEqual(row["related_edge_ids"], [row["edge_id"]])

    def test_matches_the_board_exactly(self):
        """
        판에 올라간 엣지가 빠지면 끝까지 재생해도 영영 안 나타납니다.
        반대로 판에 없는 것이 들어가면 눌러도 강조될 것이 없습니다.
        """
        board = {eid for arrow in
                 self.client.get(f"/api/v1/meetings/{self.meeting.id}/flow").data["arrows"]
                 for slot in arrow["counts"] for eid in slot["edge_ids"]}
        self.assertEqual({x["edge_id"] for x in self.get()["results"]}, board)

    def test_empty_meeting_gives_an_empty_list(self):
        other = Meeting.objects.create(
            project=self.project, project_name=self.project.name, title="빈 회의",
            status=MeetingStatus.SCHEDULED, scheduled_at=timezone.now(),
            created_by=self.me)
        r = self.client.get(f"/api/v1/meetings/{other.id}/timeline")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"count": 0, "results": []})


class ProjectTimelineTest(TestCase):
    """작업 모드는 회의가 아니라 **기간**으로 봅니다."""

    def setUp(self):
        self.me = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                           name="서재민", timezone="Asia/Seoul")
        team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=team, user=self.me, team_role=TeamRole.OWNER)
        self.project = Project.objects.create(team=team, team_name=team.name,
                                              name="Bordo", created_by=self.me)
        ProjectMember.objects.create(project=self.project, user=self.me)

        node = {"id": str(self.me.id), "kind": "USER", "user_id": str(self.me.id),
                "name": self.me.name}
        now = timezone.now()
        for minutes, ctype in ((30, FlowContentType.WORK),
                               (10, FlowContentType.FEEDBACK)):
            FlowEdge.objects.create(
                project=self.project, category=FlowCategory.WORK,
                content_type=ctype, surface=Surface.SERVICE,
                from_node=node, to_nodes=[node], label="라벨",
                direction_label="서재민 → 서재민",
                participant_ids=[str(self.me.id)],
                occurred_at=now - timedelta(minutes=minutes))

        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def get(self, **params):
        params.setdefault("category", "WORK")
        r = self.client.get(f"/api/v1/projects/{self.project.id}/timeline", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_lists_work_edges_in_order(self):
        rows = self.get()["results"]
        self.assertEqual([x["seq"] for x in rows], [1, 2])
        self.assertEqual([x["label"] for x in rows], ["작업", "피드백"])

    def test_matches_the_board_exactly(self):
        board = {eid for arrow in
                 self.client.get(f"/api/v1/projects/{self.project.id}/flow",
                                 {"category": "WORK"}).data["arrows"]
                 for slot in arrow["counts"] for eid in slot["edge_ids"]}
        self.assertEqual({x["edge_id"] for x in self.get()["results"]}, board)

    def test_outside_the_period_is_left_out(self):
        """작업 플로우의 스코프는 기간입니다. 오래된 것까지 실으면 재생이 끝나지 않습니다."""
        old = FlowEdge.objects.first()
        FlowEdge.objects.filter(pk=old.pk).update(
            occurred_at=timezone.now() - timedelta(days=90))
        self.assertNotIn(str(old.id), {x["edge_id"] for x in self.get()["results"]})
