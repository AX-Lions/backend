import re
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from services.backend import get_error

log = logging.getLogger("bordo")


class MeetingCog(commands.Cog):

    def __init__(self, bot, backend, delegate_on_users: set[str]):
        self.bot = bot
        self.backend = backend

        # [기존] _delegate_on_users
        # DelegateCog와 같은 set을 공유
        self.delegate_on_users = delegate_on_users

        # [기존] _active_meeting_threads
        self.active_meeting_threads: dict[int, dict] = {}
    # --------------------------------------------------
    # 공통 함수
    # --------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
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

        announcement = (
            f"🟢 **회의가 시작되었습니다** · 안건: {agenda}\n"
            f"대화는 이 스레드에 남겨주세요. 끝나면 이 스레드 안에서 "
            f"`/meeting-end`를 실행하면 Backend가 요약을 정리합니다."
        )

        delegated = [uid for uid, status in participants.items() if status == "delegated"]
        
        if delegated:
            mentions = ", ".join(f"<@{uid}>" for uid in delegated)
            announcement += f"\n🤖 대리 참석이 켜져 있어 AI 대리인이 대신 참석합니다: {mentions}"

        await thread.send(announcement)

        result = await self.backend.post("/internal/v1/meetings/start",
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
            "회의를 종료합니다. Backend가 대화 기록으로 요약을 만듭니다. "
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

        # 봇은 원본을 만들지 않는다. 대화는 이미 on_message에서 발언마다
        # Backend로 전달돼 있고, 요약도 Backend가 그 발언들로 만든다.
        async with interaction.channel.typing():
            result = await self.backend.post("/internal/v1/meetings/end",
                json={
                    "guild_id": str(interaction.guild_id),
                    "thread_id": str(thread_id),
                    "ended_by": str(interaction.user.id),
                    "ended_at": self._now_iso(),
                }
            )

        if result is None:
            # 네트워크 오류 등 일시적 실패 — 되돌리면 재시도가 성공할 여지가 있다.
            self.active_meeting_threads[thread_id] = meeting
            await interaction.followup.send(
                "회의 종료에 실패했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )
            return

        error = get_error(result)
        if error:
            if error.get("code") == "MEETING_NOT_FOUND":
                # thread_id는 Discord 채널 id라 항상 값이 있어 검증 오류로는 안 걸리고,
                # 이 코드만큼은 재시도해도 절대 성공하지 않는 영구적 오류다. 되돌리면
                # 이 회의는 영영 못 끝내므로, Backend가 없어도 봇은 계속 동작해야 한다는
                # 원칙(CLAUDE.md)대로 Discord 쪽은 정리하고 동기화 실패만 알린다.
                await interaction.channel.send("🔴 **회의가 종료되었습니다.**")
                await interaction.followup.send(
                    f"회의는 종료했지만 Backend와 동기화하지 못했습니다: "
                    f"{error.get('message', '')}", ephemeral=True
                )
                return

            # 그 외(예: 서비스 토큰 오류처럼 @internal 데코레이터가 뷰 실행 전에
            # 먼저 던지는 4xx)는 원인만 고치면 재시도로 풀린다. None과 동일하게
            # 되돌려서 다시 시도할 수 있게 한다.
            self.active_meeting_threads[thread_id] = meeting
            await interaction.followup.send(
                error.get("message", "회의 종료에 실패했습니다."), ephemeral=True
            )
            return

        if result.get("duplicate"):
            await interaction.followup.send("이미 종료된 회의입니다.", ephemeral=True)
            return

        summary = result.get("summary")
        if summary:
            embed = discord.Embed(
                title=f"📝 회의 요약 · {meeting['agenda']}",
                description=summary.get("one_line", ""),
                color=discord.Color.green(),
            )
            if summary.get("discovered_issues"):
                embed.add_field(name="발견된 이슈",
                                value="\n".join(f"- {i}" for i in summary["discovered_issues"]),
                                inline=False)
            if summary.get("changes"):
                embed.add_field(name="변동 사항",
                                value="\n".join(f"- {c}" for c in summary["changes"]),
                                inline=False)
            if summary.get("next_plans"):
                embed.add_field(name="다음 계획",
                                value="\n".join(f"- {p}" for p in summary["next_plans"]),
                                inline=False)
            await interaction.channel.send(embed=embed)

        await interaction.channel.send("🔴 **회의가 종료되었습니다.**")

        await interaction.followup.send("회의를 종료했습니다.")