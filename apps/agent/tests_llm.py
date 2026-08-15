"""
LLM 클라이언트 테스트.

실제 API 를 부르지 않습니다. 네트워크에 의존하면 CI 가 흔들리고 비용이 듭니다.
**형식 변환과 실패 처리**만 봅니다 — 실제 왕복은 개발 중 한 번 눈으로 확인했습니다.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.agent.services.llm import LLMClient, LLMResponse, ToolCall, _parse_arguments


def _raw(text=None, tool_calls=None, tokens=10):
    """OpenAI 응답 객체 흉내."""
    fn = MagicMock()
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    fn.choices = [choice]
    fn.usage = MagicMock(total_tokens=tokens)
    return fn


def _tc(cid="call_1", name="search_records", args='{"query":"스키마"}'):
    tc = MagicMock()
    tc.id = cid
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    return tc


class ArgumentParsingTest(SimpleTestCase):
    """
    모델이 만든 인자는 깨져서 옵니다. 여기서 예외가 나면 루프 한가운데서 죽고
    그때까지 모은 근거가 날아갑니다.
    """

    def test_valid(self):
        self.assertEqual(_parse_arguments('{"a": 1}'), {"a": 1})

    def test_broken_json_becomes_empty(self):
        self.assertEqual(_parse_arguments('{"a": '), {})

    def test_empty(self):
        self.assertEqual(_parse_arguments(""), {})

    def test_non_object_becomes_empty(self):
        """배열이나 숫자가 오면 스킬이 dict 로 다룰 수 없습니다."""
        self.assertEqual(_parse_arguments("[1,2]"), {})
        self.assertEqual(_parse_arguments("42"), {})


class ChatTest(SimpleTestCase):

    def setUp(self):
        self.c = LLMClient(api_key="test-key", model="gpt-5.5")

    def _patch(self, side_effect=None, return_value=None):
        fake = MagicMock()
        fake.chat.completions.create = MagicMock(side_effect=side_effect,
                                                 return_value=return_value)
        return patch.object(self.c, "_ensure", return_value=fake), fake

    def test_text_response(self):
        p, _ = self._patch(return_value=_raw(text="안녕하세요"))
        with p:
            r = self.c.chat([{"role": "user", "content": "hi"}])
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "안녕하세요")
        self.assertEqual(r.total_tokens, 10)

    def test_tool_call_response(self):
        p, _ = self._patch(return_value=_raw(tool_calls=[_tc()]))
        with p:
            r = self.c.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(r.tool_calls), 1)
        self.assertEqual(r.tool_calls[0].name, "search_records")
        self.assertEqual(r.tool_calls[0].arguments, {"query": "스키마"})

    def test_tools_are_wrapped_for_provider(self):
        """스킬은 {name, description, parameters} 만 압니다. 감싸기는 여기서만."""
        p, fake = self._patch(return_value=_raw(text="x"))
        with p:
            self.c.chat([{"role": "user", "content": "hi"}],
                        tools=[{"name": "t", "description": "d", "parameters": {}}])
        sent = fake.chat.completions.create.call_args.kwargs["tools"]
        self.assertEqual(sent[0]["type"], "function")
        self.assertEqual(sent[0]["function"]["name"], "t")

    def test_no_tools_key_when_empty(self):
        """빈 목록을 보내면 프로바이더가 거절합니다."""
        p, fake = self._patch(return_value=_raw(text="x"))
        with p:
            self.c.chat([{"role": "user", "content": "hi"}], tools=[])
        self.assertNotIn("tools", fake.chat.completions.create.call_args.kwargs)

    def test_system_goes_first(self):
        p, fake = self._patch(return_value=_raw(text="x"))
        with p:
            self.c.chat([{"role": "user", "content": "hi"}], system="너는 대리인이다")
        msgs = fake.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(msgs[0]["role"], "system")

    # ── 실패 처리 ──────────────────────────────────────────
    def test_missing_key_does_not_raise(self):
        """서버 전체가 못 뜨는 것보다 대리인만 오류를 돌려주는 편이 낫습니다."""
        r = LLMClient(api_key="").chat([{"role": "user", "content": "x"}])
        self.assertFalse(r.ok)
        self.assertEqual(r.error_kind, "config")

    def test_fatal_error_is_not_retried(self):
        p, fake = self._patch(side_effect=ValueError("invalid model"))
        with p:
            r = self.c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(r.error_kind, "fatal")
        self.assertEqual(fake.chat.completions.create.call_count, 1)

    @patch("apps.agent.services.llm.time.sleep")
    def test_retryable_error_is_retried(self, _sleep):
        p, fake = self._patch(side_effect=RuntimeError("503 overloaded"))
        with p:
            r = self.c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(r.error_kind, "retryable")
        self.assertEqual(fake.chat.completions.create.call_count, 3)

    @patch("apps.agent.services.llm.time.sleep")
    def test_recovers_after_transient_failure(self, _sleep):
        p, _ = self._patch(side_effect=[RuntimeError("rate limit"), _raw(text="ok")])
        with p:
            r = self.c.chat([{"role": "user", "content": "x"}])
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "ok")


class FeedbackMessageTest(SimpleTestCase):
    """루프가 프로바이더 형식을 몰라도 되도록 여기서 만들어 줍니다."""

    def test_assistant_message_carries_tool_calls(self):
        resp = LLMResponse(text="", tool_calls=[ToolCall("c1", "s", {"q": "x"})])
        msg = LLMClient.assistant_message(resp)
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["tool_calls"][0]["id"], "c1")
        self.assertEqual(json.loads(msg["tool_calls"][0]["function"]["arguments"]),
                         {"q": "x"})

    def test_tool_message_shape(self):
        msg = LLMClient.tool_message(ToolCall("c1", "s", {}), {"items": []})
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "c1")
        self.assertEqual(json.loads(msg["content"]), {"items": []})

    def test_tool_message_survives_non_serializable(self):
        """DB 에서 온 UUID·datetime 이 그대로 들어오면 직렬화가 터집니다."""
        import uuid
        msg = LLMClient.tool_message(ToolCall("c1", "s", {}), {"id": uuid.uuid4()})
        self.assertIn("id", json.loads(msg["content"]))
