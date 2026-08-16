"""
브리핑 4섹션 테스트.

화면(`FlowBriefingSidebar`)이 그리는 순서 그대로입니다.

    회의 한눈에 보기 → 확인이 필요해요 → 답변이 필요해요 → 나에게 요청한 내용

`확인이 필요해요` · `나에게 요청한 내용` 두 섹션이 오래 비어 있었습니다. 계약에는
있는데 구현이 옛 3부분(narrative + used + deferred)에 머물러, 화면 절반을 백엔드가
채울 방법이 없었습니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentSettings
from apps.agent.services import briefing
from apps.agent.services.llm import LLMResponse
from apps.meetings.models import (Attendance, BriefingConfirmation, BriefingRequest,
                                  FlowCategory, FlowContentType, FlowEdge, Meeting,
                                  MeetingParticipant)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from apps.tasks.models import TaskStatus


class FakeLLM:
    def chat(self, messages, tools=None, system=""):
        return LLMResponse(text="한 문단")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.other = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                             name="임수연")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.other)
        cls.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                             created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.other)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AgentSettings.objects.create(user=cls.me)
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.me, user_name="서재민",
            attendance=Attendance.ABSENT, delegated=True)

    def _edge(self, content_type, *, sender, to=None, label="라벨"):
        return FlowEdge.objects.create(
            meeting=self.meeting, category=FlowCategory.MEETING,
            content_type=content_type,
            from_node={"id": f"u{sender.id}", "kind": "USER",
                       "user_id": str(sender.id), "name": sender.name},
            to_nodes=[{"id": f"u{t.id}", "kind": "USER", "user_id": str(t.id),
                       "name": t.name} for t in (to or [])],
            participant_ids=[str(t.id) for t in (to or [])],
            label=label, occurred_at=timezone.now())

    def _build(self):
        return briefing.build_for_user(self.meeting, self.me, FakeLLM())


class ConfirmationTest(Base):

    def test_change_becomes_a_confirmation_card(self):
        """내가 없는 사이 바뀐 것이 `확인이 필요해요` 로 올라옵니다."""
        self._edge(FlowContentType.CHANGE, sender=self.other,
                   label="백엔드 개발 일정 변경")
        self._build()
        card = BriefingConfirmation.objects.get(user=self.me)
        self.assertEqual(card.title, "백엔드 개발 일정 변경")

    def test_my_own_change_is_not_a_card(self):
        """내가 한 변경을 나에게 확인하라고 돌려주면 목록이 자기 발언으로 찹니다."""
        self._edge(FlowContentType.CHANGE, sender=self.me)
        self._build()
        self.assertEqual(BriefingConfirmation.objects.count(), 0)

    def test_rebuild_keeps_what_i_already_confirmed(self):
        """
        이게 카드를 JSON 이 아니라 테이블에 둔 이유입니다.

        `AiBriefing` 은 재생성 때 통째로 덮어쓰므로, 카드가 그 안에 있었다면
        확인해서 없앤 것이 되살아납니다. 회의 재종료는 실제로 일어납니다.
        """
        self._edge(FlowContentType.CHANGE, sender=self.other)
        self._build()
        card = BriefingConfirmation.objects.get(user=self.me)
        card.confirmed_at = timezone.now()
        card.save(update_fields=["confirmed_at"])

        self._build()
        card.refresh_from_db()
        self.assertIsNotNone(card.confirmed_at, "재생성이 확인 표시를 지웠습니다")
        self.assertEqual(BriefingConfirmation.objects.count(), 1)


class RequestTest(Base):

    def test_request_to_me_becomes_a_card(self):
        self._edge(FlowContentType.REQUEST, sender=self.other, to=[self.me],
                   label="8/15까지 회의 화면 디자인 수정")
        self._build()
        card = BriefingRequest.objects.get(user=self.me)
        self.assertEqual(card.requester_name, "임수연")

    def test_request_to_someone_else_is_not_mine(self):
        self._edge(FlowContentType.REQUEST, sender=self.other, to=[self.other])
        self._build()
        self.assertEqual(BriefingRequest.objects.count(), 0)


class BriefingResponseTest(Base):
    """화면이 실제로 받는 JSON."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)
        self._edge(FlowContentType.CHANGE, sender=self.other, label="일정 변경")
        self._edge(FlowContentType.REQUEST, sender=self.other, to=[self.me],
                   label="디자인 수정")
        self._build()

    def test_four_sections_are_present(self):
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing")
        self.assertEqual(r.status_code, 200)
        for key in ("narrative", "location_chips", "needs_confirmation",
                    "requests_to_me", "needs_answer"):
            self.assertIn(key, r.data, f"{key} 가 응답에 없습니다")
        self.assertEqual(len(r.data["needs_confirmation"]), 1)
        self.assertEqual(len(r.data["requests_to_me"]), 1)

    def test_request_card_body_is_assembled_by_the_server(self):
        """
        화면은 카드 두 번째 줄을 그대로 찍습니다.

        조각만 주면 클라이언트가 조사를 붙이게 되고 `임수연이가` 가 나옵니다.
        """
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing")
        self.assertEqual(r.data["requests_to_me"][0]["body"], "임수연님이 요청했어요.")

    def test_chips_carry_edge_ids(self):
        """칩을 눌러 플로우로 건너뛰려면 화살표 id 가 필요합니다."""
        r = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing")
        chip = next(c for c in r.data["location_chips"]
                    if c["content_type"] == FlowContentType.CHANGE)
        self.assertEqual(chip["count"], 1)
        self.assertEqual(len(chip["edge_ids"]), 1)


class ConfirmEndpointTest(Base):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)
        self._edge(FlowContentType.CHANGE, sender=self.other)
        self._build()
        self.card = BriefingConfirmation.objects.get(user=self.me)

    def test_confirm_removes_it_from_my_list(self):
        r = self.client.post(f"/api/v1/briefing-confirmations/{self.card.id}/confirm")
        self.assertEqual(r.status_code, 200)
        body = self.client.get(f"/api/v1/meetings/{self.meeting.id}/ai-briefing").data
        self.assertEqual(body["needs_confirmation"], [])

    def test_someone_elses_card_is_404(self):
        """403 을 주면 `그런 게 있긴 하다` 가 새어 나갑니다."""
        self.client.force_authenticate(self.other)
        r = self.client.post(f"/api/v1/briefing-confirmations/{self.card.id}/confirm")
        self.assertEqual(r.status_code, 404)


class AcceptEndpointTest(Base):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)
        self._edge(FlowContentType.REQUEST, sender=self.other, to=[self.me],
                   label="회의 화면 디자인 수정")
        self._build()
        self.card = BriefingRequest.objects.get(user=self.me)

    def test_accept_creates_a_todo_not_an_approval(self):
        """
        사람이 직접 눌러서 받은 것이라 승인 단계가 없습니다.

        여기서 PENDING_APPROVAL 을 만들면 승인 큐가 남의 회의 요청으로 가득 차
        승인이라는 행위가 뜻을 잃습니다.
        """
        r = self.client.post(f"/api/v1/briefing-requests/{self.card.id}/accept")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["task"]["status"], TaskStatus.TODO)

    def test_accepting_twice_makes_one_task(self):
        """목록에서 사라지기 전에 연타하는 경우가 실제로 있습니다."""
        from apps.tasks.models import Task
        self.client.post(f"/api/v1/briefing-requests/{self.card.id}/accept")
        self.client.post(f"/api/v1/briefing-requests/{self.card.id}/accept")
        self.assertEqual(Task.objects.count(), 1)
