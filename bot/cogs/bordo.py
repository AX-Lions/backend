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
        name="bordo-team-connect",
        description="이 Discord 서버를 Bordo 팀에 연결합니다. (서버 관리 권한 필요)"
    )
    @app_commands.guild_only()
    @app_commands.describe(team_id="연결할 팀 ID. 소유·관리 중인 팀이 여럿일 때만 필요합니다.")
    async def bordo_team_connect(self, interaction: discord.Interaction, team_id: str = ""):
        # Backend는 Discord 서버 권한을 모른다. 여기서 안 막으면 아무나
        # 이 서버를 남의 팀에 연결할 수 있다.
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "서버 관리 권한이 있는 사람만 팀을 연결할 수 있습니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        payload = {
            "guild_id": str(interaction.guild_id),
            "discord_user_id": str(interaction.user.id),
        }
        if team_id:
            payload["team_id"] = team_id

        result = await self.backend.post("/internal/v1/teams/link", json=payload)

        error = (result or {}).get("error")
        if error:
            if error.get("code") == "TEAM_AMBIGUOUS":
                teams = error.get("details", {}).get("teams", [])
                listing = "\n".join(f"- `{t['team_id']}` {t['name']}" for t in teams)
                await interaction.followup.send(
                    "연결할 팀을 하나 골라 team_id 옵션과 함께 다시 실행해주세요:\n" + listing,
                    ephemeral=True
                )
                return

            await interaction.followup.send(
                error.get("message", "팀 연결에 실패했습니다."), ephemeral=True
            )
            return

        if result is None:
            await interaction.followup.send(
                "팀 연결에 실패했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"이 서버를 '{result.get('name', '팀')}'에 연결했습니다.", ephemeral=True
        )

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
    @app_commands.describe(target="질문할 대리인의 주인", question="질문 내용")
    async def ask_bordo(self, interaction: discord.Interaction, target: discord.Member, question: str):
        await interaction.response.defer()

        result = await self.backend.post("/internal/v1/deputy/ask", json={
            "requester_discord_id": str(interaction.user.id),
            "target_discord_id": str(target.id),
            "question": question,
            "thread_id": str(interaction.channel_id),
        })

        # Outbox consumer가 아직 없어서, 지금은 응답 본문에 바로 담겨 오는 답변/유보를
        # 그대로 게시한다. consumer가 붙으면 그때 옮긴다.
        body = (result or {}).get("body", "답변을 받아오지 못했습니다.")
        await interaction.followup.send(body)
