"""
채팅 화면 전체.

사이드바는 한 번에 내려줍니다. 트리를 그리면서 팀·프로젝트·방마다 미읽음을
따로 부르면 첫 진입에서만 왕복이 수십 번 생기고, 클라이언트가 합계를 직접
더하면 서버와 숫자가 어긋납니다.
"""
import logging
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings as dj_settings
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.agent.services.flow import agent_display_name, agent_display_names
from apps.common.display import country_of, user_tz
from apps.common.events import publish
from apps.common.pagination import cursor_page
from apps.common.permissions import project_membership, team_membership
from apps.common.views import listing
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from config.errors import BordoError

from .models import (GROUP_TYPES, HIDE_ON_LEAVE, UNRENAMABLE, ChatAttachment,
                     ChatMessage, ChatRoom, DailyChatSummary, MessageImportance,
                     RoomMember, RoomType)
from .serializers import (AttachmentSerializer, DailySummarySerializer,
                          MessageSerializer, RoomSummarySerializer)
from .services import (direct_key, ensure_ai_room, ensure_project_room,
                       ensure_team_room, peer_agent_key, sync_all_group_rooms, touch)

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)
ATTACHMENT_TTL_HOURS = 24


# ─────────────────────────────────────────── 접근 · 조립 헬퍼
def room_access(user, room_id, *, allow_left=False):
    """참여자가 아니면 `404` 입니다 — 방의 존재 자체를 숨깁니다."""
    room = (ChatRoom.objects.filter(pk=room_id)
            .select_related("team", "project").first())
    if not room:
        raise BordoError("CHAT_ROOM_NOT_FOUND", details={"room_id": str(room_id)})
    member = RoomMember.objects.filter(room=room, user=user).first()
    if not member or (member.left_at and not allow_left):
        raise BordoError("CHAT_ROOM_NOT_FOUND", details={"room_id": str(room_id)})
    return room, member


logger = logging.getLogger("bordo.chat")


def visible_messages(room, member):
    """
    이 사람에게 보이는 메시지.

    나중에 초대된 사람은 입장 이후 것만 봅니다 — 기존 1:1·단체 대화가
    제3자에게 소급해서 열리면 안 됩니다.
    """
    qs = ChatMessage.objects.filter(room=room)
    if member.visible_from:
        qs = qs.filter(sent_at__gte=member.visible_from)
    return qs


def unread_count(room, member):
    qs = visible_messages(room, member).exclude(sender_id=member.user_id)
    if member.last_read_at:
        qs = qs.filter(sent_at__gt=member.last_read_at)
    return qs.count()


def message_context(user, messages):
    """`is_mine` · 내 확인 여부 · 읽음 수를 한 번에 모읍니다."""
    ids = [m.id for m in messages]
    confirmed = dict(MessageImportance.objects
                     .filter(user=user, message_id__in=ids)
                     .values_list("message_id", "confirmed_at"))
    sender_ids = {m.sender_id for m in messages if m.sender_id}
    from apps.accounts.models import User
    avatars = dict(User.all_objects.filter(id__in=sender_ids)
                   .values_list("id", "avatar_url"))
    return {"me_id": user.id, "confirmed_map": confirmed, "avatars": avatars,
            "read_map": _read_counts(messages)}


def _read_counts(messages):
    """
    각 메시지를 몇 명이 읽었는지.

    읽음 워터마크(`last_read_at`)만 있으므로 방별로 워터마크를 모아 세어야 합니다.
    메시지마다 세면 목록 50건에 쿼리 50번입니다.
    """
    if not messages:
        return {}
    room_ids = {m.room_id for m in messages}
    marks = {}
    for room_id, read_at in (RoomMember.objects
                             .filter(room_id__in=room_ids, left_at__isnull=True)
                             .exclude(last_read_at=None)
                             .values_list("room_id", "last_read_at")):
        marks.setdefault(room_id, []).append(read_at)
    out = {}
    for m in messages:
        stamps = marks.get(m.room_id, [])
        # 보낸 사람 본인의 워터마크는 항상 자기 메시지 이후라 1을 빼줍니다.
        out[m.id] = max(0, sum(1 for s in stamps if s >= m.sent_at) - 1)
    return out


def room_context(user, rooms):
    """사이드바·목록용 집계를 한 번에."""
    ids = [r.id for r in rooms]
    memberships = {m.room_id: m for m in
                   RoomMember.objects.filter(user=user, room_id__in=ids)}

    unread_map, important_map = {}, {}
    for r in rooms:
        m = memberships.get(r.id)
        if not m:
            continue
        unread_map[r.id] = unread_count(r, m)
        important_map[r.id] = _has_unconfirmed_important(r, m, user)

    last_map = {}
    for r in rooms:
        m = memberships.get(r.id)
        last = (visible_messages(r, m) if m else ChatMessage.objects.filter(room=r)) \
            .order_by("-sent_at").first()
        last_map[r.id] = None if not last else {
            "sender_name": last.sender_name,
            "preview": ("삭제된 메시지입니다" if last.deleted_at
                        else (last.body[:80] or "(첨부)")),
            "sent_at": last.sent_at,
        }

    avatar_map, member_map = {}, {}
    rows = (RoomMember.objects.filter(room_id__in=ids, left_at__isnull=True)
            .select_related("user").order_by("created_at"))
    member_agent_names = agent_display_names({r.user_id for r in rows})
    for row in rows:
        avatar_map.setdefault(row.room_id, [])
        if row.user.avatar_url and len(avatar_map[row.room_id]) < 4:
            avatar_map[row.room_id].append(row.user.avatar_url)
        # 방 머리 시계 줄의 재료. 한 번에 모아 두지 않으면 방 목록 하나에
        # 참여자 수만큼 쿼리가 더 나갑니다.
        member_map.setdefault(row.room_id, []).append({
            "id": str(row.user_id),
            "name": row.user.name,
            "avatar_url": row.user.avatar_url or "",
            "timezone": row.user.timezone,
            "country": country_of(row.user.timezone),
            "presence": row.user.presence,
            "agent_name": member_agent_names.get(row.user_id, ""),
            "is_me": row.user_id == user.id,
        })

    # 대리인 방 이름은 **저장된 title 이 아니라 주인의 지금 호칭**입니다.
    #
    # 개인 설정에서 대리인 이름을 바꿨는데 방 제목만 옛 이름으로 남으면,
    # 사용자는 이름이 저장되지 않은 줄 압니다. 방 제목을 그때그때 갈아 주는
    # 마이그레이션 대신 조회 시점에 맞춥니다 — 이 두 종류는 어차피 이름을
    # 못 바꾸는 방(`UNRENAMABLE`)이라 저장된 값을 지킬 이유가 없습니다.
    owner_ids = {r.agent_owner_id for r in rooms if r.agent_owner_id}

    return {"unread_map": unread_map, "important_map": important_map,
            "last_map": last_map, "avatar_map": avatar_map,
            "member_map": member_map,
            # 알림을 껐는지는 **보는 사람 기준**입니다. 한 사람이 껐다고 남의
            # 목록에서도 꺼지면 안 됩니다.
            "muted_map": {rid: bool(m.muted_at) for rid, m in memberships.items()},
            "agent_name_map": agent_display_names(owner_ids)}


