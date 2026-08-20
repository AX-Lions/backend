import logging
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from services.backend import get_error

log = logging.getLogger("bordo")



#: 회의 시작 팝업을 열어 두는 시간.
#:
#: 회의는 이미 시작됐습니다. 오래 기다리면 그동안 오간 말을 대리인이 못 듣고,
#: 짧으면 자리에 있는 사람이 누를 틈이 없습니다. 답이 없다는 것은 대개 자리에
#: 없다는 뜻이라 **기본값을 대리 참석 쪽으로** 두고 시간을 짧게 잡습니다.
ATTENDANCE_TIMEOUT = 10.0


class AttendanceView(discord.ui.View):
    """
    `직접 참석` / `Bordo 에게 맡기기` 를 묻는 팝업.

    ## 왜 서버가 아니라 봇이 타이머를 드는가

    서버가 들면 봇이 죽어 있을 때 **아무에게도 안 물어보고 대리 처리**됩니다.
    묻지도 않고 대리인을 세우는 것이 이 서비스에서 제일 나쁜 실패입니다.

    ## 왜 스레드 안 메시지인가

    DM 으로 보내면 DM 을 막아 둔 사람에게 영영 안 닿고, 회의 스레드에는
    아무 흔적이 없어 다른 사람이 왜 저 사람만 빠졌는지 모릅니다.
    """

    def __init__(self, cog, thread_id: int, pending: dict[str, str],
                 unreachable: list[dict] | None = None):
        super().__init__(timeout=ATTENDANCE_TIMEOUT)
        self.cog = cog
        self.thread_id = thread_id
        #: discord_user_id → 대리인 호칭. 답한 사람은 여기서 빠집니다.
        self.pending = dict(pending)
        self.asked_total = len(self.pending)
        #: **Discord 계정을 안 이어 물어볼 수 없는 사람.**
        #:
        #: 예전에는 이 사람들을 그냥 걸러 버렸습니다. 그러면 남은 한 명이
        #: 답하는 순간 `모두 답했습니다` 가 떠서, 아무에게도 안 물어본 사실이
        #: 화면에서 사라집니다 — 그 사람은 `PENDING` 으로 남아 회의에서
        #: 이름을 불러도 대리인이 안 깨어납니다.
        self.unreachable = list(unreachable or [])
        self.message: discord.Message | None = None

    async def _answer(self, interaction: discord.Interaction, *, delegated: bool):
        uid = str(interaction.user.id)
        if uid not in self.pending:
            await interaction.response.send_message(
                "이 회의에서 참석 여부를 물은 대상이 아닙니다.", ephemeral=True)
            return

        result = await self.cog.backend.post(
            "/internal/v1/meetings/absence",
            json={"thread_id": str(self.thread_id), "discord_user_id": uid,
                  "delegated": delegated},
        )
        if result is None or get_error(result):
            # 못 눌렀다고 알려 줘야 합니다. 조용히 넘어가면 본인은 눌렀다고
            # 생각하는데 타임아웃이 대리 참석으로 넘겨 버립니다.
            await interaction.response.send_message(
                "지금은 저장하지 못했습니다. 다시 눌러 주세요.", ephemeral=True)
            return

        agent = self.pending.pop(uid, "")
        await interaction.response.send_message(
            "직접 참석으로 표시했습니다." if not delegated
            else f"{agent or 'Bordo'} 가 대신 참석합니다.", ephemeral=True)
        await self.cog.announce_to_thread(
            self.thread_id,
            f"🙋 <@{uid}> 님이 직접 참석합니다." if not delegated
            else f"🤖 <@{uid}> 님 대신 **{agent or 'Bordo'}** 가 참석합니다.")

        if not self.pending:
            self.stop()
            await self._close(self._done_note())

    @discord.ui.button(label="직접 참석합니다", style=discord.ButtonStyle.secondary)
    async def attend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, delegated=False)

    @discord.ui.button(label="Bordo 에게 맡기기", style=discord.ButtonStyle.primary)
    async def delegate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, delegated=True)

    async def on_timeout(self):
        # 답이 없는 사람은 대리 참석으로 넘깁니다. 자리에 없어서 못 누른 것이
        # 대부분인데, 여기서 아무것도 안 하면 그 사람 몫은 회의에서 통째로
        # 빠지고 돌아와서 볼 브리핑도 안 생깁니다(브리핑은 대리 참석자에게만).
        handed = []
        for uid, agent in list(self.pending.items()):
            result = await self.cog.backend.post(
                "/internal/v1/meetings/absence",
                json={"thread_id": str(self.thread_id), "discord_user_id": uid,
                      "delegated": True},
            )
            if result is None or get_error(result):
                log.warning("자동 대리 전환 실패 thread=%s user=%s", self.thread_id, uid)
                continue
            handed.append((uid, agent))

        await self._close(self._done_note("시간이 지나 자동으로 넘겼습니다."))
        if handed:
            names = ", ".join(f"<@{u}> 님 대신 **{a or 'Bordo'}**" for u, a in handed)
            await self.cog.announce_to_thread(
                self.thread_id, f"🤖 답이 없어 {names} 가 참석합니다.")

    def _done_note(self, note: str = "") -> str:
        """
        참석 확인 결과 문구.

        **`모두` 는 실제로 전원에게 물어봤을 때만 씁니다.** 전에는 `pending` 이
        비면 무조건 `모두 답했습니다` 였는데, `pending` 은 처음부터 *물어볼 수
        있었던 사람*의 부분집합이라 그 말이 근거보다 넓었습니다. 물어보지도 못한
        사람이 답한 것처럼 읽혀, 등록을 깜빡한 것을 이 문구가 덮어 줬습니다.
        """
        note = note or f"물어본 {self.asked_total}명이 모두 답했습니다."
        if self.unreachable:
            names = ", ".join(p.get("name") or "이름 없음" for p in self.unreachable)
            note += (f" ⚠️ {names} 님은 Discord 계정이 연결되지 않아 "
                     f"여기서 확인하지 못했습니다.")
        return note

    async def _close(self, note: str):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content=f"참석 확인이 끝났습니다 — {note}", view=self)
        except discord.HTTPException as exc:
            log.warning("참석 팝업 정리 실패 thread=%s: %s", self.thread_id, exc)


