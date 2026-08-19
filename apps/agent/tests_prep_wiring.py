"""
준비 화면에 채워 둔 것이 **대리인 실행에 실제로 반영되는가**.

저장은 되는데 아무도 안 읽는 것이 이 저장소에서 반복된 실수라(쓰기 스킬이
카탈로그에 없던 것, AgentPrompt 를 아무도 안 읽던 것) 그 자리를 눌러 둡니다.
"""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentPrompt, AgentSettings
from apps.agent.services import prompts, react
from apps.meetings.models import (DebatePoint, DebateStance, Meeting,
                                  MeetingParticipant, MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="해커톤", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="개발 방향 논의",
            status=MeetingStatus.SCHEDULED, scheduled_at=timezone.now(),
            created_by=cls.me)
        cls.part = MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.me, user_name=cls.me.name, delegated=True)


class SnapshotOverrideTest(Base):

    def test_meeting_override_wins_over_standing(self):
        AgentSettings.objects.create(user=self.me, disclose_work=True,
                                     mention_feasibility=True)
        self.part.settings_override = {"disclose_work": False}
        self.part.save()

        snap = react._snapshot_of(self.me, self.part)
        self.assertFalse(snap["disclose_work"])
        self.assertTrue(snap["mention_feasibility"])     # 안 덮어쓴 것은 평소 값

    def test_derived_key_is_recomputed(self):
        """셋을 모두 끈 회의에서 옛 키가 True 로 남으면 STATUS 관문이 통과합니다."""
        AgentSettings.objects.create(user=self.me)
        self.part.settings_override = {"disclose_work": False, "disclose_plan": False,
                                       "disclose_thought": False}
        self.part.save()

        snap = react._snapshot_of(self.me, self.part)
        self.assertFalse(snap["disclose_work_plan_thought"])

    def test_no_participant_means_standing_only(self):
        AgentSettings.objects.create(user=self.me, disclose_work=False)
        snap = react._snapshot_of(self.me, None)
        self.assertFalse(snap["disclose_work"])


class PromptOverrideTest(Base):

    def test_meeting_prompts_replace_standing(self):
        AgentPrompt.objects.create(user=self.me, body="평소 지시")
        self.part.prompt_override = ["이번 회의 전용 지시"]
        self.part.save()
        self.assertEqual(react._prompts_for(self.me, self.part),
                         ["이번 회의 전용 지시"])

    def test_empty_list_means_none(self):
        """전부 지운 사람에게 평소 지시를 그대로 쓰면 지우려던 것이 나갑니다."""
        AgentPrompt.objects.create(user=self.me, body="평소 지시")
        self.part.prompt_override = []
        self.part.save()
        self.assertEqual(react._prompts_for(self.me, self.part), [])

    def test_null_keeps_standing(self):
        AgentPrompt.objects.create(user=self.me, body="평소 지시")
        self.assertEqual(react._prompts_for(self.me, self.part), ["평소 지시"])


class StanceInPromptTest(Base):

    def point(self, order=1, title="개발 범위를 축소할 것인가?"):
        return DebatePoint.objects.create(
            meeting=self.meeting, source_key=f"k{order}", order=order, title=title,
            options=[{"key": "A", "title": "핵심 기능만 구현"},
                     {"key": "B", "title": "기존 범위 유지"}])

    def test_stances_are_collected_in_order(self):
        p2 = self.point(order=2, title="QA 일정을 연기할 것인가?")
        p1 = self.point(order=1)
        DebateStance.objects.create(point=p2, user=self.me, body="연기해야 해요")
        DebateStance.objects.create(point=p1, user=self.me, option_key="A",
                                    body="핵심만 하죠")

        rows = react._stances_for(self.meeting.id, self.me)
        self.assertEqual([r["title"] for r in rows],
                         ["개발 범위를 축소할 것인가?", "QA 일정을 연기할 것인가?"])
        self.assertEqual(rows[0]["option"], "핵심 기능만 구현")
        self.assertEqual(rows[1]["option"], "")

    def test_no_meeting_means_no_stances(self):
        self.assertEqual(react._stances_for(None, self.me), [])

    def test_prompt_carries_the_stance(self):
        system = prompts.build_system(
            "서재민",
            stances=[{"title": "개발 범위를 축소할 것인가?", "option": "핵심 기능만 구현",
                      "body": "남은 기간을 보면 축소가 맞아요"}])
        self.assertIn("미리 정해 둔 입장", system)
        self.assertIn("남은 기간을 보면 축소가 맞아요", system)
        self.assertIn("「핵심 기능만 구현」", system)

    def test_stance_comes_after_every_other_instruction(self):
        """쟁점 하나에 대한 본인의 답이라 가장 구체적입니다 — 어긋나면 이쪽이 이겨야 합니다."""
        system = prompts.build_system(
            "서재민", delegate_prompt="일정 얘기는 하지 마",
            standing_prompts=["평소 지시"],
            stances=[{"title": "쟁점", "body": "이렇게 갑시다"}])
        self.assertLess(system.index("평소 정해 둔 것"), system.index("미리 남긴 지시"))
        self.assertLess(system.index("미리 남긴 지시"), system.index("미리 정해 둔 입장"))

    def test_empty_body_is_not_carried(self):
        """제목만 남은 줄이 들어가면 모델이 없는 입장을 지어냅니다."""
        system = prompts.build_system("서재민",
                                      stances=[{"title": "쟁점", "body": "   "}])
        self.assertNotIn("미리 정해 둔 입장", system)