def _has_unconfirmed_important(room, member, user):
    """
    `!` 뱃지 판정.

    **내가 확인하지 않은** 중요 메시지가 있는지 봅니다. 남이 확인한 건
    내 뱃지를 끄지 못합니다.
    """
    confirmed = MessageImportance.objects.filter(user=user, message__room=room)
    return (visible_messages(room, member)
            .filter(is_important=True, deleted_at__isnull=True)
            .exclude(id__in=confirmed.values("message_id"))
            .exists())


# ─────────────────────────────────────────── 사이드바 · 중요 · 후보
@api_view(["GET"])
def sidebar(request):
    """
    좌측 전체.

    팀 노드의 `unread_count` 는 하위 프로젝트·방을 **다 더한 값**입니다.
    접힌 상태에서도 뱃지를 그려야 하는데, 클라이언트가 트리를 순회해 더하면
    숨겨진 방을 빠뜨려 숫자가 어긋납니다.
    """
    user = request.user
    sync_all_group_rooms(user)

    rooms = list(ChatRoom.objects
                 .filter(memberships__user=user, memberships__left_at__isnull=True,
                         memberships__hidden_at__isnull=True)
                 .distinct().order_by("-last_message_at", "-created_at"))
    ctx = room_context(user, rooms)

    ai_room = next((r for r in rooms if r.type == RoomType.AI), None)
    my_agent_room = (RoomSummarySerializer(ai_room, context=ctx).data
                     if ai_room else None)

    # `중요 채팅` 은 **미확인** 중요 메시지가 남은 방만.
    important_rooms = [RoomSummarySerializer(r, context=ctx).data
                       for r in rooms if ctx["important_map"].get(r.id)]

    # 팀 → 프로젝트 → 방 트리
    team_ids = list(TeamMember.objects.filter(user=user).values_list("team_id", flat=True))
    teams = {t.id: t for t in Team.objects.filter(id__in=team_ids)}
    projects = list(Project.objects.filter(team_id__in=team_ids)
                    .filter(Q(members__user=user) |
                            Q(team__members__user=user,
                              team__members__team_role__in=ADMINS))
                    .distinct())

    by_team_room = {r.team_id: r for r in rooms if r.type == RoomType.TEAM}
    by_project_room = {r.project_id: r for r in rooms if r.type == RoomType.PROJECT}
    other_by_project = {}
    for r in rooms:
        if r.type in (RoomType.DIRECT, RoomType.PEER_AGENT) and r.project_id:
            other_by_project.setdefault(r.project_id, []).append(r)

    tree, total = [], 0
    for team_id, team in teams.items():
        team_room = by_team_room.get(team_id)
        team_unread = ctx["unread_map"].get(team_room.id, 0) if team_room else 0
        team_important = bool(ctx["important_map"].get(team_room.id)) if team_room else False

        project_nodes = []
        for p in [p for p in projects if p.team_id == team_id]:
            proom = by_project_room.get(p.id)
            sub = ([proom] if proom else []) + other_by_project.get(p.id, [])
            p_unread = sum(ctx["unread_map"].get(r.id, 0) for r in sub)
            p_important = any(ctx["important_map"].get(r.id) for r in sub)
            project_nodes.append({
                "project_id": str(p.id), "project_name": p.name,
                "group_chat_room_id": str(proom.id) if proom else None,
                # 방 요약을 함께 싣습니다. id 만 주면 「모두 채팅 바로가기」로
                # 연 방의 제목을 대화창이 한 번 더 읽어야 하고, 이 노드의
                # 미읽음 합계에서 단체방 몫을 뺄 수가 없어 사이드바를 통째로
                # 다시 읽게 됩니다.
                "group_chat_room": (RoomSummarySerializer(proom, context=ctx).data
                                    if proom else None),
                "unread_count": p_unread, "has_important": p_important,
                "rooms": RoomSummarySerializer(sub, many=True, context=ctx).data,
            })
            team_unread += p_unread
            team_important = team_important or p_important

        tree.append({
            "team_id": str(team_id), "team_name": team.name,
            "group_chat_room_id": str(team_room.id) if team_room else None,
            "group_chat_room": (RoomSummarySerializer(team_room, context=ctx).data
                                if team_room else None),
            "unread_count": team_unread, "has_important": team_important,
            "projects": project_nodes,
        })
        total += team_unread

    # 어느 팀에도 안 매달린 방(개인 채팅·AI)까지 합계에 넣습니다.
    hung = {r.id for r in rooms if r.type in (RoomType.TEAM, RoomType.PROJECT)}
    hung |= {r.id for rs in other_by_project.values() for r in rs}
    loose = [r for r in rooms if r.id not in hung]
    total += sum(ctx["unread_map"].get(r.id, 0) for r in loose)

    return Response({
        "my_agent_room": my_agent_room,
        "important_rooms": important_rooms,
        "teams": tree,
        "direct_rooms": RoomSummarySerializer(
            [r for r in loose if r.type != RoomType.AI], many=True, context=ctx).data,
        "total_unread": total,
    })


@api_view(["GET"])
def important(request):
    """
    상단 `중요 채팅` 섹션.

    **내가 확인한 메시지는 빠집니다.** 확인은 사용자별이므로 같은 메시지가
    한 사람에게는 남고 다른 사람에게는 사라지는 게 정상입니다.
    """
    user = request.user
    memberships = {m.room_id: m for m in
                   RoomMember.objects.filter(user=user, left_at__isnull=True)}
    confirmed = set(MessageImportance.objects.filter(user=user)
                    .values_list("message_id", flat=True))

    rows = (ChatMessage.objects
            .filter(room_id__in=list(memberships), is_important=True,
                    deleted_at__isnull=True)
            .exclude(id__in=confirmed)
            .select_related("room")
            .order_by("-sent_at")[:100])
    rows = [m for m in rows
            if not memberships[m.room_id].visible_from
            or m.sent_at >= memberships[m.room_id].visible_from]

    rooms = list({m.room.id: m.room for m in rows}.values())
    rctx = room_context(user, rooms)
    mctx = message_context(user, rows)
    return Response(listing([{
        "message": MessageSerializer(m, context=mctx).data,
        "room": RoomSummarySerializer(m.room, context=rctx).data,
    } for m in rows]))


