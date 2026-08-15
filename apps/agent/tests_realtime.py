"""
실시간 전달 테스트.

두 가지를 봅니다.

- **권한이 없으면 붙는 즉시 끊기는가.** 붙여 놓고 나중에 검사하면 그 사이
  이벤트를 이미 본 뒤입니다.
- **`publish()` 호출부가 그대로인가.** 몸통만 바뀌어야 합니다.
"""
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.consumers import (CLOSE_BAD_TOKEN, CLOSE_FORBIDDEN, CLOSE_NO_TOKEN)
from apps.common.events import publish
from apps.orgs.models import Project, ProjectMember, Team
from config.asgi import application


def _token_for(user) -> str:
    from rest_framework_simplejwt.tokens import AccessToken
    return str(AccessToken.for_user(user))


class ConnectTest(TransactionTestCase):
    """
    `TransactionTestCase` 를 쓰는 이유 — 컨슈머가 별도 스레드에서 DB 를 읽습니다.
    일반 `TestCase` 의 트랜잭션 안에서는 그 스레드가 데이터를 못 봅니다.
    """

    def setUp(self):
        self.member = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                               name="멤버")
        self.stranger = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                                 name="외부인")
        team = Team.objects.create(name="팀", created_by=self.member)
        self.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                              created_by=self.member)
        ProjectMember.objects.create(project=self.project, user=self.member)

    def _url(self, token=None):
        base = f"/ws/projects/{self.project.id}"
        return f"{base}?token={token}" if token else base

    async def _connect(self, token=None):
        c = WebsocketCommunicator(application, self._url(token))
        connected, detail = await c.connect()
        return c, connected, detail

    async def test_member_connects(self):
        c, connected, _ = await self._connect(_token_for(self.member))
        self.assertTrue(connected)
        greeting = await c.receive_json_from()
        self.assertEqual(greeting["event_type"], "connected")
        await c.disconnect()

    async def test_no_token_is_rejected(self):
        _, connected, code = await self._connect()
        self.assertFalse(connected)
        self.assertEqual(code, CLOSE_NO_TOKEN)

    async def test_bad_token_is_rejected(self):
        _, connected, code = await self._connect("엉터리")
        self.assertFalse(connected)
        self.assertEqual(code, CLOSE_BAD_TOKEN)

    async def test_non_member_is_rejected(self):
        """붙여 놓고 나중에 검사하면 그 사이 이벤트를 이미 본 뒤입니다."""
        _, connected, code = await self._connect(_token_for(self.stranger))
        self.assertFalse(connected)
        self.assertEqual(code, CLOSE_FORBIDDEN)

    async def test_ping_pong(self):
        c, _, _ = await self._connect(_token_for(self.member))
        await c.receive_json_from()                 # connected
        await c.send_json_to({"type": "ping"})
        self.assertEqual((await c.receive_json_from())["event_type"], "pong")
        await c.disconnect()

    async def test_receives_project_event(self):
        c, _, _ = await self._connect(_token_for(self.member))
        await c.receive_json_from()

        layer = get_channel_layer()
        await layer.group_send(f"project.{self.project.id}", {
            "type": "bordo.event",
            "body": {"event_type": "task.completed", "project_id": str(self.project.id),
                     "user_id": None, "payload": {"task_id": "t-1"}},
        })
        got = await c.receive_json_from()
        self.assertEqual(got["event_type"], "task.completed")
        self.assertEqual(got["payload"]["task_id"], "t-1")
        await c.disconnect()


class PublishTest(TestCase):
    """호출부는 한 줄도 바뀌지 않아야 합니다."""

    def test_publish_signature_is_unchanged(self):
        import inspect
        params = list(inspect.signature(publish).parameters)
        self.assertEqual(params[:3], ["project_id", "event_type", "payload"])

    def test_publish_does_not_raise_without_layer(self):
        """
        실시간 전달이 실패했다고 이미 커밋된 쓰기 요청까지 500 으로 되돌릴 수는
        없습니다. 화면이 늦게 갱신될 뿐입니다.
        """
        from unittest.mock import patch
        with patch("channels.layers.get_channel_layer", side_effect=RuntimeError("펑")):
            publish("11111111-1111-1111-1111-111111111111", "test.event", {"a": 1})
