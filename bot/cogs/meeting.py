import logging
import time
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

        # meeting_id 자동완성용 예정 회의 목록 캐시. 한 글자씩 칠 때마다
        # Backend를 다시 부르면 왕복이 그대로 배로 늘어나므로, guild_id별로
        # 짧게 캐시해 같은 명령 입력 중에는 재사용한다.
        self._scheduled_cache: dict[int, tuple[float, list]] = {}
        self._SCHEDULED_CACHE_TTL = 5.0  # 초
    # --------------------------------------------------
    # 공통 함수
    # --------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fmt_time(iso_str: str) -> str:
        """자동완성 후보 라벨에 쓸 짧은 시각 표기. 파싱 실패하면 원문을 그대로 보여준다 —
        후보가 안 보이는 것보다 못생긴 시각이라도 보이는 게 낫다."""
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str)
        except ValueError:
            return iso_str
        return dt.strftime("%m/%d %H:%M")

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

    async def _scheduled_meetings(self, guild_id: int) -> list[dict]:
        now = time.monotonic()
        cached = self._scheduled_cache.get(guild_id)
        if cached and now - cached[0] < self._SCHEDULED_CACHE_TTL:
            return cached[1]

        result = await self.backend.get(
            "/internal/v1/meetings/scheduled", params={"guild_id": str(guild_id)}
        )
        if not isinstance(result, dict):
            return []

        # 다른 목록 엔드포인트와 마찬가지로 apps/common/views.py의 listing()이
        # 감싼 {"count", "results"} 모양을 그대로 따른다.
        meetings = result.get("results", [])
        self._scheduled_cache[guild_id] = (now, meetings)
        return meetings

    async def _meeting_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []

        meetings = await self._scheduled_meetings(interaction.guild_id)

        current = current.lower()
        choices = []
        for m in meetings:
            meeting_id = m.get("meeting_id")
            if not meeting_id:
                continue
            label = (f"{m.get('project_name', '')} · {m.get('title', '')} · "
                    f"{self._fmt_time(m.get('scheduled_at'))}")
            if current and current not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=meeting_id))

        return choices[:25]  # Discord 자동완성 후보 상한

    @app_commands.command(name="meeting-start", description="예정된 회의의 스레드를 엽니다.")
    @app_commands.describe(meeting_id="열 회의를 고르세요. 입력하면 예정된 회의가 자동완성됩니다.")
    @app_commands.autocomplete(meeting_id=_meeting_id_autocomplete)
    @app_commands.guild_only()
    async def meeting_start(self, interaction: discord.Interaction, meeting_id: str):
        await interaction.response.defer()

        # 제목은 Backend가 갖고 있는 예정된 회의에서 나온다 — 이 시점엔 아직 모르니
        # 임시 이름으로 스레드부터 만들고, 응답을 받은 뒤 실제 제목으로 바꾼다.
        thread = await interaction.channel.create_thread(
            name=f"[회의] {datetime.now().strftime('%Y%m%d')}",
            type=discord.ChannelType.public_thread,
        )

        result = await self.backend.post("/internal/v1/meetings/start",
            json={
                "guild_id": str(interaction.guild_id),
                "meeting_id": meeting_id,
                "thread_id": str(thread.id),
            }
        )

        if result is None:
            await interaction.followup.send(
                f"스레드는 만들었지만 회의 연결에 실패했습니다: {thread.mention}\n"
                "잠시 후 다시 시도하거나, 안 쓰는 스레드는 정리해주세요.", ephemeral=True
            )
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(
                f"스레드는 만들었지만 회의 연결에 실패했습니다: {thread.mention}\n"
                f"{error.get('message', '')}", ephemeral=True
            )
            return

        title = result.get("title", "회의")

        try:
            await thread.edit(name=(f"[회의] {datetime.now().strftime('%Y%m%d')} | {title}")[:100])
        except discord.HTTPException as exc:
            log.warning("스레드(%s) 이름 변경 실패: %s", thread.id, exc)

        participants = result.get("participants", [])
        self.active_meeting_threads[thread.id] = {
            "agenda": title,
            "started_at": self._now_iso(),
            "channel_id": interaction.channel_id,
            "starter_id": interaction.user.id,
            "meeting_id": meeting_id,
            "participants": {
                p["discord_user_id"]: ("delegated" if p.get("delegated") else "present")
                for p in participants if p.get("discord_user_id")
            },
        }

        announcement = (
            f"🟢 **회의가 시작되었습니다** · 안건: {title}\n"
            f"대화는 이 스레드에 남겨주세요. 끝나면 이 스레드 안에서 "
            f"`/meeting-end`를 실행하면 Backend가 요약을 정리합니다."
        )

        delegated = [p["discord_user_id"] for p in participants
                    if p.get("delegated") and p.get("discord_user_id")]

        if delegated:
            mentions = ", ".join(f"<@{uid}>" for uid in delegated)
            announcement += f"\n🤖 대리 참석이 켜져 있어 AI 대리인이 대신 참석합니다: {mentions}"

        await thread.send(announcement)

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
