"""
플로우 우측 패널(`flow_participant`)의 「발언」 칩. (#139)

`counts`가 지금까지 FlowEdge의 content_type만 셌다 — 발언은 엣지가 아니라
엣지 안에서 입을 연 횟수라 실서버에서는 이 칩이 항상 빠져 있었다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import Meeting, MeetingStatus, Utterance
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class FlowParticipantUtteranceTest(TestCase):

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
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="주간 회의",
            status=MeetingStatus.ENDED, scheduled_at=timezone.now() - timedelta(days=1),
            created_by=cls.me)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.me)

    def _say(self, user, body="발언 내용", *, is_agent=False, days_ago=1):
        return Utterance.objects.create(
            meeting=self.meeting, participant=user, participant_name=user.name,
            body=body, is_agent=is_agent,
            spoken_at=timezone.now() - timedelta(days=days_ago))

    def _get(self, user_id=None, **params):
        uid = user_id or self.me.id
        return self.api.get(
            f"/api/v1/projects/{self.project.id}/flow/participants/{uid}", params)

    def test_utterance_chip_has_no_content_type_key(self):
        """content_type을 넣으면 화면이 필터 버튼으로 그리는데 걸리는 게 없어
        패널이 통째로 빈다 — 그래서 이 칩만 key로 구분한다."""
        self._say(self.me)
        chip = self._get().json()["counts"][0]
        self.assertEqual(chip, {"key": "utterance", "label": "발언", "count": 1})
        self.assertNotIn("content_type", chip)

    def test_utterance_chip_is_first(self):
        from apps.meetings.models import FlowCategory, FlowContentType, FlowEdge

        FlowEdge.objects.create(
            project=self.project, category=FlowCategory.WORK,
            content_type=FlowContentType.WORK,
            from_node={"id": str(self.me.id), "kind": "USER",
                      "user_id": str(self.me.id), "name": "서재민"},
            to_nodes=[{"id": str(self.mate.id), "kind": "USER",
                      "user_id": str(self.mate.id), "name": "최비성"}],
            participant_ids=[str(self.me.id), str(self.mate.id)],
            label="작업", occurred_at=timezone.now() - timedelta(days=1))
        self._say(self.me)

        counts = self._get().json()["counts"]
        self.assertEqual(counts[0].get("key"), "utterance")

    def test_no_utterances_omits_the_chip(self):
        """0건일 때는 칩 자체를 안 준다 — 다른 content_type 칩들과 같은
        규칙(있을 때만 그린다)을 따른다."""
        counts = self._get().json()["counts"]
        self.assertFalse(any(c.get("key") == "utterance" for c in counts))

    def test_agent_spoken_utterances_count_toward_the_owner(self):
        """대리인이 대신 한 발언도 본인 몫으로 센다 — 이 패널이 이미
        본인·대리인 노드를 한 사람으로 묶어 보여 주고 있어서다."""
        self._say(self.me, "본인 발언")
        self._say(self.me, "대리인 발언", is_agent=True)
        chip = self._get().json()["counts"][0]
        self.assertEqual(chip["count"], 2)

    def test_utterances_outside_the_period_are_excluded(self):
        self._say(self.me, days_ago=30)
        counts = self._get(**{"from": (timezone.now() - timedelta(days=7)).isoformat(),
                              "to": timezone.now().isoformat()}).json()["counts"]
        self.assertFalse(any(c.get("key") == "utterance" for c in counts))

    def test_other_persons_utterances_do_not_leak_in(self):
        self._say(self.mate)
        counts = self._get().json()["counts"]
        self.assertFalse(any(c.get("key") == "utterance" for c in counts))