@api_view(["GET"])
def candidates(request):
    """`새 채팅 생성 → 대화상대 선택` 모달. 팀별로 묶어서 내려줍니다."""
    query = (request.query_params.get("query") or "").strip()
    want_type = (request.query_params.get("type") or "").upper()

    team_ids = list(TeamMember.objects.filter(user=request.user)
                    .values_list("team_id", flat=True))
    teams = {t.id: t for t in Team.objects.filter(id__in=team_ids)}
    rows = (TeamMember.objects.filter(team_id__in=team_ids)
            .exclude(user=request.user).select_related("user"))
    if query:
        rows = rows.filter(user__name__icontains=query)

    grouped = {}
    for r in rows:
        entry = {"user_id": str(r.user_id), "name": r.user.name,
                 "avatar_url": r.user.avatar_url or None,
                 "has_agent": hasattr(r.user, "agent_settings")}
        # `동료의 AI 대리인` 방은 대리인이 있는 사람에게만 걸 수 있습니다.
        if want_type == RoomType.PEER_AGENT and not entry["has_agent"]:
            continue
        grouped.setdefault(r.team_id, []).append(entry)

    return Response({"teams": [
        {"team_id": str(tid), "team_name": teams[tid].name,
         "members": sorted(members, key=lambda m: m["name"])}
        for tid, members in grouped.items() if tid in teams]})


# ─────────────────────────────────────────── 방
@api_view(["GET", "POST"])
def rooms(request):
    if request.method == "GET":
        rows = list(ChatRoom.objects
                    .filter(memberships__user=request.user,
                            memberships__left_at__isnull=True,
                            memberships__hidden_at__isnull=True)
                    .distinct().order_by("-last_message_at", "-created_at"))
        ctx = room_context(request.user, rows)
        return Response(listing(RoomSummarySerializer(rows, many=True, context=ctx).data))

    rtype = (request.data.get("type") or "").upper()
    if rtype not in RoomType.values:
        raise BordoError("VALIDATION_ERROR",
                         "type 은 AI · DIRECT · TEAM · PROJECT · PEER_AGENT 중 하나입니다.",
                         details={"allowed": list(RoomType.values)})

    member_ids = request.data.get("member_ids") or []
    team_id = request.data.get("team_id")
    project_id = request.data.get("project_id")

    if rtype == RoomType.AI:
        return Response(_room_body(request.user, ensure_ai_room(request.user)), status=200)

    if rtype == RoomType.TEAM:
        if not team_id:
            raise BordoError("VALIDATION_ERROR", "TEAM 방에는 team_id 가 필요합니다.")
        m = team_membership(request.user, team_id)
        return Response(_room_body(request.user, ensure_team_room(m.team)), status=200)

    if rtype == RoomType.PROJECT:
        if not project_id:
            raise BordoError("VALIDATION_ERROR", "PROJECT 방에는 project_id 가 필요합니다.")
        project, _ = project_membership(request.user, project_id)
        return Response(_room_body(request.user, ensure_project_room(project)), status=200)

    # ── DIRECT · PEER_AGENT — 상대 1명 필요
    if len(member_ids) != 1:
        raise BordoError("VALIDATION_ERROR",
                         f"{rtype} 방에는 상대 한 명만 지정합니다.",
                         details={"member_ids": member_ids})
    other_id = member_ids[0]
    if str(other_id) == str(request.user.id):
        raise BordoError("VALIDATION_ERROR", "자기 자신과는 방을 만들 수 없습니다.")

    shared = TeamMember.objects.filter(
        user_id=other_id,
        team_id__in=TeamMember.objects.filter(user=request.user).values("team_id"))
    if not shared.exists():
        raise BordoError("TEAM_ACCESS_DENIED", "같은 팀에 속한 사람에게만 걸 수 있습니다.")

    from apps.accounts.models import User
    other = User.objects.filter(pk=other_id).first()
    if not other:
        raise BordoError("STATE_NOT_FOUND", "상대를 찾을 수 없습니다.")

    if rtype == RoomType.PEER_AGENT and not hasattr(other, "agent_settings"):
        raise BordoError("STATE_NOT_FOUND", "상대에게 AI 대리인이 없습니다.")

    key = (direct_key(request.user.id, other_id) if rtype == RoomType.DIRECT
           else peer_agent_key(request.user.id, other_id))
    existing = ChatRoom.objects.filter(type=rtype, dedupe_key=key).first()
    if existing:
        # 예전에 숨겼던 방이면 다시 띄웁니다. 새로 만들면 기록이 갈라집니다.
        RoomMember.objects.filter(room=existing, user=request.user).update(
            hidden_at=None, left_at=None)
        return Response(_room_body(request.user, existing), status=200)

    # 대리인 방 제목은 저장해 두되 목록에서는 조회 시점에 다시 만듭니다
    # (`RoomSummarySerializer.get_title`). 여기서도 같은 규칙을 써야 저장된 값과
    # 보이는 값이 처음부터 어긋나지 않습니다.
    title = (other.display_name if rtype == RoomType.DIRECT
             else agent_display_name(other))
    try:
        with transaction.atomic():
            room = ChatRoom.objects.create(
                type=rtype, dedupe_key=key, title=title,
                team_id=team_id or None, project_id=project_id or None,
                agent_owner=other if rtype == RoomType.PEER_AGENT else None,
                created_by=request.user)
            if team_id:
                room.team_name = Team.objects.get(pk=team_id).name
            if project_id:
                p = Project.objects.get(pk=project_id)
                room.project_name, room.team_name = p.name, p.team_name
            room.save(update_fields=["team_name", "project_name"])
            people = [request.user.id] if rtype == RoomType.PEER_AGENT \
                else [request.user.id, other.id]
            RoomMember.objects.bulk_create(
                [RoomMember(room=room, user_id=u) for u in people],
                ignore_conflicts=True)
    except IntegrityError:
        room = ChatRoom.objects.get(type=rtype, dedupe_key=key)
        return Response(_room_body(request.user, room), status=200)
    return Response(_room_body(request.user, room), status=201)


def _room_body(user, room):
    ctx = room_context(user, [room])
    return RoomSummarySerializer(room, context=ctx).data


