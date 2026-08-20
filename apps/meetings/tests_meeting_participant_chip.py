"""
회의 스코프 참여자 패널에도 **`발언` 칩이 온다.**

`flow_participant()`(작업 모드)는 PR #142 로 붙었는데 회의 모드는 빠져 있었습니다.
시연에서 여는 화면이 이쪽이라 여기가 비면 실서버에서만 칩이 안 보입니다 — 가상
데이터로는 보입니다(이슈 #139).

**본인 노드와 대리인 노드를 갈라 셉니다.** 회의 판은 둘을 따로 그리므로, 안
가르면 두 노드가 같은 숫자를 들고 있어 대리인이 대신 말한 건수를 본인도 말한
것처럼 읽습니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.meetings.models import (Meeting, MeetingParticipant, MeetingStatus,
                                  Utterance)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember


class MeetingParticipantChip(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.other = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                             name="최비성")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.other):
            TeamMember.objects.create(team=cls.team, user=u, team_role="MEMBER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="멋사 중앙해커톤", created_by=cls.me)
        for u in (cls.me, cls.other):
            ProjectMember.objects.create(project=cls.project, user=u)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.me)
        self.meeting = Meeting.objects.create(
            project=self.project, project_name=self.project.name,
            title="디자인 리뷰", status=MeetingStatus.ENDED,
            scheduled_at=timezone.now(), duration_min=60, created_by=self.me)
        for u in (self.me, self.other):
            MeetingParticipant.objects.create(meeting=self.meeting, user=u,
                                              user_name=u.name)

    def speak(self, user, body, *, is_agent=False, meeting=None):
        return Utterance.objects.create(
            meeting=meeting or self.meeting, participant=user,
            participant_name=(f"{user.name}의 Bordo" if is_agent else user.name),
            body=body, is_agent=is_agent, spoken_at=timezone.now())

    def get(self, node):
        return self.api.get(
            f"/api/v1/meetings/{self.meeting.id}/participants/{node}/flow")

    def chips(self, node):
        return [c for c in self.get(node).data["counts"] if not c.get("content_type")]

    # ── 있어야 하는 것 ───────────────────────────────────────────

    def test_발언이_있으면_칩이_온다(self):
        self.speak(self.me, "일정부터 정합시다.")
        self.speak(self.me, "목요일이 낫겠습니다.")
        chips = self.chips(str(self.me.id))
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0]["label"], "발언")
        self.assertEqual(chips[0]["count"], 2)

    def test_칩에는_content_type_이_없다(self):
        """
        넣으면 화면이 **필터 버튼**으로 그린다. 누르면 걸리는 묶음이 하나도
        없어 패널이 통째로 빈다 — 버튼처럼 생겼는데 아무 일도 안 일어나는 것
        보다 처음부터 버튼이 아닌 편이 낫다.
        """
        self.speak(self.me, "한마디")
        self.assertNotIn("content_type", self.chips(str(self.me.id))[0])

    def test_맨_앞에_온다(self):
        self.speak(self.me, "한마디")
        self.assertEqual(self.get(str(self.me.id)).data["counts"][0]["label"], "발언")

    # ── 갈라야 하는 것 ───────────────────────────────────────────

    def test_본인과_대리인_발언을_갈라_센다(self):
        self.speak(self.me, "제가 직접 한 말")
        self.speak(self.me, "대리인이 한 말 1", is_agent=True)
        self.speak(self.me, "대리인이 한 말 2", is_agent=True)

        self.assertEqual(self.chips(str(self.me.id))[0]["count"], 1)
        self.assertEqual(self.chips(f"{self.me.id}:agent")[0]["count"], 2)

    def test_남의_발언은_안_섞인다(self):
        self.speak(self.me, "내 말")
        self.speak(self.other, "남의 말")
        self.assertEqual(self.chips(str(self.me.id))[0]["count"], 1)

    def test_다른_회의_발언은_안_섞인다(self):
        other_meeting = Meeting.objects.create(
            project=self.project, project_name=self.project.name,
            title="다른 회의", status=MeetingStatus.ENDED,
            scheduled_at=timezone.now(), duration_min=30, created_by=self.me)
        self.speak(self.me, "이 회의 말")
        self.speak(self.me, "저 회의 말", meeting=other_meeting)
        self.assertEqual(self.chips(str(self.me.id))[0]["count"], 1)

    # ── 없어야 하는 것 ───────────────────────────────────────────

    def test_발언이_0건이면_칩을_안_준다(self):
        """다른 칩도 있을 때만 그려지는 규칙이라 거기 맞춘다."""
        self.assertEqual(self.chips(str(self.me.id)), [])
