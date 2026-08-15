import discord
from discord import app_commands
from discord.ext import commands

class BordoCog(commands.Cog):
    def __init__(self, bot, backend):
        self.bot = bot
        self.backend = backend
        
    @app_commands.command(
        name="bordo-connect", 
        description="Bordo 서비스 계정 연결 코드를 DM으로 받습니다."
    )
    async def bordo_connect(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self.backend.post("/internal/v1/discord/connect/code", json={"discord_user_id": str(interaction.user.id)})
        code = (result or {}).get("code", "발급 실패")
        await interaction.user.send(f"연결 코드: `{code}` (웹 설정 화면에 입력하세요)")
        await interaction.followup.send("DM으로 연결 코드를 보냈습니다.", ephemeral=True)

    @app_commands.command(
        name="bordo-team",
        description="현재 연결된 팀은 확인하거나 선택 안내를 받습니다."
    )
    async def bordo_team(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self.backend.get("/internal/v1/teams/current", params={"discord_user_id": str(interaction.user.id)})
        await interaction.followup.send(str(result), ephemeral=True)

    @app_commands.command(
        name="ask-bordo",
        description="특정 대리인에게 질문을 전달합니다."
    )
    @app_commands.describe(target="질문 대상 대리인", question="질문 내용")
    async def ask_bordo(self, interaction: discord.Interaction, target: str, question: str):
        await interaction.response.defer()

        await self.backend.post("/internal/v1/deputy/ask", json={
            "requester_discord_id": str(interaction.user.id),
            "target": target,
            "question": question,
        })

        # 답변은 즉시 생성하지 않고 Outbox를 통해 게시된다.
        await interaction.followup.send("질문을 전달했습니다. 답변은 곧 게시됩니다.")
