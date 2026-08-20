import logging

import discord
from discord.ext import commands


log = logging.getLogger("bordo")


class GeneralCog(commands.Cog):
    def __init__(self, bot, backend, gate):
        self.bot = bot
        self.backend = backend
        self.gate = gate

        self.seen_message_ids: set[str] = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # Bot/대리인 발신 제외

        if message.guild is None:
            return  # DM은 대상 밖 (guild_id 필요한 idempotency_key 구성 불가)

        # 연결 안 된 서버의 채널인지 확인 후 아니면 return (권한 없는 채널은 무시).
        # 개인(발신자) 게이트는 안 건다 — 미연결 참석자의 발언까지 막으면
        # 회의록 원문이 비고, "원본을 지우지 않는다" 원칙과 충돌한다.
        if not await self.gate.guild_linked(message.guild.id):
            return

        # 멘션이 와도 봇이 직접 답하지 않는다. 그대로 Backend로 넘기면
        # 대리인(ReAct·POLICY·유보)이 대상 판정까지 한다 — 봇은 판단하지 않는다.

        idempotency_key = f"{message.guild.id}:{message.channel.id}:{message.id}"

        if idempotency_key in self.seen_message_ids:
            return

        self.seen_message_ids.add(idempotency_key)

        payload = {
            "guild_id": str(message.guild.id),
            "channel_id": str(message.channel.id),
            "message_id": str(message.id),
            "author_discord_id": str(message.author.id),
            "content": message.content,
            "mentions": [str(u.id) for u in message.mentions],
            "thread_id": str(message.channel.id) if isinstance(message.channel, discord.Thread) else None,
            "created_at": message.created_at.isoformat(),
            "idempotency_key": idempotency_key,
        }

        result = await self.backend.post(
            "/internal/v1/discord/messages",
            json=payload
        )

        await self._show_listening(message, result)

    #: 대리인이 이 말을 듣고 있다는 표시.
    #:
    #: 이모지 하나만 씁니다. 임시 메시지를 보내고 나중에 고치는 방법도 있지만,
    #: 회의 스레드는 사람들이 읽는 자리라 봇이 줄을 하나 더 차지하면 대화가
    #: 끊깁니다. 반응은 원문 아래에 조용히 붙습니다.
    LISTENING_EMOJI = "👀"

    async def _show_listening(self, message: discord.Message, result) -> None:
        """
        발언에 **곧바로** 반응을 붙인다.

        ## 왜 필요한가

        대리인 답변은 LLM 여러 단계를 거치고 Outbox 폴링(3초)까지 지나야
        스레드에 뜬다. 그동안 회의는 완전히 조용해서, 말한 사람은 **대리인이
        생각 중인지 아무도 안 불린 것인지 구별할 수 없다.**

        ## 왜 「누구의 대리인인지」까지 안 적는가

        반응에는 이름을 실을 수 없다. 대신 **답할 대리인이 하나도 없으면 아무
        반응도 안 붙는다** — 그것만으로 두 상태가 갈린다. 이름이 필요하면 답이
        올 때 그 메시지에 `{이름}의 Bordo:` 가 붙어서 온다.

        ## 실패해도 조용히 넘어간다

        `add_reactions` 권한이 없거나 메시지가 지워졌을 수 있다. 표시 하나
        때문에 발언 중계가 예외로 끝나면 회의록이 비는 것이 더 큰 손해다.
        """
        if not isinstance(result, dict):
            return
        if not result.get("listening"):
            return
        try:
            await message.add_reaction(self.LISTENING_EMOJI)
        except discord.HTTPException as exc:
            log.warning("청취 표시 실패 message=%s: %s", message.id, exc)