"""
`/api/v1` — Discord 연동의 **웹 쪽** 반쪽.

봇이 `/bordo-connect` 로 받아 DM 으로 준 6자리 코드를 **여기서 입력**해야 두 계정이
이어집니다. 이 엔드포인트가 없어서 코드는 발급되는데 넣을 곳이 없었고, 계정이
이어진 사람은 시드 데이터뿐이었습니다.

| 경로 | 스코프 | 하는 일 |
|---|---|---|
| `POST/DELETE /me/discord/link` | 사람 | 내 Discord 계정 잇기 · 풀기 (설정 화면) |
| `POST/DELETE /teams/{id}/discord/link` | 팀 | 코드로 계정을 잇고, `guild_id` 가 오면 서버까지 팀에 (OWNER·ADMIN) |
| `GET /teams/{id}/discord/status` | 팀 | 서버 연결 여부 · 봇 생존 신호 · 연결된 팀원 |

`/internal/v1` 뷰(`views.py`)와 파일을 나눈 이유 — 저쪽은 서비스 토큰, 여기는 JWT 입니다.
한 파일에 섞이면 데코레이터 하나 빠뜨린 뷰가 어느 쪽 인증도 안 타는 채로 나갑니다.
"""
from __future__ import annotations

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.permissions import team_membership
from apps.common.throttle import check_rate
from apps.orgs.models import TeamRole
from config.errors import BordoError

from .models import GuildLink, LinkCode

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)

#: 코드는 6자리(16^6)에 10분 수명입니다. 상한 없이 두면 그 안에 다 넣어 볼 수 있고,
#: 맞히면 남의 Discord 계정을 내 Bordo 계정에 붙이게 됩니다.
CODE_TRIES_PER_MINUTE = 10

#: 봇 생존 신호가 캐시에 남는 키·수명. 봇이 `on_ready`/`on_resumed` 에서 보냅니다.
BOT_PRESENCE_KEY = "discord:bot:presence"
BOT_PRESENCE_TTL_SEC = 15 * 60


# ─────────────────────────────────────────── 계정 연결
def _consume_code(user, raw):
    """
    코드를 소비해 `user.discord_user_id` 를 채웁니다. 소비한 `LinkCode` 를 돌려줍니다.

    쓰인 코드와 만료된 코드를 **다른 오류로** 냅니다 — "이미 썼다" 는 대개 두 번
    눌렀다는 뜻이고 "만료됐다" 는 다시 발급받으라는 뜻입니다.
    """
    from apps.accounts.models import User

    check_rate(f"discord-code:{user.id}", CODE_TRIES_PER_MINUTE)
    code = str(raw or "").strip().upper()
    if not code:
        raise BordoError("VALIDATION_ERROR", "connect_code 는 필수입니다.")

    with transaction.atomic():
        # 행을 잠그고 검사합니다. 같은 코드가 동시에 두 번 오면(더블 클릭, 또는 코드를
        # 본 두 사람) 둘 다 "안 쓰였다" 를 보고 둘 다 통과합니다. 1회용이 뜻을 잃습니다.
        row = LinkCode.objects.select_for_update().filter(code=code).first()
        if row is None:
            raise BordoError("DISCORD_CODE_INVALID")
        if row.used_at is not None:
            raise BordoError("DISCORD_CODE_ALREADY_USED")
        if row.expires_at <= timezone.now():
            raise BordoError("DISCORD_CODE_EXPIRED")

        # 한 Discord 계정이 두 Bordo 계정에 이어지면 봇의 발언이 누구 것인지 정할 수
        # 없습니다. 이전에 이어진 계정에서 떼어 내고 이 사람에게 붙입니다.
        (User.all_objects.filter(discord_user_id=row.discord_user_id)
         .exclude(pk=user.pk).update(discord_user_id=""))
        user.discord_user_id = row.discord_user_id
        user.save(update_fields=["discord_user_id", "updated_at"])
        row.used_at = timezone.now()
        row.user = user
        row.save(update_fields=["used_at", "user"])
    return row


def _account_body(user):
    return {"linked": bool(user.discord_user_id),
            "user_id": str(user.id),
            "discord_user_id": user.discord_user_id or None}


@api_view(["POST", "DELETE"])
def me_discord_link(request):
    """설정 화면. `{"connect_code": "4F2A91"}` 하나면 됩니다."""
    user = request.user
    if request.method == "DELETE":
        if user.discord_user_id:
            user.discord_user_id = ""
            user.save(update_fields=["discord_user_id", "updated_at"])
        return Response(_account_body(user))
    _consume_code(user, request.data.get("connect_code"))
    return Response(_account_body(user))


