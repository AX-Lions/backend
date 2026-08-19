import logging

import discord
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
    다만 게시 후 ACK만 실패하면(백엔드 재시작·배포 중 타임아웃 등) timeout
    뒤에 같은 이벤트가 다시 내려온다 — 서버가 멱등하게 만들어 둔 outbox_ack는
    "봇이 죽었을 때" 중복을 감수하겠다는 전제일 뿐, 프로세스가 살아 있는데
    ACK만 실패한 경우까지 재게시할 이유는 없다. `_dispatched_ids`로 막는다.

    지금 실제로 만들어지는 kind는 MESSAGE(회의 발언, speak_in_meeting
    스킬)뿐이다. ANNOUNCEMENT/DM은 모델에 정의만 돼 있고 아직 아무도 안
    만들어서 payload 모양이 확정되지 않았다 — 아래 처리는 최선의 추정이니
    실제로 쓰이기 시작하면 Backend와 맞춰 확인해야 한다.
    """

    _DISCORD_MESSAGE_LIMIT = 2000

    def __init__(self, bot, backend, poll_interval: float = 3.0):
        self.bot = bot
        self.backend = backend
        # 게시까지 끝났지만 아직 ACK가 안 끝난 이벤트 id. ACK가 실패해도
        # 다음 폴링에서 같은 이벤트가 다시 오면 여기 걸려 재게시를 막고
        # ACK만 다시 시도한다. ACK가 확정되면 바로 지운다 — 그 뒤로는
        # 백엔드가 이 id를 다시 안 보여주므로 계속 들고 있을 이유가 없다.
        self._dispatched_ids: set[str] = set()
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

        if event_id not in self._dispatched_ids:
            try:
                await self._dispatch(event)
            except Exception as exc:                          # noqa: BLE001
                log.exception("Outbox 게시 실패 id=%s kind=%s", event_id, event.get("kind"))
                if event_id not in self._dispatched_ids:
                    # 한 청크도 못 나갔다 — 아무 것도 안 나갔으니 백엔드가
                    # 처음부터 다시 시도하게 fail로 알린다.
                    await self.backend.post(
                        f"/internal/v1/outbox-events/{event_id}/fail",
                        json={"error": str(exc)},
                    )
                    return
                # 일부 청크는 이미 나갔다(_send_chunks가 표시해 둠). 여기서
                # fail을 부르면, 다음 폴링에서 같은 이벤트가 다시 와도
                # dispatched_ids에 걸려 재게시 없이 곧장 ack만 하게 되므로
                # 같은 이벤트에 fail·ack가 둘 다 찍히는 앞뒤 안 맞는 기록만
                # 남는다. 이미 재시도하지 않기로 정한 것이니 fail을 부르지
                # 않고 바로 아래에서 ack로 마무리한다.
                log.warning("Outbox 일부만 게시돼 재시도 없이 종료 id=%s", event_id)

        ack_result = await self.backend.post(f"/internal/v1/outbox-events/{event_id}/ack")
        if ack_result is None or get_error(ack_result):
            log.warning("Outbox ACK 실패 id=%s — 다음 폴링에서 ACK만 재시도", event_id)
        else:
            # ACK가 확정되면 백엔드가 이 id를 다시는 안 보여준다. 계속
            # 들고 있으면 프로세스 수명 내내 무한정 쌓이기만 하므로 지운다.
            self._dispatched_ids.discard(event_id)

    async def _dispatch(self, event: dict) -> None:
        kind = event.get("kind")
        payload = event.get("payload") or {}

        if kind == "MESSAGE":
            await self._send_message(event, payload)
        elif kind == "ANNOUNCEMENT":
            await self._send_announcement(event, payload)
        elif kind == "DM":
            await self._send_dm(event, payload)
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

    @classmethod
    def _chunks(cls, text: str, limit: int) -> list[str]:
        limit = max(limit, 1)
        return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]

    async def _send_chunks(self, event_id: str, sender, prefix: str, body: str) -> None:
        """
        2000자 제한에 맞춰 나눠 보내면서, 청크 하나가 나갈 때마다 바로
        `_dispatched_ids`에 표시한다.

        speak_in_meeting 파라미터·LLM 호출 어디에도 길이 제약이 없어(저장소
        전체에 max_tokens 0건) 2000자를 넘는 본문이 그대로 내려온다. 안
        자르면 send()가 400으로 죽고 /fail 백오프 끝에 DEAD가 되는데, DEAD는
        어디서도 안 보여서 사용자는 대리인이 말한 줄 안다.

        뒤 청크에서 실패해도(레이트리밋 등) 이미 나간 앞 청크는 다음 재시도
        때 다시 보내지 않는다 — 그 경우 뒤쪽 내용이 유실되지만, 이미 보낸
        내용을 중복 게시하는 것보다는 낫다. 대신 이미 뭔가 나간 상태에서
        끊기면, 잘렸다는 사실만이라도 사용자에게 남긴다 — 안 그러면 대리인이
        말을 하다 만 것처럼 보이는데 아무 표시도 없다.
        """
        # prefix는 1번째 청크에만 붙는다. 모든 청크를 prefix 길이만큼
        # 줄이면 2번째부터는 불필요하게 짧아져 메시지가 하나 더 나갈 수
        # 있으니, 1번째만 줄이고 나머지는 풀사이즈로 자른다.
        first_limit = max(self._DISCORD_MESSAGE_LIMIT - len(prefix), 1)
        if len(body) <= first_limit:
            chunks = [body]
        else:
            chunks = [body[:first_limit]] + self._chunks(
                body[first_limit:], self._DISCORD_MESSAGE_LIMIT)

        for i, chunk in enumerate(chunks):
            text = f"{prefix}{chunk}" if i == 0 else chunk
            try:
                await sender(text, allowed_mentions=discord.AllowedMentions.none())
            except Exception:                                  # noqa: BLE001
                if event_id in self._dispatched_ids:
                    await self._notify_truncated(event_id, sender)
                raise
            self._dispatched_ids.add(event_id)

    async def _notify_truncated(self, event_id: str, sender) -> None:
        # 안내 자체가 실패해도(레이트리밋이 여전히 걸려 있는 등) 원래
        # 예외 전파를 막으면 안 되므로 여기서 조용히 삼킨다.
        try:
            await sender("⚠️ (이어지는 내용 전송 실패)",
                        allowed_mentions=discord.AllowedMentions.none())
        except Exception:                                      # noqa: BLE001
            log.warning("잘림 안내 메시지도 전송 실패 id=%s", event_id)

    async def _send_message(self, event: dict, payload: dict) -> None:
        channel = await self._channel_of(event)
        body = self._body_of(payload)

        if payload.get("is_agent"):
            # speaker도 백엔드/LLM이 정하는 값이라 길이 제약이 없다. 자르지
            # 않으면 prefix가 2000자에 가까워질 때 _chunks가 1글자 단위로
            # 쪼개 메시지 수백~수천 개를 보내려 든다.
            speaker = (payload.get("speaker") or "대리인")[:100]
            prefix = f"🤖 **{speaker}**: "
        else:
            prefix = ""

        await self._send_chunks(event.get("id"), channel.send, prefix, body)

    async def _send_announcement(self, event: dict, payload: dict) -> None:
        # [TODO] 아직 만들어지지 않는 kind — payload 모양 추정치.
        channel = await self._channel_of(event)
        await self._send_chunks(event.get("id"), channel.send, "", self._body_of(payload))

    async def _send_dm(self, event: dict, payload: dict) -> None:
        # [TODO] 아직 만들어지지 않는 kind — payload 모양 추정치.
        discord_user_id = payload.get("discord_user_id")
        if not discord_user_id:
            raise ValueError("payload에 discord_user_id가 없습니다.")
        user = await self.bot.fetch_user(int(discord_user_id))
        await self._send_chunks(event.get("id"), user.send, "", self._body_of(payload))