class WriteGateTest(Base):
    """
    통째로 나가는 자리는 **하나라도 꺼져 있으면** 막습니다.

    `send_message` 와 `ask_peer_agent` 는 승인 단계가 없어 되돌릴 자리가 없는데,
    검색 스킬이 돌려준 원문이 이미 모델의 messages 에 들어가 있어 어느 종류가
    섞였는지 코드가 가릴 수 없습니다.
    """

    ALL_ON = {"disclose_work": True, "disclose_plan": True, "disclose_thought": True}

    def test_one_off_closes_the_wholesale_paths(self):
        snap = {**self.ALL_ON, "disclose_thought": False}
        for skill in ("send_message", "ask_peer_agent"):
            self.assertTrue(react._may_write(skill, snap), skill)

    def test_all_on_opens_them(self):
        for skill in ("send_message", "ask_peer_agent"):
            self.assertEqual(react._may_write(skill, self.ALL_ON), "", skill)

    def test_legacy_snapshot_still_decides(self):
        from apps.agent.services import policy
        self.assertFalse(policy.fully_disclosed({"disclose_work_plan_thought": False}))
        self.assertTrue(policy.fully_disclosed({"disclose_work_plan_thought": True}))

    def test_per_item_disclosure_is_still_granular(self):
        """근거 하나를 보는 판정은 낱개 그대로입니다 — 여기까지 막으면 너무 셉니다."""
        from apps.agent.services import policy
        snap = {**self.ALL_ON, "disclose_thought": False}
        self.assertTrue(policy.can_disclose({"source_type": "work"}, snap))
        self.assertFalse(policy.can_disclose({"source_type": "thought"}, snap))


class PeerPathTest(Base):
    """대리인끼리 묻는 경로에서만 준비해 둔 것이 빠지면 그 경로가 우회로가 됩니다."""

    def test_stances_apply_when_only_scope_meeting_is_given(self):
        point = DebatePoint.objects.create(meeting=self.meeting, source_key="k1",
                                           order=1, title="쟁점?")
        DebateStance.objects.create(point=point, user=self.me, body="내 입장입니다")
        rows = react._stances_for(self.meeting.id, self.me)
        self.assertEqual(len(rows), 1)

    def test_meeting_note_is_picked_up_from_the_participant_row(self):
        self.part.delegate_prompt = "이번엔 일정 얘기 하지 마"
        self.part.save()
        p = react._participant_of(self.meeting.id, self.me)
        self.assertEqual(p.delegate_prompt, "이번엔 일정 얘기 하지 마")


class StanceReachesTheMeetingTest(Base):
    """
    **준비해 둔 입장이 실제로 회의에서 나오는가.**

    프롬프트에 실리는 것만 확인하면 부족합니다. 루프가 끝나면 반드시 judge 를
    지나는데, 프롬프트에 답이 적혀 있으면 모델은 도구를 한 번도 안 부르고 바로
    답합니다 — 그러면 근거가 0건이라 R1(근거없음)에 걸려 유보가 나갑니다.
    입장을 적어 두는 상황이 대부분 기록이 없는 경우라, 바로 그때 입을 다뭅니다.
    """

    def _stance(self, body="남은 기간을 보면 축소가 맞습니다"):
        from apps.agent.models import AgentSettings
        AgentSettings.objects.get_or_create(user=self.me)
        point = DebatePoint.objects.create(
            meeting=self.meeting, source_key="k1", order=1,
            title="개발 범위를 축소할 것인가?",
            options=[{"key": "A", "title": "핵심 기능만 구현"}])
        return DebateStance.objects.create(point=point, user=self.me,
                                           option_key="A", body=body)

    def _run(self, text="남은 기간을 보면 축소가 맞습니다.", **kw):
        from apps.agent.services.llm import LLMResponse

        class Fake:
            def __init__(self):
                self.system = ""

            def chat(self, messages, tools=None, system=""):
                self.system = system
                return LLMResponse(text=text)

        fake = Fake()
        return react.run(principal=self.me, question="범위 축소 어떻게 생각하세요?",
                         client=fake, **kw), fake

    def test_agent_answers_instead_of_deferring(self):
        self._stance()
        out, fake = self._run(meeting=self.meeting)
        self.assertTrue(out.answered,
                        f"준비해 둔 입장이 있는데 유보됐습니다: {out.reason}")
        self.assertIn("축소", out.text)
        self.assertIn("미리 정해 둔 입장", fake.system)

    def test_stance_is_recorded_as_evidence(self):
        """무엇을 보고 답했는지가 남아야 나중에 되짚을 수 있습니다."""
        self._stance()
        out, _ = self._run(meeting=self.meeting)
        kinds = [e.get("source_type") for e in (out.run.evidence or [])]
        self.assertIn("stance", kinds)

    def test_without_a_stance_it_still_defers(self):
        """근거 없이 답하는 경로를 새로 만든 것이 아닙니다."""
        from apps.agent.models import AgentSettings
        AgentSettings.objects.get_or_create(user=self.me)
        out, _ = self._run(meeting=self.meeting)
        self.assertFalse(out.answered)
        self.assertEqual(out.reason, "NO_EVIDENCE")

    def test_peer_path_also_answers(self):
        """대리인끼리 묻는 경로만 유보되면 그 경로가 우회로가 됩니다."""
        self._stance()
        out, _ = self._run(meeting=None, scope_meeting_id=self.meeting.id)
        self.assertTrue(out.answered, out.reason)

    def test_empty_stance_body_is_not_evidence(self):
        from apps.agent.models import AgentSettings
        AgentSettings.objects.get_or_create(user=self.me)
        point = DebatePoint.objects.create(meeting=self.meeting, source_key="k1",
                                           order=1, title="쟁점?")
        DebateStance.objects.create(point=point, user=self.me, body="   ")
        out, _ = self._run(meeting=self.meeting)
        self.assertFalse(out.answered)