@api_view(["GET", "PATCH", "DELETE"])
def room_detail(request, room_id):
    room, member = room_access(request.user, room_id)

    if request.method == "GET":
        return Response(_room_body(request.user, room))

    if request.method == "PATCH":
        if room.type in UNRENAMABLE:
            raise BordoError("CHAT_ROOM_TYPE_NOT_ALLOWED",
                             "상대 이름으로 표시되는 방은 이름을 바꿀 수 없습니다.",
                             details={"type": room.type})
        title = (request.data.get("title") or "").strip()
        if not title:
            raise BordoError("VALIDATION_ERROR", "title 은 비울 수 없습니다.")
        room.title = title
        room.save(update_fields=["title", "updated_at"])
        publish(room.project_id, "chat.room.updated",
                {"room_id": str(room.id), "title": room.title})
        return Response(_room_body(request.user, room))

    # ── DELETE — 종류마다 뜻이 다릅니다
    if room.type == RoomType.AI:
        raise BordoError("CHAT_ROOM_TYPE_NOT_ALLOWED",
                         "나의 AI 대리인 방은 나갈 수 없습니다.")
    now = timezone.now()
    if room.type in HIDE_ON_LEAVE:
        # 내 목록에서만 숨깁니다. 상대에게는 그대로 보입니다.
        member.hidden_at = now
        member.save(update_fields=["hidden_at", "updated_at"])
        return Response({"room_id": str(room.id), "action": "HIDDEN", "at": now})

    member.left_at = now
    member.save(update_fields=["left_at", "updated_at"])
    publish(room.project_id, "chat.room.left",
            {"room_id": str(room.id), "user_id": str(request.user.id)})
    return Response({"room_id": str(room.id), "action": "LEFT", "at": now})


@api_view(["GET", "POST"])
def room_members(request, room_id):
    """
    방 참여자 목록(GET) · 대화상대 추가(POST).

    ## 읽는 자리가 없어서 넣는 자리도 못 썼습니다

    사람을 넣는 주소와 내보내는 주소는 있는데 **누가 있는지 읽을 수가
    없었습니다.** 명단 없이 내보내기 화면을 만들 방법이 없어, 그 두 주소가
    화면에서 한 번도 안 불렸습니다.

    나간 사람은 뺍니다. 기록은 `left_at` 으로 남기지만 여기는 **지금 방에 있는
    사람**을 묻는 자리라, 섞으면 내보내기 목록에 이미 나간 사람이 뜹니다.

    방 목록의 `members[]` 와 같은 모양입니다. 한 방만 다시 물을 때 화면이
    다른 모양을 받으면 같은 줄을 두 번 만들어야 합니다.
    """
    room, _ = room_access(request.user, room_id)

    if request.method == "GET":
        rows = (RoomMember.objects.filter(room=room, left_at__isnull=True)
                .select_related("user").order_by("created_at"))
        names = agent_display_names([r.user_id for r in rows])
        return Response(listing([{
            "id": str(r.user_id),
            "name": r.user.name,
            "avatar_url": r.user.avatar_url or None,
            "timezone": r.user.timezone,
            "country": country_of(r.user.timezone),
            "presence": r.user.presence,
            "agent_name": names.get(r.user_id, ""),
            "is_me": r.user_id == request.user.id,
            "joined_at": r.created_at,
        } for r in rows]))

    if room.type not in GROUP_TYPES:
        raise BordoError("CHAT_ROOM_TYPE_NOT_ALLOWED",
                         "1:1 방에 사람을 더하면 기존 대화가 제3자에게 열립니다. "
                         "새 단체방을 만드십시오.",
                         details={"type": room.type})
    user_ids = request.data.get("user_ids") or []
    if not user_ids:
        raise BordoError("VALIDATION_ERROR", "user_ids 는 비울 수 없습니다.")

    scope_team_id = room.team_id or (room.project.team_id if room.project_id else None)
    allowed = set(map(str, TeamMember.objects.filter(team_id=scope_team_id,
                                                     user_id__in=user_ids)
                      .values_list("user_id", flat=True)))
    outsiders = [str(u) for u in user_ids if str(u) not in allowed]
    if outsiders:
        raise BordoError("TEAM_ACCESS_DENIED",
                         "팀 밖 사람은 넣을 수 없습니다. 팀에 먼저 초대하십시오.",
                         details={"not_in_team": outsiders}, status=409)

    now = timezone.now()
    added = []
    for uid in allowed:
        rm, created = RoomMember.objects.get_or_create(
            room=room, user_id=uid, defaults={"visible_from": now})
        if not created and rm.left_at:
            rm.left_at, rm.visible_from = None, now
            rm.save(update_fields=["left_at", "visible_from", "updated_at"])
            created = True
        if created:
            added.append(str(uid))
    publish(room.project_id, "chat.room.members_added",
            {"room_id": str(room.id), "user_ids": added})
    return Response({"room_id": str(room.id), "added_user_ids": added})


@api_view(["DELETE"])
def room_member_detail(request, room_id, user_id):
    room, member = room_access(request.user, room_id)
    if room.type not in GROUP_TYPES:
        raise BordoError("CHAT_ROOM_TYPE_NOT_ALLOWED", "단체방에서만 내보낼 수 있습니다.")

    scope_team_id = room.team_id or (room.project.team_id if room.project_id else None)
    me = team_membership(request.user, scope_team_id)
    if me.team_role not in ADMINS and room.created_by_id != request.user.id:
        raise BordoError("TEAM_ACCESS_DENIED", "개설자 또는 OWNER · ADMIN 만 내보낼 수 있습니다.")

    target = RoomMember.objects.filter(room=room, user_id=user_id,
                                       left_at__isnull=True).first()
    if not target:
        raise BordoError("STATE_NOT_FOUND", "이 방의 참여자가 아닙니다.")
    target.left_at = timezone.now()
    target.save(update_fields=["left_at", "updated_at"])
    publish(room.project_id, "chat.room.member_removed",
            {"room_id": str(room.id), "user_id": str(user_id)})
    return Response(status=204)


# ─────────────────────────────────────────── 메시지
@api_view(["GET", "POST"])
def messages(request, room_id):
    room, member = room_access(request.user, room_id)

    if request.method == "GET":
        return Response(_message_page(request, room, member))

    body = (request.data.get("body") or "").strip()
    attachment_ids = request.data.get("attachment_ids") or []
    if not body and not attachment_ids:
        raise BordoError("VALIDATION_ERROR",
                         "본문과 첨부가 모두 비어 있습니다.")

    client_id = (request.data.get("client_message_id") or "").strip()
    if client_id:
        dup = ChatMessage.objects.filter(room=room, client_message_id=client_id).first()
        if dup:
            # 같은 메시지를 두 번 보낸 것. 처음 결과를 그대로 돌려줍니다.
            return Response(MessageSerializer(
                dup, context=message_context(request.user, [dup])).data, status=200)

    question = _resolve_pending_question(request, room)

    with transaction.atomic():
        msg = ChatMessage.objects.create(
            room=room, sender=request.user, sender_name=request.user.display_name,
            body=body, client_message_id=client_id,
            is_important=bool(request.data.get("is_important", False)),
            pending_question=question)

        if attachment_ids:
            _attach(room, request.user, attachment_ids, msg)

        if question is not None:
            # 답변 대기 질문을 여기서 닫습니다. 채팅으로 답했는데 질문이 OPEN 으로
            # 남으면 브리핑 카드가 안 사라집니다.
            question.answer_body = body
            question.answered_at = timezone.now()
            question.save(update_fields=["answer_body", "answered_at", "updated_at"])

        touch(room, msg.sent_at)
        member.last_read_at = msg.sent_at
        member.save(update_fields=["last_read_at", "updated_at"])

    publish(room.project_id, "chat.message.created",
            {"room_id": str(room.id), "message_id": str(msg.id),
             "sender_id": str(request.user.id)})
    if question is not None:
        publish(room.project_id, "agent.question.answered",
                {"question_id": str(question.id), "message_id": str(msg.id)},
                user_id=question.asker_id)

    msg = ChatMessage.objects.prefetch_related("attachments").get(pk=msg.pk)
    return Response(MessageSerializer(
        msg, context=message_context(request.user, [msg])).data, status=201)


