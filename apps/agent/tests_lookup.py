"""
대리인이 다른 대리인에게 묻기.

작업 플로우의 `AI 조회` 입니다. 여기서 보는 것은 셋입니다.

    1. 서버를 거치는가 — AI 끼리 직접 통신하지 않는다(설계 2원칙)
    2. 무한 조회가 끊기는가 — 서로 묻는 것이 끝나지 않으면 안 된다
    3. 답을 못 받아도 기록이 남는가 — 안 남기면 알아보지도 않은 줄 안다
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentLookup, AgentRun, AgentSettings
from apps.agent.services import lookup
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.meetings.models import FlowCategory, FlowContentType, FlowEdge
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from apps.states.models import WorkItem


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)

    def chat(self, messages, tools=None, system=""):
        return self._q.pop(0) if self._q else LLMResponse(text="끝")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.mate = User.objects.create_user(email="b@bordo.dev", password="x" * 10,
                                            name="강다은")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=cls.team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)
            AgentSettings.objects.create(user=u)

    def _run(self, hop_count=0):
        return AgentRun.objects.create(user=self.me, hop_count=hop_count,
                                       status=AgentRun.Status.COMPLETED)

    def _answering(self):
        """상대 대리인이 근거를 찾아 답하는 대화."""
        WorkItem.objects.create(project=self.project, owner=self.mate,
                                title="모바일 반응형", summary="기본 레이아웃 완료",
                                status="IN_PROGRESS")
        return FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "모바일 반응형"})]),
            LLMResponse(text="기본 레이아웃 적용을 완료했어요."),
        )

    def _ask(self, llm, run=None):
        with patch("apps.agent.services.react.default_client", llm):
            return lookup.ask_peer(
                asker=self.me, target=self.mate,
                topic="모바일 반응형 작업 진행 현황",
                reason="회의 화면 개발에 모바일 대응 상황이 필요했습니다.",
                question="모바일 반응형 작업은 어디까지 진행됐나요?",
                project_id=self.project.id, run=run or self._run())


class AskPeerTest(Base):

    def test_answer_is_recorded_in_four_parts(self):
        row = self._ask(self._answering())
        self.assertIsNotNone(row)
        self.assertEqual(row.topic, "모바일 반응형 작업 진행 현황")
        self.assertIn("회의 화면 개발", row.reason)
        self.assertIn("어디까지", row.question)
        self.assertIn("기본 레이아웃", row.answer)

    def test_the_target_agent_actually_runs(self):
        """
        묻는 쪽이 상대 기록을 직접 뒤지는 것이 아닙니다. **상대의 대리인이
        자기 정책으로 판단**해야 합니다 — 그래야 공개 설정이 지켜집니다.
        """
        before = AgentRun.objects.filter(user=self.mate).count()
        self._ask(self._answering())
        self.assertEqual(AgentRun.objects.filter(user=self.mate).count(), before + 1)

    def test_a_deferred_answer_is_still_recorded(self):
        """
        답을 못 받아도 남겨야 합니다. 안 남기면 사용자는 자기 대리인이
        알아보지도 않은 줄 압니다.
        """
        row = self._ask(FakeLLM(LLMResponse(text="STATUS"),
                                LLMResponse(text="아마 곧 끝날 겁니다")))
        self.assertIsNotNone(row)
        self.assertEqual(row.answer, "")
        self.assertEqual(AgentLookup.objects.count(), 1)

    def test_hop_limit_stops_endless_lookups(self):
        """
        A 의 대리인이 B 에게 묻고 B 가 다시 A 에게 묻는 것을 그냥 두면
        끝나지 않습니다.
        """
        deep = self._run(hop_count=3)
        row = self._ask(self._answering(), run=deep)
        self.assertIsNone(row)
        self.assertEqual(AgentLookup.objects.count(), 0)

    def test_asking_yourself_is_not_a_lookup(self):
        """자기 기록은 그냥 검색하면 됩니다."""
        self.assertIsNone(lookup.ask_peer(
            asker=self.me, target=self.me, topic="t", reason="r",
            question="q", project_id=self.project.id))


class LookupArrowTest(Base):

    def test_an_ai_lookup_arrow_is_drawn(self):
        self._ask(self._answering())
        e = FlowEdge.objects.get(content_type=FlowContentType.AI_LOOKUP)
        self.assertEqual(e.category, FlowCategory.WORK)
        self.assertIsNone(e.meeting_id, "작업 플로우 엣지에는 회의가 없습니다")
        self.assertEqual(e.project_id, self.project.id)

    def test_the_arrow_connects_agent_nodes_not_people(self):
        """
        물어본 것은 사람이 아니라 대리인입니다. 화면에서도 Bordo 프로필
        사이에 그려집니다.
        """
        self._ask(self._answering())
        e = FlowEdge.objects.get(content_type=FlowContentType.AI_LOOKUP)
        self.assertEqual(e.from_node["kind"], "AGENT")
        self.assertEqual(e.to_nodes[0]["kind"], "AGENT")
        self.assertEqual(e.from_node["name"], "서재민의 Bordo")

    def test_the_arrow_links_back_to_the_detail(self):
        """화살표를 눌러 4단 상세로 가려면 연결이 있어야 합니다."""
        row = self._ask(self._answering())
        row.refresh_from_db()
        self.assertIsNotNone(row.edge_id)

    def test_a_failed_arrow_does_not_lose_the_record(self):
        """화살표 하나를 못 그렸다고 조회 기록이 사라지면 안 됩니다."""
        with patch("apps.meetings.models.FlowEdge.objects.create",
                   side_effect=RuntimeError("디스크 꽉 참")):
            row = self._ask(self._answering())
        self.assertIsNotNone(row)
        self.assertEqual(AgentLookup.objects.count(), 1)


class LookupDetailApiTest(Base):

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.me)

    def _row(self, answer="기본 레이아웃 적용을 완료했어요."):
        return AgentLookup.objects.create(
            project=self.project, asker=self.me, target=self.mate,
            topic="모바일 반응형 작업 진행 현황", reason="필요했습니다.",
            question="어디까지 진행됐나요?", answer=answer,
            occurred_at=timezone.now())

    def test_four_parts_are_returned(self):
        r = self.api.get(f"/api/v1/agent-lookups/{self._row().id}")
        self.assertEqual(r.status_code, 200, r.content[:200])
        body = r.json()
        for key in ("topic", "reason", "question", "answer"):
            self.assertTrue(body[key], f"{key} 가 비었습니다")
        self.assertTrue(body["answered"])

    def test_names_are_shown_as_bordo(self):
        """`AI 대리인` 은 화면 어디에도 없는 낱말입니다."""
        body = self.api.get(f"/api/v1/agent-lookups/{self._row().id}").json()
        self.assertEqual(body["asker"]["name"], "서재민의 Bordo")
        self.assertEqual(body["target"]["name"], "강다은의 Bordo")

    def test_a_deferred_lookup_says_so(self):
        body = self.api.get(f"/api/v1/agent-lookups/{self._row(answer='').id}").json()
        self.assertFalse(body["answered"])
        self.assertEqual(body["answer"], "")

    def test_anyone_in_the_project_can_read_it(self):
        """작업 플로우는 팀 관점 화면이라 남의 조회 화살표도 눌러 볼 수 있어야 합니다."""
        api = APIClient()
        api.force_authenticate(user=self.mate)
        self.assertEqual(
            api.get(f"/api/v1/agent-lookups/{self._row().id}").status_code, 200)

    def test_outsider_cannot_read(self):
        stranger = User.objects.create_user(email="x@bordo.dev", password="x" * 10,
                                            name="남")
        api = APIClient()
        api.force_authenticate(user=stranger)
        self.assertIn(
            api.get(f"/api/v1/agent-lookups/{self._row().id}").status_code, (403, 404))


class AskPeerSkillTest(Base):

    def _ctx(self):
        from apps.agent.services.skills import SkillContext
        return SkillContext(principal_id=str(self.me.id),
                            actor_id=str(self.me.id),
                            project_id=str(self.project.id))

    def _skill(self):
        from apps.agent.services.skills.ask_peer import AskPeerAgentSkill
        return AskPeerAgentSkill()

    def test_it_asks_by_name(self):
        with patch("apps.agent.services.react.default_client", self._answering()):
            r = self._skill().run(
                {"target_name": "강다은", "question": "어디까지 진행됐나요?"},
                self._ctx())
        self.assertTrue(r.ok)
        self.assertTrue(r.data["answered"])

    def test_someone_outside_the_project_is_not_reachable(self):
        """
        이름만으로 아무나 찾으면 남의 팀 기록을 끌어올 수 있습니다.
        """
        User.objects.create_user(email="o@bordo.dev", password="x" * 10, name="외부인")
        r = self._skill().run({"target_name": "외부인", "question": "q"}, self._ctx())
        self.assertFalse(r.ok)

    def test_asking_myself_is_rejected(self):
        r = self._skill().run({"target_name": "서재민", "question": "q"}, self._ctx())
        self.assertFalse(r.ok)

    def test_the_skill_is_registered(self):
        """프롬프트에는 있는데 실행이 안 되는 상황을 막습니다."""
        from apps.agent.services.skills import registry
        self.assertIn("ask_peer_agent", [s.name for s in registry.list()])


class EndlessLookupTest(Base):
    """
    A 의 대리인이 B 에게 묻고, B 가 다시 A 에게 묻고… 를 **실제로** 돌립니다.

    앞의 `test_hop_limit_stops_endless_lookups` 는 `hop_count=3` 을 손으로 만들어
    가드 한 줄만 봅니다. 그러면 `ctx.run_id → hop_count → react.run` 으로 이어지는
    배선이 끊겨도 통과합니다 — 정작 회귀가 숨을 자리가 그 배선입니다.
    """

    class AlwaysAsksBack:
        """
        누가 물어보든 상대에게 되묻는 대리인.

        `names` 를 번갈아 쓰므로 A→B→A→B… 가 됩니다. 상한이 없으면 끝나지
        않으므로 호출 수를 세어 **테스트 자체가 멈추도록** 합니다.
        """

        def __init__(self, names, limit=40):
            self.names = names
            self.calls = 0
            self.limit = limit

        def chat(self, messages, tools=None, system=""):
            self.calls += 1
            if self.calls > self.limit:
                raise AssertionError("무한 조회가 끊기지 않았습니다")
            # 홀수 번째는 의도 분류, 짝수 번째는 되묻기.
            if self.calls % 2 == 1:
                return LLMResponse(text="STATUS")
            return LLMResponse(tool_calls=[ToolCall(
                f"c{self.calls}", "ask_peer_agent",
                {"target_name": self.names[self.calls % len(self.names)],
                 "question": "그쪽은 어디까지 됐나요?"})])

    def test_a_real_back_and_forth_chain_terminates(self):
        llm = self.AlwaysAsksBack(["서재민", "강다은"])

        with patch("apps.agent.services.react.default_client", llm):
            lookup.ask_peer(
                asker=self.me, target=self.mate,
                topic="서로 되묻기", reason="r",
                question="어디까지 됐나요?",
                project_id=self.project.id, run=self._run())

        # 끊기지 않으면 위 limit 에서 AssertionError 로 죽습니다.
        # 여기까지 왔다면 상한이 실제로 걸렸다는 뜻입니다.
        from django.conf import settings as dj_settings

        max_hops = dj_settings.BORDO.get("MAX_HOPS", 3)
        deepest = AgentRun.objects.order_by("-hop_count").first()
        self.assertLessEqual(deepest.hop_count, max_hops,
                             "상한을 넘은 실행이 있습니다")

    def test_the_chain_records_every_hop_it_actually_made(self):
        """
        중간에 끊겼더라도 **거기까지 물어본 것은 남아야 합니다.**
        안 남기면 사용자는 대리인이 알아보지도 않은 줄 압니다.
        """
        llm = self.AlwaysAsksBack(["서재민", "강다은"])
        with patch("apps.agent.services.react.default_client", llm):
            lookup.ask_peer(
                asker=self.me, target=self.mate, topic="서로 되묻기",
                reason="r", question="어디까지 됐나요?",
                project_id=self.project.id, run=self._run())

        self.assertGreaterEqual(AgentLookup.objects.count(), 1)
        # 화살표도 조회 수만큼 그려집니다.
        self.assertEqual(
            FlowEdge.objects.filter(content_type=FlowContentType.AI_LOOKUP).count(),
            AgentLookup.objects.count())
