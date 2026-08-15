"""
브리핑 테스트.

"내가 없는 동안 무슨 일이 있었지?" 의 답입니다.
비어 있거나 없던 내용이 섞이면 이 서비스의 존재 이유가 사라집니다.
"""
import json

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, AgentSettings
from apps.agent.services import briefing
from apps.agent.services.llm import LLMResponse
from apps.meetings.models import (AiBriefing, Attendance, Meeting,
                                  MeetingParticipant, MeetingSummary, Utterance)
from apps.orgs.models import Project, Team


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)
        self.seen = []

    def chat(self, messages, tools=None, system=""):
        self.seen.append(messages[-1]["content"] if messages else "")
        return self._q.pop(0) if self._q else LLMResponse(text="요약")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.other = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                             name="임수연")
        team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                             created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AgentSettings.objects.create(user=cls.me, active_version=3)
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.me, user_name="서재민",
            attendance=Attendance.ABSENT, delegated=True)

    def _run(self, *, answered, reason="", result=""):
        return AgentRun.objects.create(
            user=self.me, meeting=self.meeting,
            status=AgentRun.Status.COMPLETED, result=result,
            steps=[{"kind": "verdict", "answer": answered, "reason": reason}],
            evidence=[{"title_snapshot": "team_members 마이그레이션"}])


class BriefingTest(Base):

    def test_splits_used_and_deferred(self):
        self._run(answered=True, result="진행 중입니다")
        self._run(answered=False, reason="NEEDS_DISCUSSION",
                  result="본인 확인이 필요합니다")

        b = briefing.build_for_user(self.meeting, self.me, FakeLLM())
        self.assertEqual(len(b.used_answers), 1)
        self.assertEqual(len(b.deferred_answers), 1)
        self.assertEqual(b.deferred_answers[0]["reason"], "NEEDS_DISCUSSION")

    def test_carries_evidence_titles(self):
        """무엇을 보고 답했는지 브리핑에서 바로 보여야 합니다."""
        self._run(answered=True, result="x")
        b = briefing.build_for_user(self.meeting, self.me, FakeLLM())
        self.assertIn("team_members 마이그레이션", b.used_answers[0]["evidence"])

    def test_records_settings_version(self):
        """그때 어떤 정책으로 답했는지 되짚을 수 있어야 합니다."""
        self._run(answered=True, result="x")
        b = briefing.build_for_user(self.meeting, self.me, FakeLLM())
        self.assertEqual(b.settings_version, 3)

    def test_narrative_uses_only_the_list(self):
        """모델에게 넘기는 것이 목록뿐인지 봅니다."""
        self._run(answered=True, result="진행 중입니다")
        llm = FakeLLM(LLMResponse(text="대리인이 진행 상황을 전했습니다."))
        briefing.build_for_user(self.meeting, self.me, llm)
        self.assertIn("진행 중입니다", llm.seen[0])

    def test_survives_llm_failure(self):
        """문장이 비어도 목록은 남아야 합니다."""
        self._run(answered=True, result="x")
        b = briefing.build_for_user(self.meeting, self.me,
                                    FakeLLM(LLMResponse(error="down")))
        self.assertEqual(b.narrative, "")
        self.assertEqual(len(b.used_answers), 1)

    def test_nothing_at_all_means_no_briefing(self):
        """
        정말 아무것도 없으면 만들지 않습니다.

        빈 사이드바가 뜨면 사용자는 브리핑 생성이 실패한 줄 압니다.
        """
        self.assertIsNone(briefing.build_for_user(self.meeting, self.me, FakeLLM()))

    def test_rebuild_overwrites(self):
        """회의가 다시 종료 처리되면 같은 브리핑이 두 번 뜹니다."""
        self._run(answered=True, result="x")
        briefing.build_for_user(self.meeting, self.me, FakeLLM())
        briefing.build_for_user(self.meeting, self.me, FakeLLM())
        self.assertEqual(AiBriefing.objects.count(), 1)


class SummaryTest(Base):

    def _utterances(self):
        for who, what in [("임수연", "인덱스가 빠진 것 같아요"),
                          ("최비성", "일정을 하루 미룹시다")]:
            Utterance.objects.create(meeting=self.meeting, participant=self.other,
                                     participant_name=who, body=what)

    def test_fills_three_folders(self):
        self._utterances()
        payload = json.dumps({"discovered_issues": ["인덱스 누락"],
                              "changes": ["일정 하루 연기"],
                              "next_plans": ["인덱스 재측정"],
                              "one_line": "스키마 논의"}, ensure_ascii=False)
        s = briefing.build_summary(self.meeting, FakeLLM(LLMResponse(text=payload)))
        self.assertEqual(s.discovered_issues, ["인덱스 누락"])
        self.assertEqual(s.changes, ["일정 하루 연기"])
        self.assertEqual(s.next_plans, ["인덱스 재측정"])

    def test_handles_fenced_json(self):
        """모델이 ```json 으로 감싸는 일이 잦습니다."""
        self._utterances()
        payload = '```json\n{"discovered_issues": ["x"], "changes": [], ' \
                  '"next_plans": [], "one_line": "y"}\n```'
        s = briefing.build_summary(self.meeting, FakeLLM(LLMResponse(text=payload)))
        self.assertEqual(s.discovered_issues, ["x"])

    def test_broken_json_leaves_summary_alone(self):
        """깨진 응답으로 기존 요약을 덮어쓰면 있던 내용이 사라집니다."""
        self._utterances()
        self.assertIsNone(
            briefing.build_summary(self.meeting, FakeLLM(LLMResponse(text="음..."))))
        self.assertEqual(MeetingSummary.objects.count(), 0)

    def test_no_utterance_means_no_summary(self):
        self.assertIsNone(briefing.build_summary(self.meeting, FakeLLM()))


class BuildAllTest(Base):

    def test_builds_for_delegated_only(self):
        MeetingParticipant.objects.create(
            meeting=self.meeting, user=self.other, user_name="임수연",
            attendance=Attendance.PRESENT, delegated=False)
        self._run(answered=True, result="x")
        AgentRun.objects.create(user=self.other, meeting=self.meeting,
                                status=AgentRun.Status.COMPLETED, result="y",
                                steps=[{"kind": "verdict", "answer": True}])

        made = briefing.build_all(self.meeting, FakeLLM(
            LLMResponse(text='{"discovered_issues":[],"changes":[],'
                             '"next_plans":[],"one_line":"x"}'),
            LLMResponse(text="문장")))
        self.assertEqual(made, 1)
        self.assertEqual(AiBriefing.objects.count(), 1)

    def test_one_failure_does_not_stop_the_rest(self):
        """브리핑 하나 때문에 회의 전체 결과가 사라지면 안 됩니다."""
        from unittest.mock import patch
        self._run(answered=True, result="x")
        with patch.object(briefing, "build_for_user", side_effect=RuntimeError("펑")):
            made = briefing.build_all(self.meeting, FakeLLM())
        self.assertEqual(made, 0)      # 예외가 밖으로 나오지 않습니다