def _resolve_pending_question(request, room):
    """
    `답변 필요` 카드에서 넘어온 답인지 확인합니다.

    브리핑이 준 `chat_room_id` 로 채팅에 오면, 사용자 눈에는 그냥 답장인데
    서버에서는 질문 해결로 처리돼야 합니다.
    """
    qid = request.data.get("pending_question_id")
    if not qid:
        return None
    from apps.agent.models import PendingQuestion
    q = PendingQuestion.objects.filter(pk=qid, target_user=request.user).first()
    if not q:
        raise BordoError("STATE_NOT_FOUND", "질문을 찾을 수 없습니다.",
                         details={"pending_question_id": str(qid)})
    if q.answered_at:
        raise BordoError("DUPLICATE_EVENT", "이미 답변한 질문입니다.",
                         details={"answered_at": q.answered_at})
    return q


def _day_window(raw, tz, field="date"):
    """
    `YYYY-MM-DD` 하루의 시작과 끝.

    **요청한 사람의 시간대로 자릅니다.** 서버 `TIME_ZONE` 은 UTC 라 그것으로
    자르면 한국에서 자정 넘어 보낸 말이 전날로 묶입니다 — 화면은 브라우저
    시간대로 날짜 구분선을 그리므로, 그 구분선을 눌러도 그 메시지가 안 나옵니다.
    시간대가 다른 팀이 이 서비스의 전제라 UTC 고정은 답이 될 수 없습니다.
    """
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise BordoError("VALIDATION_ERROR", f"{field} 는 YYYY-MM-DD 입니다.")
    start = datetime.combine(day, time.min, tzinfo=tz)
    return day, start, start + timedelta(days=1)


def _message_page(request, room, member):
    """
    커서 페이징.

    `date` 와 `before` 는 같이 쓰지 못합니다. 달력에서 날짜를 고르는 것과
    위로 스크롤하는 것은 서로 다른 기준점이라, 둘을 겹치면 어느 쪽을 따를지
    서버·클라이언트 해석이 갈립니다.
    """
    date = request.query_params.get("date")
    before = request.query_params.get("before")
    if date and before:
        raise BordoError("VALIDATION_ERROR",
                         "date 와 before 는 함께 쓸 수 없습니다. "
                         "날짜 이동은 date, 이어보기는 before 입니다.")

    qs = visible_messages(room, member)
    if date:
        _, start, end = _day_window(date, user_tz(request.user))
        qs = qs.filter(sent_at__gte=start, sent_at__lt=end)
        rows = list(qs.order_by("sent_at"))
        return {"results": MessageSerializer(
            rows, many=True, context=message_context(request.user, rows)).data,
            "next_before": None,
            "has_older": visible_messages(room, member).filter(
                sent_at__lt=start).exists(),
            "has_newer": visible_messages(room, member).filter(
                sent_at__gte=end).exists()}

    rows, next_before = cursor_page(
        qs, before=before, limit=request.query_params.get("limit"),
        order_field="-sent_at")
    rows = list(reversed(rows))            # 화면은 오래된 것부터 그립니다
    return {"results": MessageSerializer(
        rows, many=True, context=message_context(request.user, rows)).data,
        "next_before": next_before,
        "has_older": next_before is not None,
        "has_newer": False}


@api_view(["PATCH", "DELETE"])
def message_detail(request, message_id):
    msg = (ChatMessage.objects.filter(pk=message_id)
           .select_related("room").first())
    if not msg:
        raise BordoError("STATE_NOT_FOUND", "메시지를 찾을 수 없습니다.")
    room, member = room_access(request.user, msg.room_id)

    if request.method == "PATCH":
        if msg.sender_id != request.user.id:
            raise BordoError("TEAM_ACCESS_DENIED", "본인 메시지만 수정할 수 있습니다.")
        if msg.is_agent:
            raise BordoError("CHAT_ROOM_TYPE_NOT_ALLOWED",
                             "대리인 발언은 판정 근거에 묶인 감사 대상이라 고칠 수 없습니다.")
        if msg.deleted_at:
            raise BordoError("STATE_NOT_FOUND", "삭제된 메시지입니다.")
        window = timedelta(minutes=dj_settings.BORDO["CHAT_EDIT_WINDOW_MINUTES"])
        if timezone.now() - msg.sent_at > window:
            raise BordoError("CHAT_EDIT_WINDOW_EXPIRED",
                             details={"window_minutes":
                                      dj_settings.BORDO["CHAT_EDIT_WINDOW_MINUTES"]})
        body = (request.data.get("body") or "").strip()
        if not body:
            raise BordoError("VALIDATION_ERROR", "body 는 비울 수 없습니다.")
        msg.body, msg.edited_at = body, timezone.now()
        msg.save(update_fields=["body", "edited_at"])
        publish(room.project_id, "chat.message.updated",
                {"room_id": str(room.id), "message_id": str(msg.id)})
        return Response(MessageSerializer(
            msg, context=message_context(request.user, [msg])).data)

    # ── DELETE — 내용만 비웁니다
    if msg.sender_id != request.user.id:
        scope_team_id = room.team_id or (room.project.team_id if room.project_id else None)
        if not scope_team_id:
            raise BordoError("TEAM_ACCESS_DENIED", "본인 메시지만 삭제할 수 있습니다.")
        me = team_membership(request.user, scope_team_id)
        if me.team_role not in ADMINS:
            raise BordoError("TEAM_ACCESS_DENIED", "본인 메시지만 삭제할 수 있습니다.")
    if msg.deleted_at:
        return Response(MessageSerializer(
            msg, context=message_context(request.user, [msg])).data)

    with transaction.atomic():
        msg.deleted_at = timezone.now()
        msg.body = ""
        msg.is_important = False           # 중요 목록에서도 같이 빠집니다
        msg.save(update_fields=["deleted_at", "body", "is_important"])
        MessageImportance.objects.filter(message=msg).delete()
        ChatAttachment.objects.filter(message=msg).delete()
    publish(room.project_id, "chat.message.deleted",
            {"room_id": str(room.id), "message_id": str(msg.id)})
    return Response(MessageSerializer(
        msg, context=message_context(request.user, [msg])).data)


