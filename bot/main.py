import os
import re
import json
import logging
import asyncio
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from openai import AsyncOpenAI


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERVICE_TOKEN = os.getenv("BORDO_SERVICE_TOKEN")          # Backend 인증용. Bot Token과 분리 관리 (설계서 12장)
BACKEND_BASE_URL = os.getenv("BORDO_BACKEND_URL", "http://localhost:8000")
OUTBOX_POLL_INTERVAL = float(os.getenv("BORDO_OUTBOX_INTERVAL", "3"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")

# discord.log 파일에 로그 생성
logging.basicConfig(filename="discord.log", encoding="utf-8", level=logging.INFO)
log = logging.getLogger("bordo")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

_seen_message_ids: set[str] = set()

class BackendClient:
    def __init__(self, base_url: str, service_token: str, timeout: float = 5.0, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {service_token}"}
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        
    async def request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout, headers=self.headers) as session:
                    async with session.request(method, url, **kwargs) as resp:
                        if resp.status == 409:
                            # DUPLICATE_EVENT는 성공 처리하고 재게시하지 않는다 (12장)
                            return {"status": "duplicate"}
                        resp.raise_for_status()
                        if resp.content_type == "application/json":
                            return await resp.json()
                        return await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                log.warning("Backend 호출 실패(%s/%s): %s %s", attempt + 1, self.max_retries + 1, path, exc)
                await asyncio.sleep(0.5 * (attempt + 1))
        log.error("Backend 호출 최종 실패: %s (%s)", path, last_exc)
        return None

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

backend = BackendClient(BACKEND_BASE_URL, SERVICE_TOKEN)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_active_meeting_threads: dict[int, dict] = {}     # {thread_id: {"agenda", "started_at", "channel_id", "starter_id", "participants": {user_id: "present"|"delegated"}}}
_meeting_transcripts: dict[int, list[dict]] = {}  # {thread_id: [{"author", "content", "at"}]}
_delegate_on_users: set[str] = set()  # {discord_user_id, ...} — 여기 있으면 항상 "delegated"로 취급

async def _announce_to_thread(thread_id: int, text: str) -> None:
    """[TEMP] 회의 스레드에 상태 변경 등을 안내하는 메시지를 게시한다. 스레드가 없어졌거나
    권한이 없어도 명령 실행 자체가 실패하지 않도록 예외를 조용히 로그만 남긴다."""
    try:
        channel = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        await channel.send(text)
    except discord.HTTPException as exc:
        log.warning("스레드(%s) 안내 메시지 게시 실패: %s", thread_id, exc)

async def llm_chat_reply(user_message: str, author_name: str) -> str:
    """일반 채팅: 사용자 메시지에 LLM이 직접 답한다."""
    if openai_client is None:
        return "OPENAI_API_KEY가 설정되지 않아 채팅 기능을 쓸 수 없습니다."
    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "너는 Discord 서버의 AI 도우미다. 간결하고 친절하게 한국어로 답한다."},
            {"role": "user", "content": f"{author_name}: {user_message}"},
        ],
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()

async def generate_meeting_wrapup(transcript: list[dict]) -> dict:
    """회의 종료 시 최근 대화를 요약하고 TODO 리스트로 정리한다."""
    if openai_client is None:
        return {"summary": "OPENAI_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다.", "todos": []}
    if not transcript:
        return {"summary": "회의 중 기록된 대화가 없습니다.", "todos": []}

    convo = "\n".join(f"[{t['author']}] {t['content']}" for t in transcript)
    resp = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": (
                "너는 회의 대화록을 분석하는 비서다. 아래 JSON 스키마로만 응답한다: "
                '{"summary": "3~5문장 요약", "todos": ["할 일1", "할 일2"]}'
            )},
            {"role": "user", "content": convo[:12000]},
        ],
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    try:
        data = json.loads(resp.choices[0].message.content)
    except (ValueError, TypeError):
        data = {"summary": resp.choices[0].message.content, "todos": []}
    return {"summary": data.get("summary", ""), "todos": data.get("todos", [])}

# ---------- Message Formatter : 'AI 대리인' 표기, 근거는 개수+링크만 ----------
def build_agent_embed(agent_name: str, answer: str, evidence_count: int, detail_url: str,
                       confidence: str | None = None, withheld: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=f"🤖 AI 대리인 · {agent_name}",
        description="사용자 확인 필요" if withheld else answer,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="근거", value=f"{evidence_count}건 · [상세 보기]({detail_url})", inline=False)
    if confidence:
        embed.set_footer(text=f"신뢰도: {confidence}")
    return embed


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

    synced = await tree.sync()
    print(f"슬래시 명령 동기화 완료: {len(synced)}개 — {[c.name for c in synced]}")
    await backend.post("/internal/v1/discord/presence", json={"status": "online", "at": _now_iso()})

