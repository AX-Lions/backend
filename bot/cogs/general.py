import logging

import discord
from discord.ext import commands


log = logging.getLogger("bordo")


class GeneralCog(commands.Cog):
    def __init__(self, bot, backend):
        self.bot = bot
        self.backend = backend

        self.seen_message_ids: set[str] = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # Bot/대리인 발신 제외

        if message.guild is None:
            return  # DM은 대상 밖 (guild_id 필요한 idempotency_key 구성 불가)

        # 멘션이 와도 봇이 직접 답하지 않는다. 그대로 Backend로 넘기면
        # 대리인(ReAct·POLICY·유보)이 대상 판정까지 한다 — 봇은 판단하지 않는다.

        # TODO: 연결된 팀의 채널인지 확인 후 아니면 return (권한 없는 채널은 무시)

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

        await self.backend.post(
            "/internal/v1/discord/messages",
            json=payload
        )