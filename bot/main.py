import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from services.backend import BackendClient
from services.openai_service import OpenAIService
from cogs.general import GeneralCog
from cogs.meeting import MeetingCog
from cogs.delegate import DelegateCog
from cogs.bordo import BordoCog

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERVICE_TOKEN = os.getenv("BORDO_SERVICE_TOKEN")
BACKEND_BASE_URL = os.getenv("BORDO_BACKEND_URL", "http://localhost:8000")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")

openai_service = OpenAIService(OPENAI_API_KEY, OPENAI_MODEL)

# discord.log 파일에 로그 생성
logging.basicConfig(filename="discord.log", encoding="utf-8", level=logging.INFO)
log = logging.getLogger("bordo")


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

backend = BackendClient(BACKEND_BASE_URL, SERVICE_TOKEN)

_delegate_on_users: set[str] = set()


async def setup_hook():
    meeting_cog = MeetingCog(
        bot,
        backend,
        _delegate_on_users
    )

    delegate_cog = DelegateCog(
        bot,
        backend,
        meeting_cog,
        _delegate_on_users
    )

    bordo_cog = BordoCog(
        bot,
        backend
    )

    general_cog = GeneralCog(
        bot,
        backend,
        openai_service
    )

    await bot.add_cog(meeting_cog)
    await bot.add_cog(delegate_cog)
    await bot.add_cog(bordo_cog)
    await bot.add_cog(general_cog)

    await bot.tree.sync()


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    log.info("연결 완료. 저는 %s입니다.", bot.user.name)
    print(f"연결 완료. 저는 {bot.user.name}입니다.")

    if not intents.message_content:
        log.critical("MESSAGE_CONTENT Intent가 꺼져 있습니다. Developer Portal에서 활성화하세요.")

    missing_perms = []
    for guild in bot.guilds:
        perms = guild.me.guild_permissions
        for name in ("send_messages", "embed_links", "create_public_threads", "manage_events"):
            if not getattr(perms, name, False):
                missing_perms.append(f"{guild.name}:{name}")

    if missing_perms:
        log.warning("권한 부족: %s", missing_perms)
        # TODO: 관리자 채널/DM으로 경고 게시

    await backend.post(
        "/internal/v1/discord/presence",
        json={
            "status": "online",
            "at": MeetingCog._now_iso()
        }
    )


@bot.event
async def on_resumed():
    log.info("Gateway Resume 완료")

    await backend.post(
        "/internal/v1/discord/presence",
        json={
            "status": "resumed",
            "at": MeetingCog._now_iso()
        }
    )


if __name__ == "__main__":
    if not DISCORD_TOKEN or not SERVICE_TOKEN:
        raise RuntimeError("DISCORD_TOKEN / BORDO_SERVICE_TOKEN 환경변수가 필요합니다.")

    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY가 없습니다. [TEMP] 멘션 시 일반 채팅 응답 기능은 동작하지 않습니다.")

    bot.run(DISCORD_TOKEN, log_handler=None)