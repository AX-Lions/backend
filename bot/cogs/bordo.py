import discord
from discord import app_commands
from discord.ext import commands

from services.backend import get_error

class BordoCog(commands.Cog):
    def __init__(self, bot, backend, gate):
        self.bot = bot
        self.backend = backend
        self.gate = gate
        
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

        error = get_error(result)
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

        if not isinstance(result, dict):
            # None(완전 실패)이거나, 2xx인데 JSON이 아닌 응답(비정상 상황) 둘 다 여기로 온다.
            await interaction.followup.send(
                "팀 연결에 실패했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )
            return

        # 캐시를 안 지우면 방금 연결한 서버가 게이트 캐시 TTL(5분) 동안
        # 계속 "미연결"로 막힌다.
        self.gate.invalidate_guild(interaction.guild_id)

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

        if result is None:
            await interaction.followup.send("팀 조회에 실패했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return

        error = get_error(result)
        if error:
            await interaction.followup.send(error.get("message", "팀 조회에 실패했습니다."), ephemeral=True)
            return

        teams = result.get("teams") if isinstance(result, dict) else None
        if not teams:
            await interaction.followup.send("아직 소속된 팀이 없습니다.", ephemeral=True)
            return

        listing = "\n".join(f"- {t.get('name', '이름 없음')} (`{t.get('role', '')}`)" for t in teams)
        message = f"소속된 팀:\n{listing}"
        # 팀이 아주 많은 경우를 대비한 방어. Discord 메시지 한도(2000자)를
        # 넘기면 send() 자체가 예외로 죽어 목록이 하나도 안 보인다.
        if len(message) > 2000:
            message = message[:1990] + "\n…"
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name="ask-bordo",
        description="특정 대리인에게 질문을 전달합니다."
    )
    @app_commands.describe(target="질문할 대리인의 주인 멘션", question="질문 내용")
    async def ask_bordo(self, interaction: discord.Interaction, target: discord.Member, question: str):
        # defer가 먼저다 — 3초 응답 시한 안에 게이트의 backend.get()이 (길드·
        # 개인 두 번 연속이라 특히) 안 끝날 수 있다.
        await interaction.response.defer()

        # 길드 체크는 guild_id가 없으면(DM) 자동으로 통과하도록 짜여 있다.
        # 다만 target이 discord.Member 타입이라 DM에서는 애초에 파라미터
        # 해석 단계에서 막힐 수 있다 — 이 게이트 변경과는 별개의 기존 문제라
        # 여기서는 안 건드린다.
        if not await self.gate.require(interaction):
            return

        # react.run()이 DB 조회·LLM 호출을 거쳐 동기로 돌아오기까지 몇 초씩
        # 걸릴 수 있다. defer()의 기본 "생각 중" 표시는 명령을 친 사람에게만
        # 보이므로, 같이 회의 중인 다른 사람도 볼 수 있게 공개 placeholder를
        # 먼저 띄우고 도착한 답으로 그 메시지를 그대로 바꿔치운다.
        placeholder = await interaction.followup.send(
            f"🤔 **{target.display_name}의 Bordo**가 생각 중입니다..."
        )

        result = await self.backend.post("/internal/v1/deputy/ask", json={
            "requester_discord_id": str(interaction.user.id),
            "target_discord_id": str(target.id),
            "question": question,
            "thread_id": str(interaction.channel_id),
        })

        if result is None:
            await placeholder.edit(content="⚠️ 답변을 받아오지 못했습니다. 잠시 후 다시 시도해주세요.")
            return

        error = get_error(result)
        if error:
            await placeholder.edit(content=error.get("message", "답변을 받아오지 못했습니다."))
            return

        # answered=False로 내부 실패한 경우(react.py의 _fail())도 "body" 키 자체는
        # 있고 빈 문자열이라, get()의 기본값은 이 경우를 못 잡는다 — 빈 답을
        # 그대로 보여주면 실패가 조용히 성공한 것처럼 보인다.
        body = (result.get("body") or "").strip() if isinstance(result, dict) else ""
        if not body:
            body = "답변을 받아오지 못했습니다."
        await placeholder.edit(content=f"🤖 **{target.display_name}의 Bordo**: {body}")
