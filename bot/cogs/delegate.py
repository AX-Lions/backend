import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from services.backend import get_error


class DelegateCog(commands.Cog):
    def __init__(self, bot, backend, meeting_cog):
        self.bot = bot
        self.backend = backend
        self.meeting_cog = meeting_cog

    async def _post_with_retry(self, path: str, json: dict, retries: int = 1, delay: float = 2.0):
        """BackendClient.request()가 이미 네트워크 오류를 자체 재시도하다
        최종적으로 None을 돌려준 뒤, 한 번 더 시도해본다. 4xx(get_error로
        걸러지는 것)는 재시도해도 같은 결과라 여기서 다시 부르지 않는다 —
        오직 완전 실패(None)일 때만 대상이다."""
        result = await self.backend.post(path, json=json)
        for _ in range(retries):
            if result is not None:
                break
            await asyncio.sleep(delay)
            result = await self.backend.post(path, json=json)
        return result

    async def _announce_delegate_change(self, user_id: int, result: dict, *, delegated: bool) -> None:
        """대리 참석 전환을 진행 중인 회의 스레드에 알린다.

        Backend 응답의 thread_ids를 우선 쓴다 — 방금 실제로 갱신한 회의가
        무엇인지는 Backend가 정확히 안다. 아직 이 필드를 안 주는 구버전
        응답이면(키 자체가 없으면) active_meeting_threads를 스캔하던 예전
        방식으로 폴백한다 — Backend가 thread_ids를 내려주기 시작하면 이
        분기는 자동으로 안 타게 된다.
        """
        uid = str(user_id)
        thread_ids = result.get("thread_ids")
        if thread_ids is None:
            thread_ids = [
                tid for tid, meeting in self.meeting_cog.active_meeting_threads.items()
                if uid in meeting.get("participants", {})
            ]

        status = "delegated" if delegated else "present"
        text = (
            f"🤖 <@{user_id}>님이 대리 참석으로 전환했습니다. AI 대리인이 대신 참석합니다."
            if delegated else
            f"🙋 <@{user_id}>님이 대리 참석을 해제하고 직접 참석으로 전환했습니다."
        )

        for raw_thread_id in thread_ids:
            try:
                thread_id = int(raw_thread_id)
            except (TypeError, ValueError):
                continue

            meeting = self.meeting_cog.active_meeting_threads.get(thread_id)
            if meeting is not None and uid in meeting.get("participants", {}):
                meeting["participants"][uid] = status

            await self.meeting_cog.announce_to_thread(thread_id, text)

    @app_commands.command(
        name="delegate-on",
        description="내 대리 참석을 활성화합니다. 어디서든 실행할 수 있습니다."
    )
    @app_commands.describe(scope="대리 참석 범위 (메모용, 예: 전체/특정 프로젝트명)")
    async def delegate_on(self, interaction: discord.Interaction, scope: str):
        await interaction.response.defer(ephemeral=True)

        result = await self._post_with_retry(
            "/internal/v1/delegate/on",
            json={"discord_user_id": str(interaction.user.id), "scope": scope},
        )

        if result is None:
            await interaction.followup.send(
                "대리 참석 활성화에 실패했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(
                f"대리 참석 활성화에 실패했습니다: {error.get('message', '')}",
                ephemeral=True,
            )
            return

        # Backend에 실제로 반영된 뒤에만 스레드에 알린다 — 실패했는데
        # "전환했습니다"라고 먼저 게시해두면, 다른 참석자는 그 메시지만
        # 보고 실제로는 안 바뀐 상태를 성공으로 오해한다.
        await self._announce_delegate_change(interaction.user.id, result, delegated=True)

        await interaction.followup.send("대리 참석을 활성화했습니다. 앞으로 시작되는 회의에도 자동 적용됩니다.", ephemeral=True)

    @app_commands.command(name="delegate-off", description="대리 참석을 해제합니다. 어디서든 실행할 수 있습니다.")
    async def delegate_off(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        result = await self._post_with_retry(
            "/internal/v1/delegate/off", json={"discord_user_id": str(interaction.user.id)}
        )

        if result is None:
            await interaction.followup.send(
                "대리 참석 해제에 실패했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(
                f"대리 참석 해제에 실패했습니다: {error.get('message', '')}",
                ephemeral=True,
            )
            return

        await self._announce_delegate_change(interaction.user.id, result, delegated=False)

        await interaction.followup.send("대리 참석을 해제했습니다.", ephemeral=True)
