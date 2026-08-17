"""
모델에게 실제로 넘어가는 도구 목록.

## 왜 이 파일이 따로 있는가

스킬 테스트가 전부 클래스를 **직접 `.run()`** 하고 있었습니다. 그러면 스킬 자체는
검증되지만 **실제 LLM 이 그걸 부를 수 있는지는 한 번도 확인되지 않습니다.**

실제로 그 틈으로 문제가 지나갔습니다. `react.run()` 이 읽기 스킬만 카탈로그에
넣고 있어서 `propose_task` · `propose_schedule` · `send_message` ·
`ask_peer_agent` 를 모델이 부를 방법이 아예 없었는데, 스킬 테스트는 전부
통과하고 있었습니다.

여기서는 `client.chat()` 에 **넘어간 인자**를 붙잡아 봅니다.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.agent.models import AgentSettings
from apps.agent.services import react
from apps.agent.services.llm import LLMResponse
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class CatalogSpy:
    """`chat()` 이 받은 도구 목록을 그대로 붙잡아 둡니다."""

    def __init__(self, *responses):
        self._q = list(responses)
        self.catalogs = []

    def chat(self, messages, tools=None, system=""):
        self.catalogs.append(tools)
        return self._q.pop(0) if self._q else LLMResponse(text="끝")

    @property
    def names(self):
        """
        도구 이름만. 의도 분류 호출은 `tools` 가 없어 건너뜁니다.

        스펙은 프로바이더 중립 형식이라 `name` 이 최상위입니다 — OpenAI 모양
        (`{"type":"function","function":{...}}`)으로 바꾸는 것은 `llm.py` 입니다.
        """
        for c in self.catalogs:
            if c:
                return [t["name"] for t in c]
        return []


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="c1@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)
        AgentSettings.objects.create(user=cls.me)

        cls.peer = User.objects.create_user(email="c2@bordo.dev", password="x" * 10,
                                            name="임수연")
        TeamMember.objects.create(team=cls.team, user=cls.peer,
                                  team_role=TeamRole.MEMBER)
        ProjectMember.objects.create(project=cls.project, user=cls.peer)
        AgentSettings.objects.create(user=cls.peer)

    def _run(self, spy, **kw):
        with patch("apps.agent.services.react.default_client", spy):
            return react.run(principal=self.me, question="진행 상황 알려줘",
                             project_id=self.project.id, client=spy, **kw)


class CatalogTest(Base):

    def _names(self):
        spy = CatalogSpy(LLMResponse(text="STATUS"), LLMResponse(text="답"))
        self._run(spy)
        return spy.names

    def test_write_skills_are_offered_to_the_model(self):
        """
        카탈로그가 그대로 OpenAI `tools` 로 나갑니다. 목록에 없는 이름은 모델이
        **부를 수 없습니다.** 빠져 있으면 그 기능은 영영 안 일어납니다.
        """
        names = self._names()
        for expected in ("propose_task", "propose_schedule",
                         "send_message", "ask_peer_agent"):
            self.assertIn(expected, names, f"{expected} 를 모델이 부를 수 없습니다")

    def test_read_skills_are_still_offered(self):
        names = self._names()
        for expected in ("search_records", "search_meeting", "think"):
            self.assertIn(expected, names)

    def test_speak_in_meeting_is_not_offered(self):
        """
        회의 발언은 모델이 고를 일이 아닙니다. 루프가 끝난 뒤 코드가 부릅니다
        (`tasks.py::_speak`). 목록에 두면 모델이 중간에 회의에 끼어듭니다.
        """
        self.assertNotIn("speak_in_meeting", self._names())

    def test_every_offered_tool_can_actually_be_dispatched(self):
        """
        목록에 있는데 레지스트리에 없으면, 모델이 부르는 순간 "없는 스킬" 로
        실패합니다. 목록과 실행 가능한 것이 갈리면 안 됩니다.
        """
        from apps.agent.services.skills import registry

        known = {s.name for s in registry.list()}
        self.assertTrue(set(self._names()) <= known)


class WritePolicyTest(Base):
    """
    쓰기는 카탈로그에 두되 **실행 직전에** 막습니다.

    빼 버리면 모델은 그런 도구가 있다는 것조차 몰라서 "제 권한이 아닙니다" 라는
    말도 못 하고 그냥 침묵합니다.
    """

    def test_schedule_is_blocked_when_the_owner_said_so(self):
        from apps.agent.services.llm import ToolCall

        AgentSettings.objects.filter(user=self.me).update(allow_schedule_change=False)
        spy = CatalogSpy(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "propose_schedule",
                                             {"title": "회고", "start_at": "2026-09-07T10:00:00+09:00"})]),
            LLMResponse(text="일정은 본인에게 남겼습니다."),
        )
        out = self._run(spy)

        from apps.calendars.models import CalendarEvent
        self.assertEqual(CalendarEvent.objects.count(), 0, "정책을 뚫고 만들어졌습니다")

        blocked = [s for s in out.run.steps if s.get("kind") == "skill_blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("일정", blocked[0]["reason"])
        self._assert_survived(out)

    def _assert_survived(self, out):
        """
        막고 **끝난 게 아니라 계속 돌았는지** 봅니다.

        `skill_blocked` 만 보면 부족합니다. 차단 직후 예외가 나도 그 단계는 이미
        `steps` 에 들어가 있고, 실행이 죽어도 예외 핸들러가 `steps` 를 그대로
        저장하기 때문입니다. **막았다는 기록은 남는데 실행은 실패한** 상태를
        구별하지 못합니다.

        실제로 그렇게 한 번 지나갔습니다 — `tool_message(call.id, ...)` 로 넘겨
        문자열의 `.id` 를 읽다 터졌는데, 이 단언이 없어 테스트가 통과했습니다.
        회의에서는 대리인이 통째로 침묵합니다.
        """
        self.assertEqual(out.error, "", f"차단이 실행을 죽였습니다: {out.error}")
        self.assertTrue(out.text, "막은 뒤 답을 만들지 못했습니다")

    def test_peer_lookup_is_blocked_when_records_are_private(self):
        """
        남에게 물으려면 이쪽 맥락을 얼마간 건네야 합니다. 본인이 자기 기록을
        안 알리기로 했다면 그 맥락도 나가면 안 됩니다.
        """
        from apps.agent.models import AgentLookup
        from apps.agent.services.llm import ToolCall

        AgentSettings.objects.filter(user=self.me).update(
            disclose_work_plan_thought=False)
        spy = CatalogSpy(
            # `STATUS` 로 두면 POLICY 가 먼저 거절해 도구 루프에 안 들어갑니다.
            LLMResponse(text="OTHER"),
            LLMResponse(tool_calls=[ToolCall("c1", "ask_peer_agent",
                                             {"target_name": "임수연", "question": "q"})]),
            LLMResponse(text="확인하지 않았습니다."),
        )
        out = self._run(spy)
        self.assertEqual(AgentLookup.objects.count(), 0)
        blocked = [s for s in out.run.steps if s.get("kind") == "skill_blocked"]
        self.assertEqual(len(blocked), 1)
        self._assert_survived(out)
