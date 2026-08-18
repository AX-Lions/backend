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

    @app_commands.command(
        name="delegate-on", 
        description="내 대리 참석을 활성화합니다. 어디서든 실행할 수 있습니다."
    )
    @app_commands.describe(scope="대리 참석 범위 (메모용, 예: 전체/특정 프로젝트명)")
    async def delegate_on(self, interaction: discord.Interaction, scope: str):
        await interaction.response.defer(ephemeral=True)

        # 이미 진행 중인 회의에 참석자로 등록돼 있다면, 그 자리의 상태도 즉시 갱신한다.
        for thread_id, meeting in self.meeting_cog.active_meeting_threads.items():
            if str(interaction.user.id) in meeting["participants"]:
                meeting["participants"][str(interaction.user.id)] = "delegated"
                await self.meeting_cog.announce_to_thread(
                    thread_id, f"🤖 <@{interaction.user.id}>님이 대리 참석으로 전환했습니다. AI 대리인이 대신 참석합니다."
                )
    
        result = await self._post_with_retry(
            "/internal/v1/delegate/on",
            json={"discord_user_id": str(interaction.user.id), "scope": scope},
        )

        if result is None:
            await interaction.followup.send(
                "대리 참석을 활성화했지만 Backend 동기화에 실패했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(
                f"대리 참석을 활성화했지만 Backend 동기화에 실패했습니다: {error.get('message', '')}",
                ephemeral=True,
            )
            return

        await interaction.followup.send("대리 참석을 활성화했습니다. 앞으로 시작되는 회의에도 자동 적용됩니다.", ephemeral=True)

    @app_commands.command(name="delegate-off", description="대리 참석을 해제합니다. 어디서든 실행할 수 있습니다.")
    async def delegate_off(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        for thread_id, meeting in self.meeting_cog.active_meeting_threads.items():
            if str(interaction.user.id) in meeting["participants"]:
                meeting["participants"][str(interaction.user.id)] = "present"
                await self.meeting_cog.announce_to_thread(
                    thread_id, f"🙋 <@{interaction.user.id}>님이 대리 참석을 해제하고 직접 참석으로 전환했습니다."
                )

        result = await self._post_with_retry(
            "/internal/v1/delegate/off", json={"discord_user_id": str(interaction.user.id)}
        )

        if result is None:
            await interaction.followup.send(
                "대리 참석을 해제했지만 Backend 동기화에 실패했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(
                f"대리 참석을 해제했지만 Backend 동기화에 실패했습니다: {error.get('message', '')}",
                ephemeral=True,
            )
            return

        await interaction.followup.send("대리 참석을 해제했습니다.", ephemeral=True)