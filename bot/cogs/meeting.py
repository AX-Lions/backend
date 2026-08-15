import re
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


log = logging.getLogger("bordo")


class MeetingCog(commands.Cog):

    def __init__(self, bot, backend, delegate_on_users: set[str], openai_service):
        self.bot = bot
        self.backend = backend

        # [기존] _delegate_on_users
        # DelegateCog와 같은 set을 공유
        self.delegate_on_users = delegate_on_users

        # [기존] _active_meeting_threads
        self.active_meeting_threads: dict[int, dict] = {}

        # [기존] _meeting_transcripts
        self.meeting_transcripts: dict[int, list[dict]] = {}

        self.openai_service = openai_service
    # --------------------------------------------------
    # 공통 함수
    # --------------------------------------------------

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def announce_to_thread(self, thread_id: int, text: str) -> None:
        """[TEMP]
        회의 스레드에 상태 변경 등을 안내하는 메시지를 게시한다.
        스레드가 없어졌거나 권한이 없어도 명령 실행 자체가
        실패하지 않도록 예외를 조용히 로그만 남긴다."""
        try:
            channel = (
                self.bot.get_channel(thread_id)
                or await self.bot.fetch_channel(thread_id)
            )
            await channel.send(text)
        except discord.HTTPException as exc:
            log.warning("스레드(%s) 안내 메시지 게시 실패: %s", thread_id, exc)

    # --------------------------------------------------
    # /meeting-start
    # --------------------------------------------------

    _MENTION_RE = re.compile(r"<@!?(\d+)>")

    @app_commands.command(name="meeting-start", description="회의 스레드를 만들고 참석자를 수집합니다.")
    @app_commands.describe(
        agenda="회의 안건",
        members=(
            "참석자 멘션. 비우면 본인만 등록됩니다. "
            "/delegate-on 해둔 사람은 자동으로 대리 참석 처리됩니다."
        ),
    )
    async def meeting_start(self, interaction: discord.Interaction, agenda: str, members: str = ""):
        await interaction.response.defer()

        # thread 생성
        thread = await interaction.channel.create_thread(
            name=(f"[회의] "f"{datetime.now().strftime('%Y%m%d')} | {agenda}")[:100],
            type=discord.ChannelType.public_thread,
        )

        invited_ids = {interaction.user.id}

        invited_ids.update(int(m) for m in self._MENTION_RE.findall(members))

        participants: dict[str, str] = {
            str(user_id): ("delegated" if str(user_id) in self.delegate_on_users else "present")
            for user_id in invited_ids
        }

        self.active_meeting_threads[thread.id] = {
            "agenda": agenda,
            "started_at": self._now_iso(),
            "channel_id": interaction.channel_id,
            "starter_id": interaction.user.id,
            "participants": participants,
        }

        self.meeting_transcripts[thread.id] = []

        announcement = (
            f"🟢 **회의가 시작되었습니다** · 안건: {agenda}\n"
            f"대화는 이 스레드에 남겨주세요. 끝나면 이 스레드 안에서 "
            f"`/meeting-end`를 실행하면 요약과 TODO가 정리됩니다."
        )

        delegated = [uid for uid, status in participants.items() if status == "delegated"]
        
        if delegated:
            mentions = ", ".join(f"<@{uid}>" for uid in delegated)
            announcement += f"\n🤖 대리 참석이 켜져 있어 AI 대리인이 대신 참석합니다: {mentions}"

        await thread.send(announcement)

        result = await self.backend.post("/internal/v1/meetings",
            json={
                "guild_id": str(interaction.guild_id),
                "text_channel_id": str(interaction.channel_id),
                "thread_id": str(thread.id),
                "agenda": agenda,
                "participants": [
                    {
                        "discord_user_id": uid,
                        "status": status
                    }
                    for uid, status in participants.items()
                ],
            }
        )

        await interaction.followup.send(f"회의 스레드를 만들었습니다: {thread.mention}")

    # --------------------------------------------------
    # /meeting-end
    # --------------------------------------------------

    @app_commands.command(
        name="meeting-end",
        description=(
            "회의를 종료하고 요약·TODO를 정리합니다. "
            "회의 스레드 안에서 실행하세요."
        )
    )
    async def meeting_end(self, interaction: discord.Interaction):
        await interaction.response.defer()

        thread_id = interaction.channel_id

        if thread_id not in self.active_meeting_threads:
            await interaction.followup.send(
                "이 스레드에서 진행 중인 회의가 없습니다. "
                "회의 스레드 안에서 실행해주세요."
            )
            return

        meeting = self.active_meeting_threads.pop(thread_id)
        transcript = self.meeting_transcripts.pop(thread_id, [])

        # 회의록 작성 및 todo 함수 들어갈 자리 #

        await interaction.channel.send("🔴 **회의가 종료되었습니다.**")

        await self.backend.post("/internal/v1/meetings/end",
            json={
                "guild_id": str(interaction.guild_id),
                "thread_id": str(thread_id),
                "ended_by": str(interaction.user.id),
                "ended_at": self._now_iso(),
            }
        )

        await interaction.followup.send("회의를 종료하고 요약을 게시했습니다.")

    # --------------------------------------------------
    # /meeting-status
    # --------------------------------------------------

    # 백엔드 연결 후 수정 필요
    @app_commands.command(
        name="meeting-status",
        description="현재 회의와 대리 참석자를 표시합니다."
    )
    async def meeting_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("현재 회의 상태를 조회 중입니다.", ephemeral=True)