"""
실시간 전달.

    /ws/projects/{project_id}?token=<JWT>

## 토큰을 쿼리로 받는 이유

브라우저의 WebSocket API 는 헤더를 붙일 수 없습니다. `Authorization` 을 쓸 방법이
없어 쿼리 파라미터로 받습니다. 접속 URL 이 로그에 남을 수 있으므로 **access 토큰만**
받고 refresh 는 받지 않습니다.

## 연결 시점에 권한을 확인합니다

프로젝트 멤버가 아니면 붙는 즉시 끊습니다. 붙여 놓고 메시지마다 검사하면,
그 사이에 흘러간 이벤트를 이미 본 뒤입니다.

## heartbeat

터널·프록시는 조용한 연결을 끊습니다. 30초마다 신호를 보내 살아 있음을 알립니다.
클라이언트가 끊긴 줄 모르고 기다리는 상황을 막습니다.
"""
from __future__ import annotations

import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger("bordo.events")

HEARTBEAT_SECONDS = 30

CLOSE_NO_TOKEN = 4001
CLOSE_BAD_TOKEN = 4002
CLOSE_FORBIDDEN = 4003


def project_group(project_id) -> str:
    return f"project.{project_id}"


def user_group(user_id) -> str:
    return f"user.{user_id}"


@database_sync_to_async
def _authenticate(token: str):
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken

    from apps.accounts.models import User

    try:
        payload = AccessToken(token)
    except TokenError:
        return None
    return User.objects.filter(pk=payload.get("user_id")).first()


@database_sync_to_async
def _is_member(user, project_id) -> bool:
    from apps.orgs.models import ProjectMember
    return ProjectMember.objects.filter(project_id=project_id, user=user).exists()


class ProjectConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self._beat = None
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        token = self._token_from_query()
        if not token:
            await self.close(code=CLOSE_NO_TOKEN)
            return

        self.user = await _authenticate(token)
        if self.user is None:
            await self.close(code=CLOSE_BAD_TOKEN)
            return

        if not await _is_member(self.user, self.project_id):
            # 붙여 놓고 나중에 검사하면 그 사이 이벤트를 이미 본 뒤입니다.
            await self.close(code=CLOSE_FORBIDDEN)
            return

        await self.channel_layer.group_add(project_group(self.project_id),
                                           self.channel_name)
        await self.channel_layer.group_add(user_group(self.user.id),
                                           self.channel_name)
        await self.accept()
        await self.send_json({"event_type": "connected",
                              "payload": {"project_id": str(self.project_id)}})
        self._beat = asyncio.create_task(self._heartbeat())

    async def disconnect(self, code):
        if getattr(self, "_beat", None):
            self._beat.cancel()
        if getattr(self, "user", None) is None:
            return
        await self.channel_layer.group_discard(project_group(self.project_id),
                                               self.channel_name)
        await self.channel_layer.group_discard(user_group(self.user.id),
                                               self.channel_name)

    async def receive_json(self, content, **kwargs):
        # 클라이언트가 보내는 것은 지금 ping 뿐입니다. 서버는 이 채널로 명령을
        # 받지 않습니다 — 쓰기는 전부 REST 를 거쳐야 권한 검사가 한곳에 남습니다.
        if content.get("type") == "ping":
            await self.send_json({"event_type": "pong", "payload": {}})

    async def bordo_event(self, message):
        """`publish()` 가 group_send 로 보낸 것을 그대로 흘립니다."""
        await self.send_json(message["body"])

    # ── 내부 ───────────────────────────────────────────────
    def _token_from_query(self) -> str:
        from urllib.parse import parse_qs
        qs = parse_qs((self.scope.get("query_string") or b"").decode())
        return (qs.get("token") or [""])[0]

    async def _heartbeat(self):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await self.send_json({"event_type": "heartbeat", "payload": {}})
        except asyncio.CancelledError:
            pass
        except Exception:                                      # noqa: BLE001
            # 하트비트가 죽어도 연결은 유지합니다. 조용해지면 프록시가 끊을 뿐
            # 데이터가 사라지지는 않습니다.
            logger.debug("heartbeat 중단", exc_info=True)
