"""
진입점 테스트.

A 담당과의 계약 지점입니다. 여기가 조용히 멈추면 **회의 내내 대리인이 아무 말도
하지 않습니다.** 그래서 실패해도 다음 발언은 받아야 합니다.
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, AgentSettings, OutboxEvent, PendingQuestion
from apps.agent.services import targeting
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.agent.tasks import run_agent_for_utterance
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  Utterance)
from apps.orgs.models import Project, Team
from apps.states.models import WorkItem


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)

    def chat(self, messages, tools=None, system=""):
        return self._q.pop(0) if self._q else LLMResponse(text="끝")


class Picker:
    """
    대상 판정 전용 가짜 LLM.

    ReAct 쪽 큐와 섞으면 테스트가 읽히지 않습니다 — 앞에 `"1"` 을 한 칸씩 끼워 넣어야
    하고, 그 한 칸이 무엇인지는 코드를 따라가야 알 수 있습니다. 판정은 따로 둡니다.
    """

    def __init__(self, choice: str = "1"):
        self.choice = choice
        self.calls = 0

    def chat(self, messages, tools=None, system=""):
        self.calls += 1
        return LLMResponse(text=self.choice)


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.absent = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                              name="서재민")
        cls.speaker = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                               name="임수연")
        cls.present = User.objects.create_user(email="p@bordo.dev", password="x" * 10,
                                               name="최비성")
        team = Team.objects.create(name="팀", created_by=cls.absent)
        cls.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                             created_by=cls.absent)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.absent,
            discord_channel_id="ch-1")
        AgentSettings.objects.create(user=cls.absent)

        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.absent, user_name="서재민",
            attendance=Attendance.ABSENT, delegated=True,
            delegate_prompt="DB 질문 나오면 진행 상황 전달해줘")
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.speaker, user_name="임수연",
            attendance=Attendance.PRESENT)
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.present, user_name="최비성",
            attendance=Attendance.PRESENT, delegated=True)

    def _utterance(self, body="DB 스키마 어디까지 됐어요?"):
        return Utterance.objects.create(
            meeting=self.meeting, participant=self.speaker,
            participant_name="임수연", body=body)


class TargetingTest(Base):

    def test_only_absent_delegated_are_candidates(self):
        """자리에 있는 사람의 대리인이 대신 말하면 이상합니다."""
        rows = targeting.candidates(self._utterance())
        self.assertEqual([p.user_id for p in rows], [self.absent.id])

    def test_speaker_is_never_a_candidate(self):
        MeetingParticipant.objects.filter(user=self.speaker).update(
            delegated=True, attendance=Attendance.ABSENT)
        rows = targeting.candidates(self._utterance())
        self.assertNotIn(self.speaker.id, [p.user_id for p in rows])

    def test_single_candidate_is_still_asked(self):
        """
        후보가 하나여도 물어봅니다.

        후보를 좁히는 것과 '이게 질문이긴 한가' 는 다른 판단인데 이 호출이 둘을
        겸합니다. 건너뛰면 질문 여부를 보는 자리가 사라집니다.
        """
        picker = Picker("1")
        picked = targeting.pick(self._utterance(), client=picker)
        self.assertEqual(picked.user_id, self.absent.id)
        self.assertEqual(picker.calls, 1, "후보가 하나라고 판정을 건너뛰었습니다")

    def test_single_candidate_non_question_wakes_nobody(self):
        """
        `아 넵 감사합니다` 에 대리인이 끼어들면 안 됩니다.

        회의 발언 대부분은 질문이 아닙니다. 여기서 못 거르면 모든 발언마다
        대리인이 깨어나 유보 문구를 회의에 뿌립니다.
        """
        self.assertIsNone(
            targeting.pick(self._utterance("아 넵 감사합니다"), client=Picker("0")))

    def test_model_picks_among_many(self):
        MeetingParticipant.objects.filter(user=self.present).update(
            attendance=Attendance.ABSENT)
        picked = targeting.pick(self._utterance(), client=FakeLLM(LLMResponse(text="2")))
        self.assertIsNotNone(picked)

    def test_model_can_choose_nobody(self):
        """회의 발언 대부분은 질문이 아닙니다."""
        MeetingParticipant.objects.filter(user=self.present).update(
            attendance=Attendance.ABSENT)
        self.assertIsNone(
            targeting.pick(self._utterance(), client=FakeLLM(LLMResponse(text="0"))))

    def test_llm_failure_picks_nobody(self):
        """잘못 부르면 엉뚱한 사람의 기록이 회의에 나갑니다."""
        MeetingParticipant.objects.filter(user=self.present).update(
            attendance=Attendance.ABSENT)
        self.assertIsNone(targeting.pick(
            self._utterance(), client=FakeLLM(LLMResponse(error="down"))))

    def test_no_delegated_participant(self):
        MeetingParticipant.objects.update(delegated=False)
        self.assertIsNone(targeting.pick(self._utterance()))


class EntrypointTest(Base):

    def _run(self, llm, body="DB 스키마 어디까지 됐어요?", picker=None):
        u = self._utterance(body)
        with patch("apps.agent.services.react.default_client", llm), \
             patch("apps.agent.services.targeting.default_client", picker or Picker()):
            run_agent_for_utterance(str(u.id))
        return u

    def test_non_question_wakes_nobody(self):
        """
        대상 판정에서 걸러지면 AgentRun 자체가 생기지 않아야 합니다.

        여기서 못 막으면 회의 내내 모든 발언마다 대리인이 깨어나 유보 문구를
        뿌리고, 유보 질문 목록이 `ㅋㅋㅋ` 으로 가득 찹니다.
        """
        self._run(FakeLLM(), body="아 넵 감사합니다", picker=Picker("0"))
        self.assertEqual(AgentRun.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)
        self.assertEqual(PendingQuestion.objects.count(), 0)

    def test_answer_goes_to_the_meeting(self):
        WorkItem.objects.create(project=self.project, owner=self.absent,
                                title="team_members 마이그레이션",
                                summary="진행 중", status="IN_PROGRESS")
        self._run(FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="team_members 마이그레이션 진행 중입니다."),
        ))
        e = OutboxEvent.objects.get()
        self.assertIn("마이그레이션", e.payload["body"])
        self.assertTrue(e.payload["is_agent"])

    def test_defer_also_goes_to_the_meeting(self):
        """
        대리인이 아무 말 없이 있으면 회의는 답을 기다리며 멈추거나,
        그 사람 몫을 빼고 결정해 버립니다.
        """
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(text="아마 곧 끝날 겁니다")))
        e = OutboxEvent.objects.get()
        self.assertIn("본인 확인", e.payload["body"])
        self.assertEqual(PendingQuestion.objects.count(), 1)

    def test_delegate_prompt_is_used(self):
        """회의 전에 본인이 남긴 지시가 실제로 전달돼야 합니다."""
        seen = []

        def spy(messages, tools=None, system=""):
            # 첫 호출은 의도 분류라 지시가 없습니다. 전부 모아서 봐야 합니다.
            seen.append(system)
            return LLMResponse(text="STATUS")

        llm = FakeLLM()
        llm.chat = spy
        self._run(llm)
        self.assertTrue(any("DB 질문" in s for s in seen),
                        "사전 지시가 프롬프트에 실리지 않았습니다")

    def test_meeting_scoped_sources_reach_the_run(self):
        """
        불참 팝업에서 고른 자료 범위가 실행 스냅샷까지 가야 합니다.

        저장만 하고 안 넘기면 사용자는 껐다고 믿는 기록이 그대로 회의에
        나갑니다. 스냅샷에 남아야 나중에 "그때 이 범위로 판정했다" 도 됩니다.
        """
        MeetingParticipant.objects.filter(user=self.absent).update(
            delegate_sources=["work"])
        self._run(FakeLLM(LLMResponse(text="STATUS")))
        run = AgentRun.objects.get()
        self.assertEqual(run.settings_snapshot.get("delegate_sources"), ["work"])

    def test_unset_sources_leave_the_snapshot_alone(self):
        """
        고른 적이 없으면(`None`) 제한이 없습니다. 빈 목록을 넣어 두면 불참
        등록을 안 한 회의에서 대리인이 아무 기록도 못 씁니다.
        """
        self._run(FakeLLM(LLMResponse(text="STATUS")))
        run = AgentRun.objects.get()
        self.assertNotIn("delegate_sources", run.settings_snapshot)

    def test_nobody_targeted_does_nothing(self):
        MeetingParticipant.objects.update(delegated=False)
        self._run(FakeLLM())
        self.assertEqual(AgentRun.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_missing_utterance_is_quiet(self):
        import uuid
        run_agent_for_utterance(str(uuid.uuid4()))
        self.assertEqual(AgentRun.objects.count(), 0)

    def test_llm_failure_says_nothing_in_the_meeting(self):
        """
        "지금 오류가 났습니다" 를 대리인이 떠들면 회의가 어지러워지고
        사람이 할 수 있는 일도 없습니다.
        """
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(error="503", error_kind="retryable")))
        self.assertEqual(OutboxEvent.objects.count(), 0)
        self.assertEqual(AgentRun.objects.get().status, AgentRun.Status.FAILED)

    def test_exception_does_not_propagate(self):
        """여기서 터지면 다음 발언도 처리되지 않습니다."""
        with patch("apps.agent.services.targeting.default_client", Picker()), \
             patch("apps.agent.services.react.run", side_effect=RuntimeError("펑")):
            u = self._utterance()
            run_agent_for_utterance(str(u.id))      # 예외가 밖으로 나오면 실패

    def test_speaks_once_per_run(self):
        u = self._utterance()
        llm = FakeLLM(LLMResponse(text="STATUS"), LLMResponse(text="x"))
        with patch("apps.agent.services.react.default_client", llm), \
             patch("apps.agent.services.targeting.default_client", Picker()):
            run_agent_for_utterance(str(u.id))
            run_agent_for_utterance(str(u.id))
        # 멱등은 **실행 단위**입니다. 두 번째 호출은 새 AgentRun 이라 발언도 새로
        # 나갑니다. 발언 자체를 영구 차단하면 실패 후 재시도가 불가능해집니다.
        self.assertEqual(OutboxEvent.objects.count(), 2)
        self.assertEqual(AgentRun.objects.count(), 2)
