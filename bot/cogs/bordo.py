import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services.backend import get_error

log = logging.getLogger("bordo")

#: Discord 메시지 한도. 넘기면 edit()/send() 자체가 400으로 죽는다.
_MESSAGE_LIMIT = 2000


class BordoCog(commands.Cog):
    def __init__(self, bot, backend, gate):
        self.bot = bot
        self.backend = backend
        self.gate = gate

    @staticmethod
    async def _finish_response(interaction: discord.Interaction, content: str, *, log_ctx: str = "") -> None:
        """
        defer()로 만들어진 원본 응답(placeholder)을 최종 내용으로 채운다.

        길이 초과나 일시적 오류로 edit이 실패해도 "생각 중..."인 채로 방치하지
        않는다 — 방치하면 실패가 실패로 안 보이고 영구히 대기 중인 것처럼 보인다.
        """
        if len(content) > _MESSAGE_LIMIT:
            content = content[:_MESSAGE_LIMIT - 1] + "…"
        try:
            await interaction.edit_original_response(content=content)
        except discord.HTTPException:
            log.exception("ask-bordo 응답 게시 실패 %s", log_ctx)
            try:
                await interaction.edit_original_response(
                    content="⚠️ 답변을 표시하는 데 실패했습니다."
                )
            except discord.HTTPException:
                log.exception("ask-bordo 실패 안내조차 게시하지 못함 %s", log_ctx)

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

        # defer()가 이미 비-ephemeral이라 Discord 기본 "생각 중" 로딩 메시지도
        # 이미 공개다. 새 메시지를 또 보내면 로딩 표시가 두 개(Discord 것 +
        # 이것) 남는데, Discord 것은 이 코드가 채우지 않는 한 영영 안 채워진다.
        # 그래서 새로 보내지 않고 defer()가 만든 원본 응답 자체를 채운다.
        await interaction.edit_original_response(
            content=f"🤔 **{target.display_name}의 Bordo**가 생각 중입니다..."
        )

        # ReAct 실행은 최대 20초(OPENAI_TIMEOUT_SEC) × 6단계(MAX_STEPS)라
        # BackendClient 기본 timeout(5초)·재시도(2회) 안에 못 끝나는 게
        # 보통이다. 재시도는 특히 해롭다 — 실패해서가 아니라 응답이 느려서
        # 다시 보내는 거라, 이미 진행 중인 ReAct 실행을 그대로 중복 실행
        # 시킨다(#132). 이 호출만 타임아웃을 늘리고 재시도를 끈다.
        result = await self.backend.post(
            "/internal/v1/deputy/ask", json={
                "requester_discord_id": str(interaction.user.id),
                "target_discord_id": str(target.id),
                "question": question,
                "thread_id": str(interaction.channel_id),
            },
            timeout=aiohttp.ClientTimeout(total=90), max_retries=0)

        if result is None:
            await self._finish_response(
                interaction, "⚠️ 답변을 받아오지 못했습니다. 잠시 후 다시 시도해주세요.",
                log_ctx="(backend.post()가 None)")
            return

        error = get_error(result)
        if error:
            await self._finish_response(
                interaction, error.get("message", "답변을 받아오지 못했습니다."),
                log_ctx=f"(error={error.get('code')})")
            return

        # answered=False로 내부 실패한 경우(react.py의 _fail())도 "body" 키 자체는
        # 있고 빈 문자열이라, get()의 기본값은 이 경우를 못 잡는다 — 빈 답을
        # 그대로 보여주면 실패가 조용히 성공한 것처럼 보인다.
        body = (result.get("body") or "").strip() if isinstance(result, dict) else ""
        if not body:
            body = "답변을 받아오지 못했습니다."
        await self._finish_response(
            interaction, f"🤖 **{target.display_name}의 Bordo**: {body}",
            log_ctx=f"(run_id={result.get('run_id') if isinstance(result, dict) else None})")
