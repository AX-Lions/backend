import math
import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from services.backend import BackendClient
from cogs.general import GeneralCog
from cogs.meeting import MeetingCog
from cogs.delegate import DelegateCog
from cogs.bordo import BordoCog
from cogs.outbox import OutboxCog

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERVICE_TOKEN = os.getenv("BORDO_SERVICE_TOKEN")
BACKEND_BASE_URL = os.getenv("BORDO_BACKEND_URL", "http://localhost:8000")

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


"""
사람 접속 상태는 **켜져 있을 때만** 쓴다.

`presences` 와 `members` 는 Discord 개발자 포털에서 따로 켜야 하는 권한이다.
안 켜져 있으면 discord.py 가 연결 단계에서 `PrivilegedIntentsRequired` 로
죽는다 — **봇이 아예 안 뜬다.**

접속 상태는 대리 참석 판단의 **보조 신호**다. 그것 하나 때문에 회의 진행도
대리인 발언도 전부 멈추는 것은 맞바꿈이 안 맞는다. 그래서 기본은 끔이고,
포털에서 켠 뒤에 `BORDO_PRESENCE_INTENT=1` 로 함께 켠다.

    포털에서 켠다 → .env 에 BORDO_PRESENCE_INTENT=1 → 재시작

`members` 를 같이 켜는 이유 — PRESENCE_UPDATE 는 discord.py 가 길드 멤버
캐시에서 대상을 못 찾으면 조용히 버린다(`state.py` 의 `parse_presence_update`).
캐시가 비어 있으면 `on_presence_update` 자체가 대부분 안 불려서, 켜 놓고도
아무 신호가 안 가는 상태가 된다.
"""
PRESENCE_INTENT = os.getenv("BORDO_PRESENCE_INTENT", "").strip() in ("1", "true", "True")

intents = discord.Intents.default()
intents.message_content = True
intents.presences = PRESENCE_INTENT
intents.members = PRESENCE_INTENT

if not PRESENCE_INTENT:
    log.info("사람 접속 상태 전달 꺼짐 — 포털에서 Presence·Server Members 를 켠 뒤 "
             "BORDO_PRESENCE_INTENT=1 로 함께 켜십시오.")

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
        backend
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
async def on_presence_update(before: discord.Member, after: discord.Member):
    # 권한이 꺼져 있으면 이 이벤트는 애초에 안 온다. 방어로 한 번 더 본다.
    if not PRESENCE_INTENT:
        return
    if after.bot:
        return  # 봇 계정 발신 제외 — 이 봇 자신이나 다른 봇의 상태는 대리 참석 신호가 아니다

    # 활동(게임 등) 변화에도 매번 불린다. status가 안 바뀌었으면 보낼 게 없다.
    if before.status == after.status:
        return

    await backend.post(
        "/internal/v1/discord/presence",
        json={
            "discord_user_id": str(after.id),
            "status": str(after.status),
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

    bot.run(DISCORD_TOKEN, log_handler=None)