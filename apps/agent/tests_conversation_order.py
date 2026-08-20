"""
대화 목록에서 **답이 질문보다 앞에 오지 않는다.**

`AgentMessage.sent_at` 은 `auto_now_add` 라 `timezone.now()` 를 씁니다. 시계
해상도가 굵은 환경(Windows 는 약 15ms)에서는 몇 밀리초 사이에 만든 두 행이
같은 값을 갖고, 목록은 `sent_at` 하나로만 정렬하므로 순서가 정해지지 않습니다.

`tests_conversation_reply` 도 이걸 밟지만 **시계에 기대므로 붙었다 떨어졌다**
합니다. 여기서는 두 행의 시각을 강제로 같게 만들어 **매번** 재현합니다.
그러지 않으면 회귀가 돌아와도 CI 가 조용히 넘어갑니다.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.agent.models import AgentConversation, AgentMessage, AgentRun


class ConversationOrder(TestCase):

    def setUp(self):
        self.user = User.objects.create(email="order@bordo.dev", name="서재민")
        self.conv = AgentConversation.objects.create(user=self.user, title="새 대화")
        self.question = AgentMessage.objects.create(
            conversation=self.conv, role=AgentMessage.Role.USER,
            body="지금 뭐 하고 있어?")

    def _answer(self, body="결제 모듈 붙이는 중입니다."):
        from apps.agent.tasks import _reply
        _reply(self.conv, self.question, AgentRun.objects.create(user=self.user), body)

    def rows(self):
        return list(AgentMessage.objects
                    .filter(conversation=self.conv).order_by("sent_at"))

    def test_시각이_같아도_답이_뒤에_온다(self):
        """
        이 시험의 본체다. 두 행의 `sent_at` 이 같으면 SQLite 는 순서를
        보장하지 않는다 — **답이 질문 위에 뜬다.**
        """
        with patch("django.utils.timezone.now", return_value=self.question.sent_at):
            self._answer()
        self.assertEqual([m.role for m in self.rows()], ["USER", "AGENT"])

    def test_답이_질문보다_이른_시각이어도_뒤에_온다(self):
        """시계가 뒤로 돌아간 경우(NTP 보정). 드물지만 나면 매번 거꾸로 뜬다."""
        earlier = self.question.sent_at - __import__("datetime").timedelta(seconds=5)
        with patch("django.utils.timezone.now", return_value=earlier):
            self._answer()
        self.assertEqual([m.role for m in self.rows()], ["USER", "AGENT"])

    def test_정상_시각이면_건드리지_않는다(self):
        """앞선 것보다 뒤면 그대로 둔다 — 필요 없을 때 시각을 밀면 안 된다."""
        self._answer()
        rows = self.rows()
        self.assertEqual([m.role for m in rows], ["USER", "AGENT"])
        self.assertGreater(rows[1].sent_at, rows[0].sent_at)

    def test_주고받기를_반복해도_답이_자기_질문_뒤에_온다(self):
        """
        여러 번 주고받는 경우.

        **연속한 질문끼리 시각이 겹치는 것은 여기서 막지 않는다.** 질문은
        사람이 치는 것이라 실제로는 초 단위로 벌어지고, 만드는 경로도
        다르다(`views.py`). 이 고침이 약속하는 것은 하나다 —
        **답은 자기 질문보다 항상 뒤에 온다.**
        """
        from apps.agent.tasks import _reply
        pairs = []
        for i in range(3):
            q = AgentMessage.objects.create(
                conversation=self.conv, role=AgentMessage.Role.USER, body=f"질문 {i}")
            with patch("django.utils.timezone.now", return_value=q.sent_at):
                _reply(self.conv, q, AgentRun.objects.create(user=self.user), f"답 {i}")
            a = AgentMessage.objects.filter(
                conversation=self.conv, body=f"답 {i}").first()
            pairs.append((q, a))

        for q, a in pairs:
            a.refresh_from_db()
            self.assertGreater(a.sent_at, q.sent_at, f"「{a.body}」 이 자기 질문보다 앞에 있다")
