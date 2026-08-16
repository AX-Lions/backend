"""
작업(Work) 플로우 조회.

회의 플로우와 달리 **기간이 스코프**입니다. 화면 헤더가 `8.10 - 8.16 작업 흐름`
이고 붙일 회의가 없습니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (FlowCategory, FlowContentType, FlowEdge,
                                  FlowSource, Meeting)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class ProjectFlowTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="최비성")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.me)

    def _edge(self, ctype, *, days_ago=1, source="", category=FlowCategory.WORK,
              meeting=None):
        return FlowEdge.objects.create(
            project=self.project, meeting=meeting, category=category,
            content_type=ctype, source=source,
            from_node={"id": str(self.me.id), "kind": "USER",
                       "user_id": str(self.me.id), "name": "서재민"},
            to_nodes=[{"id": str(self.mate.id), "kind": "USER",
                       "user_id": str(self.mate.id), "name": "최비성"}],
            participant_ids=[str(self.me.id), str(self.mate.id)],
            label="로그인 API 구현",
            occurred_at=timezone.now() - timedelta(days=days_ago))

    def _get(self, **params):
        return self.api.get(f"/api/v1/projects/{self.project.id}/flow", params)

    def test_work_edges_without_a_meeting_are_returned(self):
        """작업 엣지에는 회의가 없습니다. 그게 이 API 가 따로 있는 이유입니다."""
        self._edge(FlowContentType.WORK)
        r = self._get()
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(len(r.json()["arrows"]), 1)

    def test_period_label_is_built_by_the_server(self):
        """클라이언트가 만들면 시간대·표기가 사람마다 갈립니다."""
        self._edge(FlowContentType.WORK)
        label = self._get().json()["period_label"]
        self.assertIn("작업 흐름", label)
        self.assertRegex(label, r"\d+\.\d+ - \d+\.\d+")

    def test_edges_outside_the_period_are_cut(self):
        self._edge(FlowContentType.WORK, days_ago=1)
        self._edge(FlowContentType.REVISION, days_ago=30)
        self.assertEqual(len(self._get().json()["arrows"]), 1)

    def test_period_can_be_widened(self):
        self._edge(FlowContentType.WORK, days_ago=30)
        wide = self._get(**{"from": (timezone.now() - timedelta(days=60)).isoformat()})
        self.assertEqual(len(wide.json()["arrows"]), 1)

    def test_opacity_follows_the_chosen_period(self):
        """
        같은 엣지도 기간을 넓게 잡으면 진하기가 달라져야 합니다.

        저장값을 그대로 쓰면 사용자가 기간을 바꿔도 그림이 그대로입니다.
        회의는 끝나면 구간이 고정되지만 작업 플로우의 구간은 **사용자가 고릅니다.**
        """
        self._edge(FlowContentType.WORK, days_ago=6)
        narrow = self._get().json()["arrows"][0]["opacity"]
        wide = self._get(
            **{"from": (timezone.now() - timedelta(days=60)).isoformat()}
        ).json()["arrows"][0]["opacity"]
        self.assertNotEqual(narrow, wide)

    def test_work_types_are_the_five_from_the_design(self):
        for t in (FlowContentType.WORK, FlowContentType.REVISION,
                  FlowContentType.FEEDBACK, FlowContentType.SHARE,
                  FlowContentType.AI_LOOKUP):
            self._edge(t)
        opts = self._get().json()["filter_options"]["content_types"]
        self.assertEqual(opts, ["WORK", "REVISION", "FEEDBACK", "SHARE", "AI_LOOKUP"])

    def test_meeting_only_types_are_rejected_in_work_mode(self):
        """
        빈 배열이 아니라 400 입니다. 빈 배열을 주면 "조건에 맞는 게 없다" 와
        "조건 자체가 틀렸다" 를 클라이언트가 구별하지 못합니다.
        """
        self._edge(FlowContentType.WORK)
        r = self._get(content_types="OPINION")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "VALIDATION_ERROR")

    def test_source_filter(self):
        self._edge(FlowContentType.WORK, source=FlowSource.GITHUB)
        self._edge(FlowContentType.SHARE, source=FlowSource.FIGMA)
        self.assertEqual(len(self._get(sources="GITHUB").json()["arrows"]), 1)
        self.assertEqual(
            self._get().json()["filter_options"]["sources"], ["FIGMA", "GITHUB"])

    def test_unknown_source_is_400(self):
        self._edge(FlowContentType.WORK)
        self.assertEqual(self._get(sources="JIRA").status_code, 400)

    def test_meeting_edges_do_not_leak_into_work_mode(self):
        """
        같은 프로젝트에 회의 엣지도 쌓입니다. 카테고리로 갈리지 않으면 작업
        화면에 회의 발언이 섞여 나옵니다.
        """
        m = Meeting.objects.create(project=self.project, project_name="Bordo",
                                   title="회의", scheduled_at=timezone.now(),
                                   created_by=self.me)
        self._edge(FlowContentType.OPINION, category=FlowCategory.MEETING, meeting=m)
        self._edge(FlowContentType.WORK)
        self.assertEqual(len(self._get().json()["arrows"]), 1)

    def test_outsider_cannot_read(self):
        stranger = User.objects.create_user(email="x@bordo.dev", password="x" * 10,
                                            name="남")
        api = APIClient()
        api.force_authenticate(user=stranger)
        r = api.get(f"/api/v1/projects/{self.project.id}/flow")
        self.assertIn(r.status_code, (403, 404))

    def test_from_after_to_is_400(self):
        now = timezone.now()
        r = self._get(**{"from": now.isoformat(),
                         "to": (now - timedelta(days=3)).isoformat()})
        self.assertEqual(r.status_code, 400)
