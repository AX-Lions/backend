"""대리인이 1:1 대화에 답하는지."""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentConversation, AgentMessage
from apps.agent.services.react import RunOutcome


class ConversationReplyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="a@b.dev", name="서재민")
        self.conv = AgentConversation.objects.create(user=self.user, title="새 대화")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, body="지금 뭐 하고 있어?"):
        return self.client.post(
            f"/api/v1/me/agent/conversations/{self.conv.id}/messages",
            {"body": body}, format="json")

    def test_대리인_답이_대화에_남는다(self):
        from apps.agent.models import AgentRun
        run = AgentRun.objects.create(user=self.user)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True, text="결제 모듈 붙이는 중입니다.")):
            r = self._post()
        self.assertEqual(r.status_code, 202)
        rows = list(AgentMessage.objects.filter(conversation=self.conv).order_by("sent_at"))
        self.assertEqual([m.role for m in rows], ["USER", "AGENT"])
        self.assertEqual(rows[1].body, "결제 모듈 붙이는 중입니다.")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.last_message_preview, "결제 모듈 붙이는 중입니다.")

    def test_유보도_답으로_남는다(self):
        from apps.agent.models import AgentRun
        run = AgentRun.objects.create(user=self.user)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=False,
                                           text="본인 확인이 필요합니다.")):
            self._post()
        agent_rows = AgentMessage.objects.filter(conversation=self.conv, role="AGENT")
        self.assertEqual(agent_rows.count(), 1)
        self.assertIn("본인 확인", agent_rows.first().body)

    def test_실행이_터져도_침묵하지_않는다(self):
        with patch("apps.agent.services.react.run", side_effect=RuntimeError("boom")):
            r = self._post()
        self.assertEqual(r.status_code, 202)
        agent_rows = AgentMessage.objects.filter(conversation=self.conv, role="AGENT")
        self.assertEqual(agent_rows.count(), 1, "조용히 멈추면 사용자는 안 보내진 줄 안다")
        self.assertNotIn("boom", agent_rows.first().body, "내부 오류 문구가 새면 안 된다")

    def test_같은_질문에_두_번_답하지_않는다(self):
        from apps.agent.models import AgentRun
        from apps.agent.tasks import run_agent_for_conversation
        run = AgentRun.objects.create(user=self.user)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True, text="답")) as m:
            self._post()
            msg_id = AgentMessage.objects.get(conversation=self.conv, role="USER").id
            run_agent_for_conversation(str(msg_id))   # 재시도가 온 상황
        self.assertEqual(m.call_count, 1)
        self.assertEqual(
            AgentMessage.objects.filter(conversation=self.conv, role="AGENT").count(), 1)

    def test_본인_비공개_기록을_본다(self):
        from apps.agent.models import AgentRun
        run = AgentRun.objects.create(user=self.user)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True, text="답")) as m:
            self._post()
        self.assertTrue(m.call_args.kwargs["allow_private"],
                        "본인이 자기 대리인과 하는 대화다")