class MeetingCog(commands.Cog):

    def __init__(self, bot, backend, gate):
        self.bot = bot
        self.backend = backend
        self.gate = gate

        # [기존] _active_meeting_threads
        self.active_meeting_threads: dict[int, dict] = {}

        # meeting_id 자동완성용 예정 회의 목록 캐시. 한 글자씩 칠 때마다
        # Backend를 다시 부르면 왕복이 그대로 배로 늘어나므로, guild_id별로
        # 짧게 캐시해 같은 명령 입력 중에는 재사용한다.
        self._scheduled_cache: dict[int, tuple[float, list]] = {}
        self._SCHEDULED_CACHE_TTL = 5.0  # 초

        # meeting_end가 지금 처리 중인 thread_id. 재시작 생존은 필요 없다 —
        # 오직 같은 프로세스 안에서 /meeting-end가 거의 동시에 두 번 불렸을 때만
        # 막으면 된다. Backend의 meeting_end가 select_for_update 없이 읽고
        # 쓰므로, 이게 없으면 요약이 두 번 게시될 수 있다.
        self._ending_threads: set[int] = set()
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

    @staticmethod
    def _agenda_from_thread(channel) -> str:
        """meeting-start가 스레드 이름을 '[회의] {날짜} | {제목}'로 짓는다.
        meeting-end는 로컬 메모리 없이 동작하므로, 요약 임베드 제목에 쓸
        안건을 여기서 복구한다."""
        name = getattr(channel, "name", "") or ""
        if " | " in name:
            return name.split(" | ", 1)[1]
        return name or "회의"

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
        # defer가 먼저다 — 게이트의 backend.get()이 캐시 미스일 때 몇 초씩
        # 걸릴 수 있는데, Discord는 상호작용을 3초 안에 확인 응답 안 하면
        # 토큰을 무효화한다.
        await interaction.response.defer()

        if not await self.gate.require_guild(interaction):
            return

        # 제목은 Backend가 갖고 있는 예정된 회의에서 나온다 — 이 시점엔 아직 모르니
        # 임시 이름으로 스레드부터 만들고, 응답을 받은 뒤 실제 제목으로 바꾼다.
        thread = await interaction.channel.create_thread(
            name=f"[회의] {datetime.now().strftime('%Y%m%d')}",
            type=discord.ChannelType.public_thread,
        )

        payload = {
            "guild_id": str(interaction.guild_id),
            "meeting_id": meeting_id,
            "thread_id": str(thread.id),
        }
        # BackendClient가 이미 재시도했는데도 완전히 실패했다는 뜻이지만,
        # 마지막 시도가 서버에서는 실제로 성공하고 응답만 유실됐을 수도
        # 있다. 같은 thread_id로 한 번 더 보내면 Backend의 중복 처리
        # (discord_channel_id로 기존 회의 찾기)가 안전하게 확인해준다 —
        # 진짜 실패였으면 여기서도 그대로 None/에러가 온다.
        result = await self.backend.post_with_retry(
            "/internal/v1/meetings/start", json=payload)

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

        # 멘션할 수 있으면 멘션하고, 계정을 안 이었으면 **이름으로** 적습니다.
        # 예전에는 `discord_user_id` 가 없으면 이 줄에서 통째로 빠져, 대리
        # 참석이 켜져 있는데도 회의 시작 안내에 그 사람이 안 보였습니다.
        delegated = [(f"<@{p['discord_user_id']}>" if p.get("discord_user_id")
                      else (p.get("name") or "이름 없음"))
                     for p in participants if p.get("delegated")]

        if delegated:
            mentions = ", ".join(delegated)
            announcement += f"\n🤖 대리 참석이 켜져 있어 AI 대리인이 대신 참석합니다: {mentions}"

        await thread.send(announcement)

        await self._ask_attendance(thread)

        await interaction.followup.send(f"회의 스레드를 만들었습니다: {thread.mention}")

    async def _ask_attendance(self, thread) -> None:
        """
        아직 참석 여부를 안 정한 사람에게 묻습니다.

        **누가 대상인지는 서버가 정합니다.** 봇이 나름의 규칙으로 고르면
        "대리인이 답할 사람" 이 서버와 갈려, 대신 참석한다고 알렸는데 정작
        대리인은 안 깨어납니다. `asked=false` 가 그 목록입니다.

        물어볼 사람이 없으면 아무 메시지도 안 냅니다 — 웹에서 미리 불참을
        등록해 둔 회의에 빈 팝업이 뜨면 이미 정한 것을 다시 묻는 것처럼 보입니다.
        """
        result = await self.backend.get(
            "/internal/v1/meetings/participants",
            params={"thread_id": str(thread.id)},
        )
        if not isinstance(result, dict) or get_error(result):
            # 못 물어본 것뿐입니다. 회의 자체는 이미 열렸으니 막지 않습니다.
            log.warning("참석 대상 조회 실패 thread=%s", thread.id)
            return

        # 아직 안 정한 사람 = 물어볼 대상. **Discord 계정 유무로 버리지 않습니다.**
        # 예전에는 여기서 `if p.get("discord_user_id")` 로 걸렀는데, 그러면
        # 계정을 안 이은 팀원이 목록에서 조용히 사라져 `PENDING` 으로 남습니다 —
        # 회의에서 이름을 불러도 대리인이 깨어나지 않습니다.
        todo = [p for p in result.get("results", []) if not p.get("asked")]
        pending = {p["discord_user_id"]: p.get("agent_name") or ""
                   for p in todo if p.get("discord_user_id")}
        unreachable = [p for p in todo if not p.get("discord_user_id")]

        # 못 물어보는 사람은 **기다릴 이유가 없습니다.** 타이머는 팝업을 보고도
        # 안 누른 사람을 위한 것이고, 이쪽은 팝업이 닿지도 않습니다. 회의가
        # 시작된 이상 그 자리를 비워 두면 그 사람 몫이 통째로 빠집니다.
        handed = await self._hand_over_unreachable(thread, unreachable)

        if not pending:
            return

        view = AttendanceView(self, thread.id, pending, unreachable=unreachable)
        mentions = " ".join(f"<@{uid}>" for uid in pending)
        note = ""
        if handed:
            note = ("\n⚠️ " + ", ".join(handed)
                    + " 님은 Discord 계정이 연결되지 않아 물어보지 못했습니다 — "
                      "Bordo 가 대신 참석합니다.")
        view.message = await thread.send(
            f"{mentions}\n"
            f"회의가 시작됐습니다. 직접 참석하시나요?\n"
            f"**{int(ATTENDANCE_TIMEOUT)}초 안에 답이 없으면 Bordo 가 대신 참석합니다.**"
            f"{note}",
            view=view,
        )

    async def _hand_over_unreachable(self, thread, rows: list[dict]) -> list[str]:
        """
        물어볼 수 없는 사람을 대리 참석으로 넘기고, 넘긴 이름을 돌려준다.

        ## 묻지도 않고 대리인을 세워도 되는가

        `AttendanceView` 머리말에 **묻지도 않고 대리 처리하는 것이 제일 나쁜
        실패**라고 적혀 있습니다. 그 말은 여전히 맞지만, 여기서 아무것도 안 하면
        더 나쁜 일이 벌어집니다 — 그 사람은 회의에서 이름을 불러도 대리인이
        안 깨어나고, 회의가 끝난 뒤 브리핑도 안 생깁니다(브리핑은 대리 참석자
        에게만). 자리를 비운 것도 아니고 참석한 것도 아닌 상태로 통째로 빠집니다.

        그래서 넘기되 **넘겼다는 사실을 스레드에 적습니다.** 조용히 넘기는 것과
        적고 넘기는 것은 다릅니다.
        """
        handed = []
        for p in rows:
            result = await self.backend.post(
                "/internal/v1/meetings/absence",
                json={"thread_id": str(thread.id), "user_id": p.get("user_id"),
                      "delegated": True},
            )
            if result is None or get_error(result):
                log.warning("미연동 대리 전환 실패 thread=%s user=%s",
                            thread.id, p.get("user_id"))
                continue
            handed.append(p.get("name") or "이름 없음")
        return handed

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

        # Backend의 meeting_end는 select_for_update 없이 상태를 읽고 쓴다.
        # /meeting-end가 같은 스레드에서 거의 동시에 두 번 불리면 둘 다
        # status != ENDED를 보고 둘 다 종료 처리해 요약이 두 번 게시될 수
        # 있다 — 같은 프로세스 안에서만이라도 겹치지 않게 막는다. 재시작 생존은
        # 필요 없다(그 사이엔 처리 중인 호출이 없으니).
        if thread_id in self._ending_threads:
            await interaction.followup.send(
                "이미 종료를 처리하는 중입니다. 잠시 기다려주세요.", ephemeral=True
            )
            return

        self._ending_threads.add(thread_id)
        try:
            # active_meeting_threads(봇 메모리)를 사전 체크로 쓰지 않는다. 봇이
            # 재시작하면 이 dict는 비는데, Backend는 discord_channel_id로 회의를
            # 정확히 알고 있다 — 로컬 기억이 없어도 일단 물어본다.
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
                await interaction.followup.send(
                    "회의 종료에 실패했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True
                )
                return

            error = get_error(result)
            if error:
                if error.get("code") == "MEETING_NOT_FOUND":
                    # 사전 체크는 없앴지만, 로컬에 있었는지는 여전히 신호로 쓴다 —
                    # 두 경우를 구분해야 한다.
                    had_local_state = self.active_meeting_threads.pop(thread_id, None) is not None
                    if had_local_state:
                        # 로컬은 회의가 있다고 알고 있었는데 Backend가 모른다 — 진짜
                        # 동기화 문제다. Discord 쪽은 이미 진행 중이라고 안내해 둔
                        # 상태이니 정리하고 알린다.
                        await interaction.channel.send("🔴 **회의가 종료되었습니다.**")
                        await interaction.followup.send(
                            f"회의는 종료했지만 Backend와 동기화하지 못했습니다: "
                            f"{error.get('message', '')}", ephemeral=True
                        )
                        return

                    # 로컬도 몰랐고 Backend도 모른다 — 진짜로 이 스레드에 회의가 없다.
                    await interaction.followup.send(
                        "이 스레드에서 진행 중인 회의가 없습니다. "
                        "회의 스레드 안에서 실행해주세요.", ephemeral=True
                    )
                    return

                await interaction.followup.send(
                    error.get("message", "회의 종료에 실패했습니다."), ephemeral=True
                )
                return

            if result.get("duplicate"):
                # 이미 끝난 회의다 — active_meeting_threads에 남아있었다면
                # 여기서도 정리한다. 안 지우면 delegate.py의 폴백 스캔이 이미
                # 끝난 회의를 계속 활성 회의로 착각한다.
                self.active_meeting_threads.pop(thread_id, None)
                await interaction.followup.send("이미 종료된 회의입니다.", ephemeral=True)
                return

            # Backend가 이미 종료를 확정했다 — 이 아래 Discord 게시가 실패해도
            # 되살릴 이유가 없는(이미 끝난) 회의다. 게시 전에 먼저 정리해서,
            # 임베드 전송 실패 같은 이후 예외가 캐시 정리 자체를 건너뛰지 않게 한다.
            self.active_meeting_threads.pop(thread_id, None)

            agenda = self._agenda_from_thread(interaction.channel)

            summary = result.get("summary")
            if summary:
                embed = discord.Embed(
                    title=f"📝 회의 요약 · {agenda}",
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
        finally:
            self._ending_threads.discard(thread_id)
