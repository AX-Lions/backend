"""
대리인 이름과 평소 지시.

## 왜 프롬프트 문자열까지 보는가

`AgentPrompt` 는 CRUD 가 다 도는데 **대리인이 한 번도 읽지 않았습니다.**
사용자는 "말하면 안 되는 것" 을 적어 두고 대리인이 그것을 지킨다고 믿고
있었습니다. **저장되는데 안 지켜지는 설정은 없는 것보다 나쁩니다.**

저장만 확인하면 같은 일이 또 지나갑니다. 모델에게 실제로 넘어가는 system
프롬프트를 붙잡아 봅니다.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentPrompt, AgentSettings
from apps.agent.services import react
from apps.agent.services.flow import agent_display_name, agent_node
from apps.agent.services.llm import LLMResponse
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class Spy:
    def __init__(self, *responses):
        self._q = list(responses)
        self.systems = []

    def chat(self, messages, tools=None, system=""):
        self.systems.append(system)
        return self._q.pop(0) if self._q else LLMResponse(text="끝")

    @property
    def agent_system(self):
        return next((s for s in self.systems if "지켜야 할 것" in s), "")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="n1@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        AgentSettings.objects.create(user=cls.me)

    def _system(self):
        spy = Spy(LLMResponse(text="STATUS"), LLMResponse(text="답"))
        with patch("apps.agent.services.react.default_client", spy):
            react.run(principal=self.me, question="진행 상황",
                      project_id=self.project.id, client=spy)
        return spy.agent_system


class StandingPromptTest(Base):

    def test_saved_prompts_reach_the_model(self):
        """저장해 둔 지시가 프롬프트에 실려야 대리인이 지킵니다."""
        AgentPrompt.objects.create(
            user=self.me, body="개인적인 내용은 다른 사람에게 공유하지 마")

        self.assertIn("개인적인 내용은 다른 사람에게", self._system())

    def test_no_prompt_adds_no_section(self):
        """한 줄도 없으면 빈 제목만 남기지 않습니다."""
        self.assertNotIn("평소 정해 둔 것", self._system())

    def test_only_the_recent_ones_are_carried(self):
        """
        전부 실으면 규칙보다 길어져 모델이 앞쪽 규칙을 흘립니다.
        최근 것부터 다섯 개까지만 싣습니다.
        """
        for i in range(8):
            AgentPrompt.objects.create(user=self.me, body=f"지시{i}")

        system = self._system()
        self.assertEqual(sum(1 for i in range(8) if f"지시{i}" in system), 5)

    def test_meeting_instruction_comes_after(self):
        """
        회의별 지시가 평소 지시보다 **뒤**에 옵니다. 어긋날 때 뒤가 이깁니다 —
        그쪽이 더 최근이고 이 회의를 보고 적은 것입니다.
        """
        AgentPrompt.objects.create(user=self.me, body="평소것")
        spy = Spy(LLMResponse(text="STATUS"), LLMResponse(text="답"))
        with patch("apps.agent.services.react.default_client", spy):
            react.run(principal=self.me, question="q", project_id=self.project.id,
                      client=spy, delegate_prompt="이번회의것")

        s = spy.agent_system
        self.assertLess(s.index("평소것"), s.index("이번회의것"))


class AgentNameTest(Base):

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_the_default_is_the_owners_name(self):
        self.assertEqual(agent_display_name(self.me), "서재민의 Bordo")

    def test_a_chosen_name_wins(self):
        AgentSettings.objects.filter(user=self.me).update(agent_name="제로")

        self.assertEqual(agent_display_name(self.me), "제로")
        # 플로우 노드에도 같은 이름이 실려야 합니다. 노드가 옛 이름이면
        # 설정 화면과 회의 화면이 서로 다른 이름을 보여 줍니다.
        self.assertEqual(agent_node(self.me)["name"], "제로")

    def test_a_blank_name_falls_back(self):
        """
        빈 값을 그대로 쓰면 노드에 아무것도 안 적혀 누구의 대리인인지 모릅니다.
        """
        AgentSettings.objects.filter(user=self.me).update(agent_name="   ")

        self.assertEqual(agent_display_name(self.me), "서재민의 Bordo")

    def test_it_is_saved_through_the_api(self):
        res = self.client.patch("/api/v1/me/agent/settings",
                                {"agent_name": "제로"}, content_type="application/json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["agent_name"], "제로")

    def test_clearing_it_is_allowed(self):
        """지운 사람에게 '이름을 넣으십시오' 라고 하면 되돌릴 방법이 없습니다."""
        AgentSettings.objects.filter(user=self.me).update(agent_name="제로")

        res = self.client.patch("/api/v1/me/agent/settings",
                                {"agent_name": ""}, content_type="application/json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(AgentSettings.objects.get(user=self.me).agent_name, "")

    def test_too_long_is_refused(self):
        res = self.client.patch("/api/v1/me/agent/settings",
                                {"agent_name": "가" * 41}, content_type="application/json")

        self.assertEqual(res.status_code, 400)

    def test_settings_row_missing_still_works(self):
        """설정 화면에 한 번도 안 들어간 사용자의 대리인도 그려져야 합니다."""
        other = User.objects.create_user(email="n2@bordo.dev", password="x" * 10,
                                         name="강다은")

        self.assertEqual(agent_display_name(other), "강다은의 Bordo")
