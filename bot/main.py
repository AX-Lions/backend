import math
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
from cogs.outbox import OutboxCog

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

try:
    OUTBOX_POLL_INTERVAL = float(os.getenv("BORDO_OUTBOX_INTERVAL", "3"))
    # math.isfinite()가 nan·inf를 같이 걸러준다 — 그냥 <= 0만 보면 nan은
    # 모든 비교가 False라 통과하고, inf는 0보다 커서 통과한다. 둘 다
    # asyncio.sleep()에 들어가면 루프가 그대로 멈춘다.
    if not math.isfinite(OUTBOX_POLL_INTERVAL) or OUTBOX_POLL_INTERVAL <= 0:
        raise ValueError
except ValueError:
    log.warning("BORDO_OUTBOX_INTERVAL이 올바르지 않아 기본값 3초를 씁니다.")
    OUTBOX_POLL_INTERVAL = 3.0


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

backend = BackendClient(BACKEND_BASE_URL, SERVICE_TOKEN)


async def setup_hook():
    meeting_cog = MeetingCog(
        bot,
        backend,
    )

    delegate_cog = DelegateCog(
        bot,
        backend,
        meeting_cog,
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

    outbox_cog = OutboxCog(
        bot,
        backend,
        OUTBOX_POLL_INTERVAL
    )

    await bot.add_cog(meeting_cog)
    await bot.add_cog(delegate_cog)
    await bot.add_cog(bordo_cog)
    await bot.add_cog(general_cog)
    await bot.add_cog(outbox_cog)

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