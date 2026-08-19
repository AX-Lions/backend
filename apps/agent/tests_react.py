"""
ReAct 루프 테스트.

LLM 은 가짜로 대체합니다. 네트워크에 의존하면 CI 가 흔들리고 비용이 듭니다.
여기서 보는 것은 **경로가 갈리는 지점**입니다 — POLICY 에서 막히는가,
유보로 끝나는가, 실패가 유보로 둔갑하지 않는가.
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, AgentSettings
from apps.agent.services import react
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.meetings.models import Meeting
from apps.orgs.models import Project, Team
from apps.states.models import WorkItem


class FakeLLM:
    """정해진 응답을 순서대로 돌려줍니다."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None, system=""):
        self.calls += 1
        if not self._queue:
            return LLMResponse(text="더 없음")
        return self._queue.pop(0)


def intent(word):
    return LLMResponse(text=word)


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.asker = User.objects.create_user(email="q@bordo.dev", password="x" * 10,
                                             name="질문자")
        team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                             created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="프로젝트", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AgentSettings.objects.create(user=cls.me)

    def _work(self, **kw):
        base = dict(project=self.project, owner=self.me,
                    title="team_members 마이그레이션", summary="스키마 확인 대기",
                    status="IN_PROGRESS")
        base.update(kw)
        return WorkItem.objects.create(**base)

    def _run(self, llm, question="DB 스키마 어디까지 됐어?", **kw):
        return react.run(principal=self.me, question=question,
                         meeting=self.meeting, actor_id=self.asker.id,
                         client=llm, **kw)


class PolicyGateTest(Base):

    def test_blocked_intent_never_calls_generation(self):
        """막힌 뒤에 만들어 두면 새어 나갈 자리가 생깁니다."""
        AgentSettings.objects.filter(user=self.me).update(
            disclose_work=False, disclose_plan=False, disclose_thought=False)
        llm = FakeLLM(intent("STATUS"))
        out = self._run(llm)

        self.assertFalse(out.answered)
        self.assertEqual(out.reason, "POLICY_DISCLOSURE")
        self.assertEqual(llm.calls, 1)          # 의도 분류 한 번뿐
        self.assertEqual(out.run.status, AgentRun.Status.COMPLETED)

    def test_policy_step_is_recorded(self):
        AgentSettings.objects.filter(user=self.me).update(mention_feasibility=False)
        out = self._run(FakeLLM(intent("FEASIBILITY")))
        kinds = [s["kind"] for s in out.run.steps]
        self.assertIn("policy", kinds)


class AnswerTest(Base):

    def test_answers_with_direct_own_evidence(self):
        self._work()
        llm = FakeLLM(
            intent("STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="team_members 마이그레이션 진행 중입니다."),
        )
        out = self._run(llm)

        self.assertTrue(out.answered)
        self.assertIn("마이그레이션", out.text)
        self.assertEqual(out.run.status, AgentRun.Status.COMPLETED)
        self.assertTrue(out.run.evidence)

    def test_steps_record_the_skill_call(self):
        """무엇을 보고 답했는지 화면이 그려져야 합니다."""
        self._work()
        llm = FakeLLM(
            intent("STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="답변"),
        )
        out = self._run(llm)
        skill_steps = [s for s in out.run.steps if s["kind"] == "skill"]
        self.assertEqual(skill_steps[0]["name"], "search_records")
        self.assertGreater(skill_steps[0]["found"], 0)

    def test_settings_snapshot_is_frozen(self):
        """그때 어떤 정책으로 답했나를 재현할 수 있어야 합니다."""
        self._work()
        out = self._run(FakeLLM(intent("STATUS"),
                                LLMResponse(text="답변")))
        self.assertIn("disclose_work_plan_thought", out.run.settings_snapshot)


class DeferTest(Base):

    def test_no_evidence_defers(self):
        llm = FakeLLM(intent("STATUS"), LLMResponse(text="아마 곧 끝날 겁니다"))
        out = self._run(llm)

        self.assertFalse(out.answered)
        self.assertEqual(out.reason, "NO_EVIDENCE")
        # 모델이 지어낸 문장이 그대로 나가면 안 됩니다.
        self.assertNotIn("아마", out.text)
        self.assertIn("본인 확인", out.text)

    def test_defer_is_completed_not_failed(self):
        """FAILED 로 두면 사용자는 대리인이 고장난 줄 압니다."""
        out = self._run(FakeLLM(intent("STATUS"), LLMResponse(text="x")))
        self.assertEqual(out.run.status, AgentRun.Status.COMPLETED)

    def test_someone_elses_record_defers(self):
        self._work(owner=self.asker)
        llm = FakeLLM(
            intent("STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="남의 기록으로 답함"),
        )
        out = self._run(llm)
        self.assertEqual(out.reason, "NOT_MY_RECORD")

    def test_requires_discussion_defers(self):
        from apps.states.models import ThoughtItem
        ThoughtItem.objects.create(project=self.project, owner=self.me,
                                   topic="스키마 구조", content="고민 중",
                                   requires_discussion=True)
        llm = FakeLLM(
            intent("STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "스키마 구조"})]),
            LLMResponse(text="이렇게 하기로 했습니다"),
        )
        out = self._run(llm)
        self.assertEqual(out.reason, "NEEDS_DISCUSSION")

    def test_defer_message_lists_evidence(self):
        """왜 그렇게 판단했는지 근거를 함께 보여줍니다."""
        self._work(owner=self.asker)
        llm = FakeLLM(
            intent("STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="x"),
        )
        out = self._run(llm)
        self.assertIn("team_members", out.text)


class LimitTest(Base):

    def test_max_steps_does_not_force_an_answer(self):
        """상한을 넘겼다고 답을 만들게 하면 그 순간 지어내기가 됩니다."""
        calls = [intent("STATUS")]
        calls += [LLMResponse(tool_calls=[ToolCall(f"c{i}", "think",
                                                   {"reasoning": "계속 생각"})])
                  for i in range(react.MAX_STEPS)]
        out = self._run(FakeLLM(*calls))

        self.assertFalse(out.answered)
        self.assertIn("max_steps", [s["kind"] for s in out.run.steps])

    def test_hop_limit_stops_before_starting(self):
        """대리인끼리 되묻기 시작하면 끝나지 않습니다."""
        out = self._run(FakeLLM(intent("STATUS")), hop_count=3)
        self.assertFalse(out.answered)
        self.assertEqual(out.reason, "MAX_HOPS")


class FailureTest(Base):

    def test_llm_error_is_failed_not_deferred(self):
        """기술적 실패를 유보로 둔갑시키면 장애를 눈치채지 못합니다."""
        llm = FakeLLM(intent("STATUS"),
                      LLMResponse(error="503 overloaded", error_kind="retryable"))
        out = self._run(llm)

        self.assertFalse(out.answered)
        self.assertEqual(out.run.status, AgentRun.Status.FAILED)
        self.assertTrue(out.error)

    def test_unexpected_exception_is_failed(self):
        with patch.object(react, "_classify", side_effect=RuntimeError("펑")):
            out = self._run(FakeLLM())
        self.assertEqual(out.run.status, AgentRun.Status.FAILED)

    def test_steps_survive_failure(self):
        """루프 중간에 죽어도 그때까지 모은 것은 남아야 추적이 됩니다."""
        llm = FakeLLM(intent("STATUS"),
                      LLMResponse(error="boom", error_kind="fatal"))
        out = self._run(llm)
        self.assertTrue(out.run.steps)
