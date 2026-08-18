"""
말투 설정.

## 왜 프롬프트 문자열까지 보는가

설정을 저장하는 것만 확인하면 **대리인이 그 투로 말하는지는 모릅니다.** 값은
DB 에 잘 들어갔는데 프롬프트로 가는 길이 끊겨 있으면, 사용자는 골라 뒀는데
대리인은 늘 같은 투로 말합니다. 그 상태는 화면만 봐서는 드러나지 않습니다.

`tests_catalog.py` 에서 카탈로그를 붙잡아 본 것과 같은 이유입니다 — 저장까지가
아니라 **모델에게 실제로 넘어가는 것**을 봅니다.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentSettings, AgentSettingsVersion, AgentTone
from apps.agent.services import prompts, react
from apps.agent.services.llm import LLMResponse
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class SystemSpy:
    """`chat()` 이 받은 system 프롬프트를 붙잡아 둡니다."""

    def __init__(self, *responses):
        self._q = list(responses)
        self.systems = []

    def chat(self, messages, tools=None, system=""):
        self.systems.append(system)
        return self._q.pop(0) if self._q else LLMResponse(text="끝")

    @property
    def agent_system(self):
        """의도 분류 호출은 도구가 없습니다. 대리 실행 쪽만 봅니다."""
        return next((s for s in self.systems if "지켜야 할 것" in s), "")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="t1@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        cls.settings = AgentSettings.objects.create(user=cls.me)

    def _system_for(self, tone):
        AgentSettings.objects.filter(user=self.me).update(tone=tone)
        spy = SystemSpy(LLMResponse(text="STATUS"), LLMResponse(text="답"))
        with patch("apps.agent.services.react.default_client", spy):
            react.run(principal=self.me, question="진행 상황 알려줘",
                      project_id=self.project.id, client=spy)
        return spy.agent_system


class PromptTest(Base):

    def test_the_chosen_tone_reaches_the_model(self):
        """고른 투가 프롬프트에 실려야 대리인이 그 투로 말합니다."""
        for tone, mark in [(AgentTone.FORMAL, "정중한"),
                           (AgentTone.FRIENDLY, "구어체"),
                           (AgentTone.CONCISE, "핵심만")]:
            with self.subTest(tone=tone):
                self.assertIn(mark, self._system_for(tone))

    def test_tones_are_not_mixed(self):
        """하나를 고르면 나머지 안내는 들어가지 않습니다."""
        system = self._system_for(AgentTone.CONCISE)
        self.assertNotIn("정중한", system)
        self.assertNotIn("구어체", system)

    def test_concise_still_keeps_the_reason(self):
        """
        `간결하게` 가 유보 사유까지 줄이면 안 됩니다.

        말투는 **어떻게 말할지**만 정합니다. 짧게 만들려고 근거와 유보 사유를
        빼면 왜 그렇게 답했는지가 사라지고, 그건 말투가 판정을 바꾼 것입니다.
        """
        system = self._system_for(AgentTone.CONCISE)
        self.assertIn("유보 사유는 줄이지", system)

    def test_an_unset_tone_adds_nothing(self):
        """설정이 없어도 대리인은 돌아야 합니다."""
        self.assertNotIn("## 말투", prompts.build_system("서재민"))


class SettingsApiTest(Base):

    def setUp(self):
        # API 는 JWT 를 씁니다. 세션 로그인으로는 401 입니다.
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_tone_is_saved_and_bumps_the_version(self):
        before = AgentSettings.objects.get(user=self.me).active_version

        res = self.client.patch("/api/v1/me/agent/settings",
                                {"tone": "FRIENDLY"}, content_type="application/json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["settings"]["tone"], "FRIENDLY")
        obj = AgentSettings.objects.get(user=self.me)
        self.assertEqual(obj.tone, "FRIENDLY")
        self.assertEqual(obj.active_version, before + 1)

    def test_the_version_snapshot_carries_the_tone(self):
        """
        나중에 "왜 저렇게 말했지" 를 되짚으려면 그때의 투가 함께 있어야 합니다.
        """
        self.client.patch("/api/v1/me/agent/settings",
                          {"tone": "CONCISE"}, content_type="application/json")

        row = AgentSettingsVersion.objects.filter(user=self.me).first()
        self.assertEqual(row.snapshot["tone"], "CONCISE")

    def test_an_unknown_tone_is_refused(self):
        """
        조용히 기본값으로 되돌리면 사용자는 골랐다고 생각하는데 대리인은
        다른 투로 말합니다.
        """
        res = self.client.patch("/api/v1/me/agent/settings",
                                {"tone": "SASSY"}, content_type="application/json")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(AgentSettings.objects.get(user=self.me).tone,
                         AgentTone.FORMAL)

    def test_the_same_tone_does_not_bump_the_version(self):
        before = AgentSettings.objects.get(user=self.me).active_version

        self.client.patch("/api/v1/me/agent/settings",
                          {"tone": "FORMAL"}, content_type="application/json")

        self.assertEqual(AgentSettings.objects.get(user=self.me).active_version, before)
