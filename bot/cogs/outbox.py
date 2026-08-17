import logging

from discord.ext import commands, tasks

from services.backend import get_error

log = logging.getLogger("bordo")


class OutboxCog(commands.Cog):
    """
    Backend Outbox(OutboxEvent) 큐를 폴링해 Discord에 게시하고 ACK/FAIL을
    보고한다.

    대리인은 Discord를 직접 부르지 않는다. Backend가 OutboxEvent에 행 하나만
    남기면, 여기서 꺼내 게시한다 (GET/POST /internal/v1/outbox-events...,
    apps/discord/views.py의 outbox_events/outbox_ack/outbox_fail).

    서버가 조회 시 available_at을 잠시 뒤로 밀어 두므로(visibility timeout),
    같은 이벤트를 짧은 간격으로 두 번 가져오는 것은 서버 쪽에서 막아준다.
    다만 ACK가 실패하면 timeout 이후 같은 이벤트가 다시 보여 중복 게시가
    나갈 수 있다 — 이건 백엔드 설계상 받아들인 트레이드오프라 봇 쪽에서
    추가로 막지 않는다.

    지금 실제로 만들어지는 kind는 MESSAGE(회의 발언, speak_in_meeting
    스킬)뿐이다. ANNOUNCEMENT/DM은 모델에 정의만 돼 있고 아직 아무도 안
    만들어서 payload 모양이 확정되지 않았다 — 아래 처리는 최선의 추정이니
    실제로 쓰이기 시작하면 Backend와 맞춰 확인해야 한다.
    """

    def __init__(self, bot, backend, poll_interval: float = 3.0):
        self.bot = bot
        self.backend = backend
        self.drain_outbox.change_interval(seconds=poll_interval)

    async def cog_load(self):
        self.drain_outbox.start()

    def cog_unload(self):
        self.drain_outbox.cancel()

    @tasks.loop(seconds=3)
    async def drain_outbox(self):
        await self._drain_safe()

    async def _drain_safe(self) -> None:
        try:
            await self._drain()
        except Exception:                                      # noqa: BLE001
            # tasks.Loop는 OSError·aiohttp.ClientError 등 정해진 예외만 자동
            # 재시도한다. 그 외(JSON 파싱 실패 등)를 여기서 안 잡으면 루프
            # 자체가 영구히 멈춘다 — Backend가 이상해도 봇은 계속 동작해야
            # 한다는 원칙(CLAUDE.md)이 이 루프에서만 깨진다.
            log.exception("Outbox 폴링 중 예상치 못한 예외")

    async def _drain(self) -> None:
        result = await self.backend.get("/internal/v1/outbox-events", params={"limit": 20})

        if result is None:
            log.warning("Outbox 조회 실패 — 다음 폴링에서 재시도")
            return

        error = get_error(result)
        if error:
            # 4xx는 재시도해도 같은 결과다. 그냥 넘어가면 아무 로그도 안 남고
            # 큐만 계속 쌓이는데, 그건 이 값이 없어서 생긴 게 아니라 여기서
            # 조용히 삼켰기 때문이다.
            log.error("Outbox 조회 실패 %s: %s", error.get("code"), error.get("message"))
            return

        if not isinstance(result, dict):
            log.error("Outbox 조회 응답이 예상과 다릅니다: %r", result)
            return

        for event in result.get("results", []):
            await self._handle(event)

    @drain_outbox.before_loop
    async def before_drain_outbox(self):
        await self.bot.wait_until_ready()

    async def _handle(self, event: dict) -> None:
        event_id = event.get("id")
        if not event_id:
            # ack/fail을 부를 대상이 없다. 조용히 버려두면 visibility timeout
            # 뒤에 서버가 다시 보여줄 테니, 여기서는 로그만 남긴다.
            log.error("Outbox 이벤트에 id가 없습니다: %s", event)
            return

        try:
            await self._dispatch(event)
        except Exception as exc:                              # noqa: BLE001
            log.exception("Outbox 게시 실패 id=%s kind=%s", event_id, event.get("kind"))
            await self.backend.post(
                f"/internal/v1/outbox-events/{event_id}/fail",
                json={"error": str(exc)},
            )
            return

        await self.backend.post(f"/internal/v1/outbox-events/{event_id}/ack")

    async def _dispatch(self, event: dict) -> None:
        kind = event.get("kind")
        payload = event.get("payload") or {}

        if kind == "MESSAGE":
            await self._send_message(event, payload)
        elif kind == "ANNOUNCEMENT":
            await self._send_announcement(event, payload)
        elif kind == "DM":
            await self._send_dm(payload)
        else:
            raise ValueError(f"알 수 없는 outbox kind: {kind}")

    async def _channel_of(self, event: dict):
        channel_id = event.get("channel_id")
        if not channel_id:
            raise ValueError("channel_id가 없습니다.")
        cid = int(channel_id)
        return self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)

    @staticmethod
    def _body_of(payload: dict) -> str:
        # "body" 키가 있으면(빈 문자열이라도) 그 값을 쓴다. 없을 때만 content로
        # 넘어간다 — or로 하면 정상적인 빈 문자열 본문까지 content로 밀린다.
        if "body" in payload:
            return payload["body"]
        return payload.get("content", "")

    async def _send_message(self, event: dict, payload: dict) -> None:
        channel = await self._channel_of(event)
        body = self._body_of(payload)
        if payload.get("is_agent"):
            speaker = payload.get("speaker") or "대리인"
            await channel.send(f"🤖 **{speaker}**: {body}")
        else:
            await channel.send(body)

    async def _send_announcement(self, event: dict, payload: dict) -> None:
        # [TODO] 아직 만들어지지 않는 kind — payload 모양 추정치.
        channel = await self._channel_of(event)
        await channel.send(self._body_of(payload))

    async def _send_dm(self, payload: dict) -> None:
        # [TODO] 아직 만들어지지 않는 kind — payload 모양 추정치.
        discord_user_id = payload.get("discord_user_id")
        if not discord_user_id:
            raise ValueError("payload에 discord_user_id가 없습니다.")
        user = await self.bot.fetch_user(int(discord_user_id))
        await user.send(self._body_of(payload))