# ─────────────────────────────────────────── 팀 · 서버 연결
def _guild_body(team, link):
    if link is None:
        return {"linked": False, "team_id": str(team.id), "guild_id": None}
    return {"linked": True, "team_id": str(team.id), "guild_id": link.guild_id,
            "linked_by": str(link.linked_by_id) if link.linked_by_id else None,
            "linked_at": link.created_at}


@api_view(["POST", "DELETE"])
def team_discord_link(request, team_id):
    """
    계약(`linkDiscord`) 그대로 — 코드로 계정을 잇고, `guild_id` 가 오면 서버도 팀에.

    서버 연결은 OWNER·ADMIN 만입니다. 일반 멤버가 붙이면 팀 전체의 회의가 그
    서버로 흘러갑니다. 봇의 `/bordo-team-connect` 와 같은 결과를 웹에서도 낼 수
    있게 둔 것이라, 봇에서 이미 연결했다면 코드만 넣으면 됩니다.
    """
    member = team_membership(request.user, team_id)
    team = member.team

    if request.method == "DELETE":
        return _unlink_guild(team, member)

    _consume_code(request.user, request.data.get("connect_code"))
    guild_id = str(request.data.get("guild_id") or "").strip()
    link = GuildLink.objects.filter(team=team).first()
    if guild_id:
        if member.team_role not in ADMINS:
            raise BordoError("TEAM_ACCESS_DENIED",
                             "서버 연결은 팀 소유자 또는 관리자만 할 수 있습니다. "
                             "계정 연결(코드 입력)은 됐습니다.")
        link, _ = GuildLink.objects.update_or_create(
            guild_id=guild_id, defaults=dict(team=team, linked_by=request.user))

    body = _account_body(request.user)
    body.update(_guild_body(team, link))
    return Response(body)


def _unlink_guild(team, member):
    """
    서버 연결 해제.

    대기 중인 Outbox 는 전부 `DEAD` 로 떨어뜨립니다. 보낼 곳이 없어졌는데 PENDING
    으로 남겨 두면 큐가 영원히 비지 않고, 다시 연결했을 때 옛 발언이 쏟아집니다.
    """
    from apps.agent.models import OutboxEvent

    if member.team_role not in ADMINS:
        raise BordoError("TEAM_ACCESS_DENIED", "소유자 또는 관리자만 해제할 수 있습니다.")
    with transaction.atomic():
        deleted, _ = GuildLink.objects.filter(team=team).delete()
        dropped = (OutboxEvent.objects
                   .filter(team=team, status=OutboxEvent.Status.PENDING)
                   .update(status=OutboxEvent.Status.DEAD,
                           last_error="Discord 연결이 해제돼 보낼 곳이 없습니다."))
    if not deleted:
        raise BordoError("DISCORD_NOT_LINKED")
    return Response({"team_id": str(team.id), "unlinked_at": timezone.now(),
                     "dropped_outbox_count": dropped})


@api_view(["GET"])
def team_discord_status(request, team_id):
    """
    연결 상태 진단.

    Intent · 권한은 **봇만 압니다.** 봇이 `/internal/v1/discord/presence` 로 보내 준
    생존 신호(있으면)만 실어 주고, 나머지는 서버가 아는 것 — 서버 연결 여부와
    계정이 이어진 팀원 수 — 입니다.
    """
    from apps.orgs.models import TeamMember

    member = team_membership(request.user, team_id)
    team = member.team
    link = GuildLink.objects.filter(team=team).select_related("linked_by").first()

    rows = list(TeamMember.objects.filter(team=team).select_related("user"))
    linked = [m for m in rows if m.user.discord_user_id]

    presence = cache.get(BOT_PRESENCE_KEY) or {}
    warnings = []
    if link is None:
        warnings.append({"code": "GUILD_NOT_LINKED",
                         "detail": "Discord 서버가 팀에 연결되지 않았습니다. "
                                   "Discord 에서 /bordo-team-connect 를 실행하십시오."})
    if not presence:
        warnings.append({"code": "BOT_NOT_SEEN",
                         "detail": "봇의 생존 신호가 없습니다. 봇이 꺼져 있거나 "
                                   "백엔드 주소가 다를 수 있습니다."})
    if not linked:
        warnings.append({"code": "NO_LINKED_MEMBERS",
                         "detail": "계정을 연결한 팀원이 없습니다. 회의 참석자로 "
                                   "잡히지 않습니다."})

    return Response({
        "connected": link is not None,
        "guild_id": link.guild_id if link else None,
        "linked_at": link.created_at if link else None,
        "bot_status": "READY" if presence else "UNKNOWN",
        "gateway": {"status": presence.get("status"), "last_seen_at": presence.get("at")},
        "members": {"total": len(rows), "linked": len(linked),
                    "unlinked_names": [m.user.display_name for m in rows
                                       if not m.user.discord_user_id]},
        "warnings": warnings,
    })