@bot.event
async def on_resumed():
    log.info("Gateway Resume 완료")
    await backend.post("/internal/v1/discord/presence", json={"status": "resumed", "at": _now_iso()})

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return  # Bot/대리인 발신 제외

    # [TEMP] 활성 회의 스레드의 대화는 종료 시 요약·TODO 생성을 위해 임시로 기록해둔다.
    if message.channel.id in _meeting_transcripts:
        _meeting_transcripts[message.channel.id].append({
            "author": message.author.display_name,
            "content": message.content,
            "at": _now_iso(),
        })

    # [TEMP] 봇을 멘션하면 일반 채팅으로 LLM이 직접 답한다.
    if bot.user in message.mentions:
        async with message.channel.typing():
            reply = await llm_chat_reply(message.content, message.author.display_name)
        await message.reply(reply, mention_author=False)

    # TODO: 연결된 팀의 채널인지 확인 후 아니면 return (권한 없는 채널은 무시)

    idempotency_key = f"{message.guild.id}:{message.channel.id}:{message.id}"
    if idempotency_key in _seen_message_ids:
        return
    _seen_message_ids.add(idempotency_key)

    payload = {
        "guild_id": str(message.guild.id),
        "channel_id": str(message.channel.id),
        "message_id": str(message.id),
        "author_discord_id": str(message.author.id),
        "content": message.content,
        "mentions": [str(u.id) for u in message.mentions],
        "thread_id": str(message.channel.id) if isinstance(message.channel, discord.Thread) else None,
        "created_at": message.created_at.isoformat(),
        "idempotency_key": idempotency_key,
    }
    await backend.post("/internal/v1/discord/messages", json=payload)
    await bot.process_commands(message)


# ------------ Slash Commands  ------------

