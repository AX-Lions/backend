import discord
from discord import app_commands
from discord.ext import commands

class DelegateCog(commands.Cog):
    def __init__(self, bot, backend, meeting_cog, delegate_on_users):
        self.bot = bot
        self.backend = backend
        self.meeting_cog = meeting_cog
        
        self.delegate_on_users = delegate_on_users
        
    @app_commands.command(
        name="delegate-on", 
        description="내 대리 참석을 활성화합니다. 어디서든 실행할 수 있습니다."
    )
    @app_commands.describe(scope="대리 참석 범위 (메모용, 예: 전체/특정 프로젝트명)")
    async def delegate_on(self, interaction: discord.Interaction, scope: str):
        await interaction.response.defer(ephemeral=True)
    
        # [TEMP] 특정 회의 스레드에 종속되지 않는 전역 설정으로 저장한다. 어느 채널에서 실행해도 동작한다.
        self.delegate_on_users.add(str(interaction.user.id))
    
        # 이미 진행 중인 회의에 참석자로 등록돼 있다면, 그 자리의 상태도 즉시 갱신한다.
        for thread_id, meeting in self.meeting_cog.active_meeting_threads.items():
            if str(interaction.user.id) in meeting["participants"]:
                meeting["participants"][str(interaction.user.id)] = "delegated"
                await self.meeting_cog.announce_to_thread(
                    thread_id, f"🤖 <@{interaction.user.id}>님이 대리 참석으로 전환했습니다. AI 대리인이 대신 참석합니다."
                )
    
        await self.backend.post("/internal/v1/delegate/on", json={"discord_user_id": str(interaction.user.id), "scope": scope})
        await interaction.followup.send("대리 참석을 활성화했습니다. 앞으로 시작되는 회의에도 자동 적용됩니다.", ephemeral=True)    

    @app_commands.command(name="delegate-off", description="대리 참석을 해제합니다. 어디서든 실행할 수 있습니다.")
    async def delegate_off(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # [TEMP] 전역 설정 해제. 본인이 직접 참석하는 것으로 되돌린다.
        self.delegate_on_users.discard(str(interaction.user.id))

        for thread_id, meeting in self.meeting_cog.active_meeting_threads.items():
            if str(interaction.user.id) in meeting["participants"]:
                meeting["participants"][str(interaction.user.id)] = "present"
                await self.meeting_cog.announce_to_thread(
                    thread_id, f"🙋 <@{interaction.user.id}>님이 대리 참석을 해제하고 직접 참석으로 전환했습니다."
                )

        await self.backend.post("/internal/v1/delegate/off", json={"discord_user_id": str(interaction.user.id)})
        await interaction.followup.send("대리 참석을 해제했습니다.", ephemeral=True)