@api_view(["POST"])
def read(request, room_id):
    """`up_to_message_id` 까지 읽음. 생략하면 방 전체."""
    room, member = room_access(request.user, room_id)
    up_to = request.data.get("up_to_message_id")
    if up_to:
        anchor = ChatMessage.objects.filter(pk=up_to, room=room).first()
        if not anchor:
            raise BordoError("STATE_NOT_FOUND", "그 메시지는 이 방에 없습니다.")
        mark = anchor.sent_at
    else:
        mark = timezone.now()

    # 워터마크는 앞으로만 갑니다. 뒤로 돌리면 읽은 게 다시 안 읽음이 됩니다.
    if member.last_read_at is None or mark > member.last_read_at:
        member.last_read_at = mark
        member.save(update_fields=["last_read_at", "updated_at"])

    mine = RoomMember.objects.filter(user=request.user, left_at__isnull=True,
                                     hidden_at__isnull=True).select_related("room")
    total = sum(unread_count(m.room, m) for m in mine)
    return Response({"room_id": str(room.id),
                     "unread_count": unread_count(room, member),
                     "total_unread": total})


# ─────────────────────────────────────────── 중요 표시 · 확인
@api_view(["PATCH"])
def message_important(request, message_id):
    msg = ChatMessage.objects.filter(pk=message_id).select_related("room").first()
    if not msg:
        raise BordoError("STATE_NOT_FOUND", "메시지를 찾을 수 없습니다.")
    room, _ = room_access(request.user, msg.room_id)
    if msg.deleted_at:
        raise BordoError("STATE_NOT_FOUND", "삭제된 메시지입니다.")
    if "is_important" not in request.data:
        raise BordoError("VALIDATION_ERROR", "is_important 는 필수입니다.")

    msg.is_important = bool(request.data["is_important"])
    msg.save(update_fields=["is_important"])
    if not msg.is_important:
        # 표시를 내리면 확인 기록도 의미가 없어집니다.
        MessageImportance.objects.filter(message=msg).delete()
    publish(room.project_id, "chat.message.important_changed",
            {"room_id": str(room.id), "message_id": str(msg.id),
             "is_important": msg.is_important})
    return Response(MessageSerializer(
        msg, context=message_context(request.user, [msg])).data)


@api_view(["POST"])
def message_important_confirm(request, message_id):
    """
    `중요하시죠 - 확인되면 회색으로 - 상단 중요 채팅에서 제외됨`.

    표시를 내리는 것과 다릅니다. `is_important` 는 true 로 남고 **내 확인 기록만**
    생깁니다. 나중에 중요 메시지 이력을 되짚을 수 있어야 하기 때문입니다.
    """
    msg = ChatMessage.objects.filter(pk=message_id).select_related("room").first()
    if not msg:
        raise BordoError("STATE_NOT_FOUND", "메시지를 찾을 수 없습니다.")
    room_access(request.user, msg.room_id)
    if not msg.is_important:
        raise BordoError("VALIDATION_ERROR", "중요 표시가 안 된 메시지입니다.")
    MessageImportance.objects.get_or_create(message=msg, user=request.user)
    return Response(MessageSerializer(
        msg, context=message_context(request.user, [msg])).data)


# ─────────────────────────────────────────── 첨부
@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def attachments(request, room_id):
    """
    업로드만 하고 아직 메시지에 안 붙은 상태입니다.

    `expires_at` 을 두는 이유 — 보내다 만 파일이 영원히 남으면 안 됩니다.
    전송에 성공해야 `ATTACHED` 로 넘어가고 만료가 풀립니다.
    """
    room, _ = room_access(request.user, room_id)
    f = request.FILES.get("file")
    if not f:
        raise BordoError("VALIDATION_ERROR", "file 이 필요합니다.")

    # 같은 파일을 두 번 올리지 않습니다.
    #
    # 화면이 만들어 보낸 열쇠로 이미 올라간 것을 찾으면 그것을 그대로
    # 돌려줍니다. 없으면 400 을 내지 않고 그냥 새로 올립니다 — 이 키를
    # 모르는 클라이언트가 업로드를 통째로 못 하게 되면 안 됩니다.
    upload_key = str(request.data.get("client_upload_id") or "").strip()[:64]
    if upload_key:
        seen = ChatAttachment.objects.filter(uploader=request.user,
                                             client_upload_id=upload_key).first()
        if seen is not None:
            return Response(AttachmentSerializer(seen).data, status=200)

    limit = dj_settings.CHAT_ATTACHMENT_MAX_BYTES
    if f.size > limit:
        # 상한이 없으면 실수로 올린 큰 파일 하나가 디스크를 채우고, 그때부터
        # 모든 업로드가 함께 실패합니다.
        raise BordoError("VALIDATION_ERROR",
                         f"{limit // (1024 * 1024)}MB 를 넘는 파일은 올릴 수 없습니다.",
                         details={"size_bytes": f.size, "limit_bytes": limit})

    mime = getattr(f, "content_type", "") or ""
    att = ChatAttachment(
        room=room, uploader=request.user, name=f.name[:255], size_bytes=f.size,
        mime_type=mime,
        kind=(ChatAttachment.Kind.IMAGE if mime.startswith("image/")
              else ChatAttachment.Kind.FILE),
        client_upload_id=upload_key,
        expires_at=timezone.now() + timedelta(hours=ATTACHMENT_TTL_HOURS))

    # 파일 이름을 저장 경로에 쓰지 않습니다.
    #
    # 올린 이름에 `../` 가 들어오면 저장소 밖으로 빠져나가고, 한글·공백이 섞이면
    # 운영체제마다 다르게 저장됩니다. 같은 이름을 두 번 올리면 앞의 것을
    # 덮어써 다른 사람 첨부가 사라집니다. 보여줄 이름은 `name` 에 따로 있습니다.
    att.stored_path = default_storage.save(
        f"chat/{room.id}/{att.id}{Path(f.name).suffix[:16]}", f)
    att.url = f"/api/v1/chat/attachments/{att.id}/download"
    att.save()
    return Response(AttachmentSerializer(att).data, status=201)


