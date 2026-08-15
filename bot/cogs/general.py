import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands


log = logging.getLogger("bordo")


class GeneralCog(commands.Cog):
    def __init__(self, bot, backend, meeting_cog, openai_service):
        self.bot = bot
        self.backend = backend
        self.meeting_cog = meeting_cog
        self.openai_service = openai_service

        self.seen_message_ids: set[str] = set()

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # Bot/대리인 발신 제외

        # [TEMP] 활성 회의 스레드의 대화는 종료 시 요약·TODO 생성을 위해 임시로 기록해둔다.
        if message.channel.id in self.meeting_cog.meeting_transcripts:
            self.meeting_cog.meeting_transcripts[message.channel.id].append({
                "author": message.author.display_name,
                "content": message.content,
                "at": self.now_iso(),
            })

        # [TEMP] 봇을 멘션하면 일반 채팅으로 LLM이 직접 답한다.
        if self.bot.user in message.mentions:
            async with message.channel.typing():
                reply = await self.openai_service.llm_chat_reply(
                    message.content,
                    message.author.display_name
                )

            await message.reply(reply, mention_author=False)

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