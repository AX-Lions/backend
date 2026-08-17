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
import json
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
        self.turns = []

    def chat(self, messages, tools=None, system=""):
        self.catalogs.append(tools)
        # 얕은 복사를 뜹니다. `messages` 는 루프가 계속 덧붙이는 **같은 객체**라,
        # 그냥 담아 두면 매 턴이 마지막 상태를 가리켜 "이 턴에 무엇이 보였나" 를
        # 볼 수 없습니다.
        self.turns.append(list(messages))
        return self._q.pop(0) if self._q else LLMResponse(text="끝")

    @property
    def tool_replies(self):
        """모델이 돌려받은 도구 결과들. 마지막 턴 기준입니다."""
        return [m for m in (self.turns[-1] if self.turns else [])
                if m.get("role") == "tool"]

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

    def test_a_personal_message_is_blocked_when_records_are_private(self):
        """
        `send_message` 는 **즉시 나갑니다.** 승인 큐를 안 거치므로 나간 뒤에
        되돌릴 자리가 없습니다.

        최종 답변은 `judge.can_disclose()` 가 거르지만, 검색이 돌려준 원문은
        그 전에 이미 모델이 보고 있어 본문에 옮겨 담으면 판정을 우회합니다.
        """
        from apps.agent.services.llm import ToolCall
        from apps.chat.models import ChatMessage

        AgentSettings.objects.filter(user=self.me).update(
            disclose_work_plan_thought=False)
        spy = CatalogSpy(
            # 의도를 `OTHER` 로 둡니다. `STATUS` 로 두면 **POLICY 단계에서 실행
            # 자체가 거절돼** 도구 루프에 들어가지도 않습니다 — 그러면 이 테스트는
            # `_may_write` 를 확인하는 게 아니라 앞단이 막았다는 걸 확인하는
            # 셈이 됩니다(실제로 처음에 그렇게 써서 헛돌았습니다).
            LLMResponse(text="OTHER"),
            LLMResponse(tool_calls=[ToolCall("c1", "send_message",
                                             {"to_user_id": str(self.peer.id),
                                              "body": "지금 인덱스 작업 중입니다"})]),
            LLMResponse(text="따로 전하지 않았습니다."),
        )
        out = self._run(spy)

        self.assertEqual(ChatMessage.objects.count(), 0, "정책을 뚫고 나갔습니다")
        blocked = [s for s in out.run.steps if s.get("kind") == "skill_blocked"]
        self.assertEqual(len(blocked), 1)
        self._assert_survived(out)

    def test_the_tool_is_still_offered_even_when_blocked(self):
        """
        목록에서 빼면 모델은 왜 안 되는지 말할 수조차 없습니다. 두고 막습니다.
        """
        AgentSettings.objects.filter(user=self.me).update(allow_schedule_change=False)
        spy = CatalogSpy(LLMResponse(text="STATUS"), LLMResponse(text="답"))
        self._run(spy)
        self.assertIn("propose_schedule", spy.names)

    def test_allowed_write_goes_through(self):
        from apps.agent.services.llm import ToolCall
        from apps.tasks.models import Task, TaskStatus

        spy = CatalogSpy(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "propose_task",
                                             {"title": "인덱스 추가"})]),
            LLMResponse(text="후보로 올려 뒀습니다."),
        )
        self._run(spy)

        task = Task.objects.get()
        self.assertEqual(task.title, "인덱스 추가")
        # 1원칙 — AI 는 후보만 만들고 확정은 사람이 합니다.
        self.assertEqual(task.status, TaskStatus.PENDING_APPROVAL)

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


class SkillFailureTest(Base):
    """
    스킬이 실패했을 때 **모델이 그 사실을 아는가.**

    실패를 빈 결과로 돌려주면 모델은 "찾았는데 없었다" 와 "부르다 터졌다" 를
    구별하지 못하고, 되지도 않은 일을 했다고 답합니다.
    """

    def test_the_model_is_told_why_a_skill_failed(self):
        from apps.agent.services.llm import ToolCall
        from apps.chat.models import ChatMessage

        spy = CatalogSpy(
            LLMResponse(text="OTHER"),
            # 없는 사람에게 보냅니다 — 스킬이 `not_found` 로 실패합니다.
            LLMResponse(tool_calls=[ToolCall(
                "c1", "send_message",
                {"to_user_id": "00000000-0000-0000-0000-000000000000",
                 "body": "안녕하세요"})]),
            LLMResponse(text="전달하지 못했습니다."),
        )
        out = self._run(spy)

        self.assertEqual(ChatMessage.objects.count(), 0)

        replies = spy.tool_replies
        self.assertEqual(len(replies), 1)
        body = json.loads(replies[0]["content"])
        # 빈 dict 를 돌려주면 모델은 보낸 줄 압니다.
        self.assertIn("error", body, f"실패 사유가 모델에게 안 갔습니다: {body}")
        self.assertIn("받는 사람", body["error"])
        self.assertEqual(body["code"], "not_found")

    def test_a_successful_skill_still_returns_its_data(self):
        """실패 경로를 붙이면서 성공 경로 모양이 바뀌면 다른 스킬들이 깨집니다."""
        from apps.agent.services.llm import ToolCall

        spy = CatalogSpy(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "인덱스"})]),
            LLMResponse(text="답"),
        )
        self._run(spy)

        body = json.loads(spy.tool_replies[0]["content"])
        self.assertNotIn("error", body)