def _attach(room, user, attachment_ids, message):
    rows = list(ChatAttachment.objects.filter(
        id__in=attachment_ids, room=room, uploader=user))
    found = {str(a.id) for a in rows}
    missing = [str(a) for a in attachment_ids if str(a) not in found]
    if missing:
        raise BordoError("STATE_NOT_FOUND", "첨부를 찾을 수 없습니다.",
                         details={"attachment_ids": missing})
    already = [str(a.id) for a in rows if a.message_id]
    if already:
        raise BordoError("DUPLICATE_EVENT", "이미 다른 메시지에 붙은 첨부입니다.",
                         details={"attachment_ids": already})
    expired = [str(a.id) for a in rows if a.status == ChatAttachment.Status.EXPIRED]
    if expired:
        raise BordoError("STATE_NOT_FOUND", "만료된 첨부입니다. 다시 올리십시오.",
                         details={"attachment_ids": expired})
    ChatAttachment.objects.filter(id__in=[a.id for a in rows]).update(
        message=message, status=ChatAttachment.Status.ATTACHED, expires_at=None)


@api_view(["DELETE"])
def attachment_detail(request, attachment_id):
    att = ChatAttachment.objects.filter(pk=attachment_id).first()
    if not att:
        raise BordoError("STATE_NOT_FOUND", "첨부를 찾을 수 없습니다.")
    room_access(request.user, att.room_id)
    if att.uploader_id != request.user.id:
        raise BordoError("TEAM_ACCESS_DENIED", "본인이 올린 첨부만 지울 수 있습니다.")
    if att.message_id:
        raise BordoError("REFERENCED_BY_OTHERS",
                         "이미 전송된 첨부입니다. 메시지를 지우면 함께 사라집니다.",
                         details={"message_id": str(att.message_id)})
    _drop_file(att)
    att.delete()
    return Response(status=204)


def _drop_file(att) -> None:
    """
    실제 파일도 지웁니다.

    행만 지우면 디스크에는 남아 주소를 아는 사람이 계속 받을 수 있습니다.
    첨부는 하드 삭제 대상이라 되돌릴 자리도 없습니다.
    """
    if not att.stored_path:
        return
    try:
        default_storage.delete(att.stored_path)
    except Exception:                                          # noqa: BLE001
        # 파일이 이미 없어도 행 삭제는 끝내야 합니다. 여기서 막히면 화면에는
        # 지워지지 않은 첨부가 계속 남습니다.
        logger.warning("첨부 파일 삭제 실패 attachment=%s path=%s",
                       att.id, att.stored_path)


@api_view(["GET"])
def attachment_download(request, attachment_id):
    """
    첨부 내려받기.

    ## 왜 `/media/` 로 그냥 안 여는가

    주소만 알면 누구나 받게 됩니다. 채팅 첨부는 **그 방 참여자만** 봐야 하므로
    권한을 보는 자리를 지납니다. 참여자가 아니면 404 입니다 — 403 을 주면
    "그런 파일이 있긴 하다" 가 샙니다.
    """
    att = ChatAttachment.objects.filter(pk=attachment_id).first()
    if not att or not att.stored_path:
        raise BordoError("STATE_NOT_FOUND", "첨부를 찾을 수 없습니다.")
    room_access(request.user, att.room_id)

    if not default_storage.exists(att.stored_path):
        raise BordoError("STATE_NOT_FOUND", "파일이 저장소에 없습니다.")

    return FileResponse(default_storage.open(att.stored_path, "rb"),
                        as_attachment=True, filename=att.name)


# ─────────────────────────────────────────── 달력 · 요약 · 검색
@api_view(["GET"])
def active_dates(request, room_id):
    """달력에서 `채팅한 날짜만 검은 색`. 없는 날은 못 누르게 합니다."""
    room, member = room_access(request.user, room_id)
    # 달의 경계도 보는 사람 기준입니다. 서버 시간대로 자르면 월초·월말 하루가
    # 옆 달로 넘어가, 달력에서 그 날만 못 누릅니다.
    tz = user_tz(request.user)
    month = (request.query_params.get("month")
             or timezone.localtime(timezone=tz).strftime("%Y-%m"))
    try:
        first = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    except ValueError:
        raise BordoError("VALIDATION_ERROR", "month 는 YYYY-MM 입니다.")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)

    start = datetime.combine(first, time.min, tzinfo=tz)
    end = datetime.combine(nxt, time.min, tzinfo=tz)
    qs = visible_messages(room, member)

    dates = sorted({s.astimezone(tz).date().isoformat() for s in
                    qs.filter(sent_at__gte=start, sent_at__lt=end)
                    .values_list("sent_at", flat=True)})
    return Response({
        "month": month,
        "active_dates": dates,
        "has_prev_month": qs.filter(sent_at__lt=start).exists(),
        "has_next_month": qs.filter(sent_at__gte=end).exists(),
    })


@api_view(["GET"])
def daily_summary(request, room_id):
    """
    `2026년 8월 9일 채팅 요약`.

    `one_line` 과 `my_todos` 는 AI 산출물이라 1차에서는 빈 값으로 나갑니다.
    빈 값과 `아직 생성 안 됨` 을 구분할 수 있도록 `generated_at` 을 같이 줍니다 —
    화면이 `요약 준비 중` 과 `요약할 게 없음` 을 다르게 그려야 합니다.
    """
    room, member = room_access(request.user, room_id)
    tz = user_tz(request.user)
    raw = (request.query_params.get("date")
           or timezone.localtime(timezone=tz).date().isoformat())
    day, start, end = _day_window(raw, tz)

    row = DailyChatSummary.objects.filter(room=room, date=day).first()
    if row:
        body = DailySummarySerializer(row).data
    else:
        body = {"date": raw, "one_line": "", "my_todos": [], "schedules": [],
                "generated_at": None}

    body["message_count"] = visible_messages(room, member).filter(
        sent_at__gte=start, sent_at__lt=end).count()
    body["status"] = "READY" if row and row.generated_at else "PENDING"
    return Response(body)


@api_view(["GET"])
def search(request, room_id):
    """
    방 안 검색.

    결과를 누르면 그 위치로 이동해야 하므로 `date` 를 같이 줍니다 —
    클라이언트가 `date` 로 다시 목록을 불러 해당 메시지로 스크롤합니다.
    """
    room, member = room_access(request.user, room_id)
    q = (request.query_params.get("q") or "").strip()
    if not q:
        return Response(listing([]))
    rows = (visible_messages(room, member)
            .filter(body__icontains=q, deleted_at__isnull=True)
            .order_by("-sent_at")[:100])
    rows = list(rows)
    ctx = message_context(request.user, rows)
    # 결과를 누르면 이 `date` 로 목록을 다시 부릅니다. `?date=` 를 자르는 기준과
    # 같은 시간대로 찍어야 눌렀을 때 그 메시지가 있는 날이 열립니다.
    tz = user_tz(request.user)
    return Response(listing([{
        "message": MessageSerializer(m, context=ctx).data,
        "date": m.sent_at.astimezone(tz).date().isoformat(),
    } for m in rows]))