@tree.command(name="bordo-connect", description="Bordo 서비스 계정 연결 코드를 DM으로 받습니다.")
async def bordo_connect(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await backend.post("/international/v1/discord/connect/code", json={"discord_user_id": str(interaction.user.id)})
    code = (result or {}).get("code", "발급 실패")
    await interaction.user.send(f"연결 코드: `{code}` (웹 설정 화면에 입력하세요)")
    await interaction.followup.send("DM으로 연결 코드를 보냈습니다.", ephemeral=True)

@tree.command(name="bordo-team", description="현재 연결된 팀은 확인하거나 선택 안내를 받습니다.")
async def bordo_team(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await backend.get("/internal/v1/teams/current", params={"discord_user_id": str(interaction.user.id)})
    await interaction.followup.send(str(result), ephemeral=True)
        
        
_MENTION_RE = re.compile(r"<@!?(\d+)>")


# /message-start로 회의 공간(thread) 생성
@tree.command(name="meeting-start", description="회의 스레드를 만들고 참석자를 수집합니다.")
@app_commands.describe(
    agenda="회의 안건",
    members="참석자 멘션. 비우면 본인만 등록됩니다. /delegate-on 해둔 사람은 자동으로 대리 참석 처리됩니다.",
)
async def meeting_start(interaction: discord.Interaction, agenda: str, members: str = ""):
    await interaction.response.defer()
    
    
    #thread 생성
    thread = await interaction.channel.create_thread(
        name=f"[회의] {datetime.now().strftime('%Y%m%d')} | {agenda}"[:100],
        type=discord.ChannelType.public_thread,
    )
    
    invited_ids = {interaction.user.id}
    invited_ids.update(int(m) for m in _MENTION_RE.findall(members))
 
    participants: dict[str, str] = {
        str(user_id): ("delegated" if str(user_id) in _delegate_on_users else "present")
        for user_id in invited_ids
    }
    _active_meeting_threads[thread.id] = {
        "agenda": agenda,
        "started_at": _now_iso(),
        "channel_id": interaction.channel_id,
        "starter_id": interaction.user.id,
        "participants": participants,
    }
    _meeting_transcripts[thread.id] = []
    announcement = (
        f"🟢 **회의가 시작되었습니다** · 안건: {agenda}\n"
        f"대화는 이 스레드에 남겨주세요. 끝나면 이 스레드 안에서 `/meeting-end`를 실행하면 요약과 TODO가 정리됩니다."
    )    
    delegated = [uid for uid, status in participants.items() if status == "delegated"]
    if delegated:
        mentions = ", ".join(f"<@{uid}>" for uid in delegated)
        announcement += f"\n🤖 대리 참석이 켜져 있어 AI 대리인이 대신 참석합니다: {mentions}"
    await thread.send(announcement)

    result = await backend.post("/internal/v1/meetings", json={
        "guild_id": str(interaction.guild_id),
        "text_channel_id": str(interaction.channel_id),
        "thread_id": str(thread.id),
        "agenda": agenda,
        "participants": [
                {"discord_user_id": uid, "status": status} for uid, status in participants.items()
            ],
    })

    await interaction.followup.send(f"회의 스레드를 만들었습니다: {thread.mention}")

@tree.command(name="meeting-end", description="회의를 종료하고 요약·TODO를 정리합니다. 회의 스레드 안에서 실행하세요.")
async def meeting_end(interaction: discord.Interaction):
    await interaction.response.defer()
    
    thread_id = interaction.channel_id
    if thread_id not in _active_meeting_threads:
        await interaction.followup.send("이 스레드에서 진행 중인 회의가 없습니다. 회의 스레드 안에서 실행해주세요.")
        return
    
    meeting = _active_meeting_threads.pop(thread_id)
    transcript = _meeting_transcripts.pop(thread_id, [])
    
    # 회의록 작성 및 todo 함수 들어갈 자리 #
    
    await interaction.channel.send("🔴 **회의가 종료되었습니다.**")

    await backend.post("/internal/v1/meetings/end", json={
        "guild_id": str(interaction.guild_id),
        "thread_id": str(thread_id),
        "ended_by": str(interaction.user.id),
        "ended_at": _now_iso(),
    })

    await interaction.followup.send("회의를 종료하고 요약을 게시했습니다.")

# 백엔드 연결 후 수정 필요
@tree.command(name="meeting-status", description="현재 회의와 대리 참석자를 표시합니다.")
async def meeting_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("현재 회의 상태를 조회 중입니다.", ephemeral=True)
    
@tree.command(name="ask-bordo", description="특정 대리인에게 질문을 전달합니다.")
@app_commands.describe(target="질문 대상 대리인", question="질문 내용")
async def ask_bordo(interaction: discord.Interaction, target: str, question: str):
    await interaction.response.defer()
    await backend.post("/internal/v1/deputy/ask", json={
        "requester_discord_id": str(interaction.user.id),
        "target": target,
        "question": question,
    })
    # 답변은 즉시 생성하지 않고 Outbox를 통해 게시된다 (8, 9장)
    await interaction.followup.send("질문을 전달했습니다. 답변은 곧 게시됩니다.")

@tree.command(name="delegate-on", description="내 대리 참석을 활성화합니다. 어디서든 실행할 수 있습니다.")
@app_commands.describe(scope="대리 참석 범위 (메모용, 예: 전체/특정 프로젝트명)")
async def delegate_on(interaction: discord.Interaction, scope: str):
    await interaction.response.defer(ephemeral=True)
 
    # [TEMP] 특정 회의 스레드에 종속되지 않는 전역 설정으로 저장한다. 어느 채널에서 실행해도 동작한다.
    _delegate_on_users.add(str(interaction.user.id))
 
    # 이미 진행 중인 회의에 참석자로 등록돼 있다면, 그 자리의 상태도 즉시 갱신한다.
    for thread_id, meeting in _active_meeting_threads.items():
        if str(interaction.user.id) in meeting["participants"]:
            meeting["participants"][str(interaction.user.id)] = "delegated"
            await _announce_to_thread(
                thread_id, f"🤖 <@{interaction.user.id}>님이 대리 참석으로 전환했습니다. AI 대리인이 대신 참석합니다."
            )
 
    await backend.post("/internal/v1/delegate/on", json={"discord_user_id": str(interaction.user.id), "scope": scope})
    await interaction.followup.send("대리 참석을 활성화했습니다. 앞으로 시작되는 회의에도 자동 적용됩니다.", ephemeral=True)    
    
@tree.command(name="delegate-off", description="대리 참석을 해제합니다. 어디서든 실행할 수 있습니다.")
async def delegate_off(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
 
    # [TEMP] 전역 설정 해제. 본인이 직접 참석하는 것으로 되돌린다.
    _delegate_on_users.discard(str(interaction.user.id))
 
    for thread_id, meeting in _active_meeting_threads.items():
        if str(interaction.user.id) in meeting["participants"]:
            meeting["participants"][str(interaction.user.id)] = "present"
            await _announce_to_thread(
                thread_id, f"🙋 <@{interaction.user.id}>님이 대리 참석을 해제하고 직접 참석으로 전환했습니다."
            )
 
    await backend.post("/internal/v1/delegate/off", json={"discord_user_id": str(interaction.user.id)})
    await interaction.followup.send("대리 참석을 해제했습니다.", ephemeral=True)   

if __name__ == "__main__":
    if not DISCORD_TOKEN or not SERVICE_TOKEN:
        raise RuntimeError("DISCORD_TOKEN / BORDO_SERVICE_TOKEN 환경변수가 필요합니다.")
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY가 없습니다. [TEMP] 채팅/요약/TODO 기능은 동작하지 않습니다.")
    bot.run(DISCORD_TOKEN, log_handler=None)  # logging.basicConfig로 이미 핸들러 설정됨
    