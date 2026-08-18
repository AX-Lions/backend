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