@api_view(["GET"])
def away_handled(request):
    """
    자리를 비운 사이 **내 대리인이 대신 받은 대화.**

    좌측 목록의 `중요 채팅` 자리를 이것으로 바꿉니다. 그쪽은 내가 미리 별을
    찍어 둔 것만 모이는데, 자리를 비우기 전에 무엇이 중요해질지 알 수 있으면
    애초에 자리를 안 비웁니다. 돌아와서 먼저 봐야 하는 것은 없는 동안 오간
    말입니다.

    **방 기준으로 묶어 개수까지 셉니다.** 화면이 방마다 메시지를 받아 세게
    두면 목록 하나 그리려고 방 수만큼 요청이 나갑니다.

    `is_agent` 로만 거르지 않습니다 — 옆에서 시켜서 한 말도 대리인이 보낸
    것이라, 그것까지 섞이면 무엇을 확인해야 하는지가 흐려집니다.
    """
    user = request.user
    rows = (ChatMessage.objects
            .filter(sender=user, is_agent=True, answered_while_away=True,
                    deleted_at__isnull=True,
                    room__memberships__user=user,
                    room__memberships__left_at__isnull=True)
            .select_related("room").order_by("-sent_at"))

    grouped = {}
    for m in rows:
        slot = grouped.setdefault(m.room_id, {"room": m.room, "count": 0, "last": m})
        slot["count"] += 1

    ctx = room_context(user, [g["room"] for g in grouped.values()])
    results = []
    for slot in sorted(grouped.values(), key=lambda g: g["last"].sent_at, reverse=True):
        room, last = slot["room"], slot["last"]
        body = RoomSummarySerializer(room, context=ctx).data
        results.append({
            "room_id": str(room.id),
            "title": body["title"],
            "path_label": body.get("path_label") or "",
            "handled_count": slot["count"],
            "last_reply": {"id": str(last.id),
                           "preview": last.body[:80] or "(첨부)",
                           "sent_at": last.sent_at},
        })
    return Response(listing(results))


@api_view(["PATCH"])
def room_mute(request, room_id):
    """
    이 방 알림 끄기·켜기.

    **방 나가기와 다릅니다.** 나가면 목록에서 사라지고 새 메시지도 안 보이는데,
    알림만 끄는 것은 대화는 계속 보되 소리로 부르지 말라는 뜻입니다.

    미읽음 수는 그대로 셉니다. 안 세면 "알림을 껐다" 와 "다 읽었다" 가 화면에서
    구별되지 않습니다.
    """
    room, member = room_access(request.user, room_id)
    want = request.data.get("muted")
    if want is None:
        raise BordoError("VALIDATION_ERROR", "muted 는 필수입니다.")

    now = timezone.now()
    member.muted_at = now if want else None
    member.save(update_fields=["muted_at", "updated_at"])
    return Response({"room_id": str(room.id), "muted": bool(member.muted_at),
                     "muted_at": member.muted_at})


@api_view(["GET"])
def room_search(request):
    """
    방 찾기. **방 하나 안의 메시지를 찾는 `rooms/{id}/search` 와 다릅니다.**

    사이드바 돋보기가 쓰던 것은 검색이 아니라 이미 받아 온 목록을 좁히는
    것이었습니다(프론트 `ChatListPanel`). 방이 늘면 목록에 안 실린 방은 아무리
    쳐도 안 나옵니다.

    ## 무엇으로 찾는가

    화면이 좁히던 것과 **같은 셋**입니다 — 방 이름 · 팀/프로젝트 이름 ·
    최근 메시지. 서버가 다른 기준으로 찾으면 같은 글자를 쳤는데 목록을 좁힐
    때와 검색할 때 결과가 달라집니다.

    ## 대리인 방 이름은 저장값이 아닙니다

    `{이름}의 Bordo` 는 조회할 때 조립합니다(`agent_display_name`). 저장된
    제목으로만 찾으면 화면에 보이는 이름을 그대로 쳤는데 안 걸립니다.
    그래서 그 방들만 파이썬에서 한 번 더 봅니다 — 사람당 하나뿐이라 양이
    적습니다.

    ## 안 보이는 방은 안 찾습니다

    나갔거나(`left_at`) 목록에서 숨긴(`hidden_at`) 방은 뺍니다. 나중에 초대된
    사람은 입장 이후 메시지만 보므로, 메시지로 찾을 때도 그 규칙을 그대로
    지납니다 — 안 그러면 검색 결과로 못 볼 대화가 새어 나갑니다.
    """
    q = (request.query_params.get("q") or "").strip()
    if not q:
        raise BordoError("VALIDATION_ERROR", "q 는 비울 수 없습니다.")

    memberships = {m.room_id: m for m in
                   RoomMember.objects.filter(user=request.user,
                                             left_at__isnull=True,
                                             hidden_at__isnull=True)}
    if not memberships:
        return Response(listing([]))

    mine = list(ChatRoom.objects.filter(id__in=list(memberships))
                .order_by("-last_message_at", "-created_at"))

    by_name = {r.id for r in mine
               if q.lower() in " ".join(filter(None, [r.title, r.team_name,
                                                      r.project_name])).lower()}

    # 대리인 방은 조립된 이름으로 한 번 더 봅니다.
    owner_ids = {r.agent_owner_id for r in mine if r.agent_owner_id}
    agent_names = agent_display_names(owner_ids)
    by_name |= {r.id for r in mine
                if r.agent_owner_id
                and q.lower() in (agent_names.get(r.agent_owner_id) or "").lower()}

    by_message = set()
    for row in (ChatMessage.objects
                .filter(room_id__in=list(memberships), body__icontains=q,
                        deleted_at__isnull=True)
                .values("room_id", "sent_at")):
        member = memberships[row["room_id"]]
        if member.visible_from and row["sent_at"] < member.visible_from:
            continue
        by_message.add(row["room_id"])

    hit = by_name | by_message
    rows = [r for r in mine if r.id in hit]
    ctx = room_context(request.user, rows)
    body = RoomSummarySerializer(rows, many=True, context=ctx).data
    for item, room in zip(body, rows):
        # 왜 걸렸는지 알려 줍니다. 이름이 안 겹치는데 목록에 뜨면 화면이
        # 「왜 이 방이 나왔지」 를 설명할 방법이 없습니다.
        item["matched"] = ("NAME" if room.id in by_name else "MESSAGE")
    return Response(listing(body))
