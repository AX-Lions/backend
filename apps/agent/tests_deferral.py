"""
유보 기록 테스트.

유보가 화면에 남지 않으면 **대리인이 침묵한 사실 자체가 사라집니다.**
사용자는 회의에서 자기 이름이 불린 줄도 모릅니다.
"""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, AgentSettings, PendingQuestion
from apps.agent.services import deferral, react
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.meetings.models import Meeting
from apps.orgs.models import Project, Team
from apps.states.models import ThoughtItem, WorkItem


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)

    def chat(self, messages, tools=None, system=""):
        return self._q.pop(0) if self._q else LLMResponse(text="끝")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.asker = User.objects.create_user(email="q@bordo.dev", password="x" * 10,
                                             name="임수연")
        team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                             created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AgentSettings.objects.create(user=cls.me)

    def _run_obj(self, meeting=True):
        return AgentRun.objects.create(
            user=self.me, meeting=self.meeting if meeting else None,
            status=AgentRun.Status.COMPLETED)


class RecordTest(Base):

    def test_creates_question(self):
        q = deferral.record(run=self._run_obj(), question="DB 스키마 어디까지 됐어요?",
                            reason_message="관련 기록을 찾지 못했습니다.",
                            asker=self.asker)
        self.assertIsNotNone(q)
        self.assertEqual(q.target_user, self.me)
        self.assertEqual(q.asker_name, "임수연")

    def test_body_holds_question_and_reason(self):
        """사유만 있으면 무슨 질문이었는지 되짚을 수 없습니다."""
        q = deferral.record(run=self._run_obj(), question="스키마 언제 끝나요?",
                            reason_message="본인이 논의가 필요하다고 표시했습니다.",
                            evidence=[{"title_snapshot": "DB 스키마 구조"}],
                            asker=self.asker)
        self.assertIn("스키마 언제 끝나요?", q.body)
        self.assertIn("논의가 필요", q.body)
        self.assertIn("DB 스키마 구조", q.body)

    def test_title_is_the_question_itself(self):
        """요약을 LLM 에게 맡기면 질문이 다르게 적혀 본인이 헷갈립니다."""
        q = deferral.record(run=self._run_obj(), question="스키마 진행 상황",
                            reason_message="x")
        self.assertEqual(q.title, "스키마 진행 상황")

    def test_long_title_is_cut(self):
        q = deferral.record(run=self._run_obj(), question="가" * 300,
                            reason_message="x")
        self.assertLessEqual(len(q.title), 200)
        self.assertTrue(q.title.endswith("…"))

    def test_same_run_records_once(self):
        """재시도에서 목록이 불어나면 같은 질문을 여러 번 답해야 합니다."""
        run = self._run_obj()
        a = deferral.record(run=run, question="q", reason_message="r")
        b = deferral.record(run=run, question="q", reason_message="r")
        self.assertEqual(a.id, b.id)
        self.assertEqual(PendingQuestion.objects.count(), 1)

    def test_no_meeting_records_nothing(self):
        """본인과의 대화에서 나온 유보는 되물을 상대가 자기 자신입니다."""
        q = deferral.record(run=self._run_obj(meeting=False), question="q",
                            reason_message="r")
        self.assertIsNone(q)
        self.assertEqual(PendingQuestion.objects.count(), 0)


class ThroughLoopTest(Base):

    def _run(self, llm, question="DB 스키마 어디까지 됐어요?"):
        return react.run(principal=self.me, question=question, meeting=self.meeting,
                         actor_id=self.asker.id, asker=self.asker, client=llm)

    def test_defer_leaves_a_question(self):
        out = self._run(FakeLLM(LLMResponse(text="STATUS"),
                                LLMResponse(text="아마 곧 끝날 겁니다")))
        self.assertFalse(out.answered)
        self.assertIsNotNone(out.pending_question)
        self.assertEqual(PendingQuestion.objects.count(), 1)

    def test_answer_leaves_nothing(self):
        WorkItem.objects.create(project=self.project, owner=self.me,
                                title="team_members 마이그레이션",
                                summary="진행 중", status="IN_PROGRESS")
        out = self._run(FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="진행 중입니다."),
        ))
        self.assertTrue(out.answered)
        self.assertIsNone(out.pending_question)
        self.assertEqual(PendingQuestion.objects.count(), 0)

    def test_policy_block_also_leaves_a_question(self):
        """정책으로 막힌 것도 본인은 알아야 합니다."""
        AgentSettings.objects.filter(user=self.me).update(
            disclose_work_plan_thought=False)
        out = self._run(FakeLLM(LLMResponse(text="STATUS")))
        self.assertIsNotNone(out.pending_question)
        self.assertIn("설정", out.pending_question.body)

    def test_question_is_visible_to_the_principal(self):
        """이 목록이 곧 '내가 없는 동안 무슨 일이 있었지' 의 답입니다."""
        ThoughtItem.objects.create(project=self.project, owner=self.me,
                                   topic="스키마 구조", content="고민 중",
                                   requires_discussion=True)
        self._run(FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "스키마 구조"})]),
            LLMResponse(text="이렇게 하기로 했습니다"),
        ))
        mine = PendingQuestion.objects.filter(target_user=self.me, answered_at=None)
        self.assertEqual(mine.count(), 1)
        self.assertEqual(mine.first().asker, self.asker)
