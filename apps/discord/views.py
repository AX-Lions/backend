"""
`/internal/v1` — Discord 봇 전용.

## 이 진입점의 성격

봇은 **판단하지 않고 릴레이합니다.** 그래서 여기 뷰들은 받은 것을 그대로 저장하고,
해석은 대리인(`apps/agent`)에게 넘깁니다.

## 원본을 지우지 않습니다

회의 발언은 `Utterance` 로 **원문 그대로** 쌓입니다. 요약은 백엔드가 종료 시점에
따로 만듭니다.

봇이 요약해서 보내면 그 과정에서 빠진 내용이 영영 사라집니다. 요약에서 생략된
한 줄이 누군가에게는 중요할 수 있고, 나중에 대리인이 사람마다 다르게 정리해
주려면 원본이 있어야 합니다. (2026-08-15 회의 결정)

## 멱등

같은 발언이 두 번 들어와도 한 번만 처리합니다. 봇이 재시도하거나 Discord 가
같은 이벤트를 두 번 보내는 일이 실제로 있습니다.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.views import listing
from config.errors import BordoError

from .auth import service_token_required
from .models import GuildLink, LinkCode

logger = logging.getLogger("bordo.discord")

#: 봇이 가져간 뒤 이 시간 동안은 다시 안 보입니다.
#:
#: 게시에 걸리는 시간보다 넉넉해야 같은 것이 두 번 나가지 않고, 봇이 죽었을 때
#: 다시 잡히는 시간보다는 짧아야 합니다. Discord 게시는 보통 1초 안입니다.
VISIBILITY_TIMEOUT_SEC = 60

#: `/meeting-start` 자동완성이 보여줄 구간과 개수.
#
# 지난달 회의까지 뜨면 고를 수가 없습니다. 지금 시각 언저리만 봅니다.
SCHEDULED_PAST_HOURS = 1
SCHEDULED_AHEAD_HOURS = 6
SCHEDULED_LIMIT = 25

def internal(methods):
    """`@api_view` + 인증 해제 + 서비스 토큰 검사를 한 번에."""
    def deco(fn):
        fn = service_token_required(fn)
        fn = permission_classes([])(fn)
        fn = authentication_classes([])(fn)
        fn = api_view(methods)(fn)
        return fn
    return deco


def _require(value, field: str) -> str:
    """
    빈 값을 조회에 넣지 않습니다.

    미연동 사용자는 `discord_user_id=""` 로 저장되고, 웹에서 만든 회의는
    `discord_channel_id=""` 입니다. 빈 문자열로 조회하면 **"못 찾음" 이 아니라
    아무 행이나 잡힙니다** — 남의 대리인이 답하거나 남의 회의가 종료됩니다.

    호출부마다 가드를 두는 대신 여기서 막습니다. 조회를 하나 더 추가할 때
    가드를 빠뜨리는 일이 없어야 합니다.
    """
    v = str(value or "").strip()
    if not v:
        raise BordoError("VALIDATION_ERROR", f"{field} 는 필수입니다.")
    return v


def _team_of(guild_id: str):
    guild_id = _require(guild_id, "guild_id")
    link = GuildLink.objects.filter(guild_id=guild_id).select_related("team").first()
    if link is None:
        raise BordoError("TEAM_NOT_FOUND",
                         "이 Discord 서버에 연결된 팀이 없습니다. /bordo-team-connect 로 먼저 연결하십시오.")
    return link.team


def _user_of(discord_user_id: str):
    from apps.accounts.models import User
    discord_user_id = _require(discord_user_id, "discord_user_id")
    user = User.objects.filter(discord_user_id=discord_user_id).first()
    if user is None:
        raise BordoError("USER_NOT_FOUND",
                         "연결된 계정이 없습니다. /bordo-connect 로 먼저 연결하십시오.")
    return user


# ═══════════════════════════════════════════ 계정 · 팀

@internal(["POST"])
def connect_code(request):
    """
    계정 연결 코드를 발급합니다. 봇이 DM 으로 전달합니다.

    **코드는 서버가 만듭니다.** 봇이 만들면 서버가 그 값을 믿을 근거가 없습니다.
    """
    discord_user_id = _require(request.data.get("discord_user_id"),
                               "discord_user_id")

    # 이전에 발급한 살아 있는 코드는 무효로 만듭니다. 여러 개가 동시에 살아 있으면
    # 어느 것이 최신인지 사용자가 알 수 없습니다.
    LinkCode.objects.filter(discord_user_id=discord_user_id,
                            used_at=None).update(used_at=timezone.now())

    # 6자리라 드물게 겹칩니다. 그때 500 을 내면 사용자는 이유를 알 수 없습니다.
    row = None
    for _ in range(5):
        try:
            row = LinkCode.objects.create(
                code=secrets.token_hex(3).upper(),
                discord_user_id=discord_user_id,
                guild_id=str(request.data.get("guild_id") or ""),
                expires_at=timezone.now() + timezone.timedelta(
                    minutes=LinkCode.TTL_MINUTES),
            )
            break
        except IntegrityError:
            continue
    if row is None:
        raise BordoError("INTERNAL_ERROR",
                         "코드 발급에 실패했습니다. 다시 시도해 주십시오.")
    return Response({"code": row.code,
                     "expires_at": row.expires_at.isoformat(),
                     "ttl_minutes": LinkCode.TTL_MINUTES}, status=201)


@internal(["GET"])
def teams_current(request):
    """이 Discord 서버가 어느 팀에 묶여 있는지."""
    guild_id = str(request.query_params.get("guild_id") or "").strip()
    discord_user_id = str(request.query_params.get("discord_user_id") or "").strip()

    if guild_id:
        team = _team_of(guild_id)
        return Response({"team_id": str(team.id), "name": team.name,
                         "linked": True})

    # guild_id 가 없으면 사용자 기준으로 답합니다. 봇이 DM 에서 부르는 경우입니다.
    if not discord_user_id:
        raise BordoError("VALIDATION_ERROR", "guild_id 또는 discord_user_id 가 필요합니다.")

    from apps.orgs.models import TeamMember
    user = _user_of(discord_user_id)
    rows = (TeamMember.objects.filter(user=user).select_related("team")
            .order_by("joined_at"))
    return Response({"linked": bool(rows),
                     "teams": [{"team_id": str(m.team_id), "name": m.team.name,
                                "role": m.team_role} for m in rows]})


@internal(["POST"])
def teams_link(request):
    """
    이 Discord 서버를 팀에 연결합니다.

    ## 왜 연결 코드를 따로 두지 않는가

    계정 연결에는 코드가 필요했습니다. Discord 계정과 Bordo 계정이 **서로를 모르니**
    둘을 잇는 증거가 있어야 했습니다.

    팀 연결에는 필요 없습니다. 이미 이어진 계정으로 부르므로 서버가 그 사람이 팀
    OWNER 인지 압니다. Discord 서버 권한은 봇이 슬래시 명령에서 확인합니다.
    **두 증거가 이미 확보돼 있어 코드를 한 번 더 주고받을 이유가 없습니다.**

    ## 봇이 반드시 해야 하는 것

    `manage_guild` 권한 확인입니다. 백엔드는 Discord 권한을 알 수 없어, 이걸 봇이
    안 하면 **아무나 이 서버를 남의 팀에 붙일 수 있습니다.**
    """
    from apps.orgs.models import TeamMember, TeamRole

    guild_id = _require(request.data.get("guild_id"), "guild_id")
    user = _user_of(request.data.get("discord_user_id"))

    # 팀을 만들거나 관리할 수 있는 사람만 연결합니다. 일반 멤버가 붙이면
    # 팀 전체의 회의가 그 서버로 흘러갑니다.
    memberships = list(TeamMember.objects
                       .filter(user=user, team_role__in=[TeamRole.OWNER, TeamRole.ADMIN])
                       .select_related("team"))
    if not memberships:
        raise BordoError("TEAM_ACCESS_DENIED",
                         "팀의 소유자 또는 관리자만 서버를 연결할 수 있습니다.")

    team_id = str(request.data.get("team_id") or "").strip()
    if team_id:
        chosen = next((m for m in memberships if str(m.team_id) == team_id), None)
        if chosen is None:
            raise BordoError("TEAM_ACCESS_DENIED", "그 팀의 소유자 또는 관리자가 아닙니다.")
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        # 어느 팀인지 서버가 정하면 안 됩니다. 잘못 고르면 남의 팀 회의가
        # 이 서버로 흘러가고, 되돌려도 그 사이 오간 발언은 남습니다.
        raise BordoError(
            "TEAM_AMBIGUOUS", "연결할 팀을 골라 주십시오.",
            details={"teams": [{"team_id": str(m.team_id), "name": m.team.name}
                               for m in memberships]})

    link, created = GuildLink.objects.update_or_create(
        guild_id=guild_id,
        defaults=dict(team=chosen.team, linked_by=user))

    return Response({"team_id": str(link.team_id), "name": chosen.team.name,
                     "created": created}, status=201 if created else 200)


# ═══════════════════════════════════════════ 대리 참석

def _queue_debate_points(meeting_ids) -> None:
    """
    논쟁점 예측을 걸어 둡니다. **기다리지 않습니다.**

    여기서 터지면 대리 참석 토글 자체가 실패한 것으로 읽히는데, 상태는 이미
    저장돼 있습니다. 봇은 "안 켜졌다" 로 읽고 사용자에게 다시 하라고 합니다.
    """
    from apps.agent.tasks import build_debate_points

    for meeting_id in meeting_ids:
        try:
            build_debate_points.delay(str(meeting_id))
        except Exception:                                      # noqa: BLE001
            logger.exception("논쟁점 생성 호출 실패 meeting=%s", meeting_id)


def _set_delegate(request, on: bool):
    from apps.meetings.models import Attendance, MeetingParticipant, MeetingStatus

    user = _user_of(request.data.get("discord_user_id"))

    # 진행 중이거나 아직 안 열린 회의에만 적용합니다. 끝난 회의의 참석 상태를
    # 뒤늦게 바꾸면 그때 대리인이 왜 답했는지 기록과 어긋납니다.
    #
    # `CONFIRMED` 를 빠뜨리고 있었습니다 — 일정이 확정된 회의가 대부분인데 그
    # 회의들에는 `/delegate-on` 이 아무 일도 안 했습니다.
    qs = MeetingParticipant.objects.filter(
        user=user,
        meeting__status__in=[MeetingStatus.SCHEDULED, MeetingStatus.CONFIRMED,
                             MeetingStatus.ACTIVE])

    """
    알릴 스레드를 **갱신 전에** 모읍니다.

    봇이 「어느 스레드에 알릴지」 를 자기 메모리에서 찾고 있었는데, 그 기억은
    봇이 재시작하면 사라집니다. 그러면 **DB 는 정확히 갱신됐는데 스레드에는
    아무 알림도 안 갑니다** — 대리 참석은 켜졌지만 회의에 있는 사람들은
    모릅니다. 봇이 배포 중에 두 개로 뜨면 각자 자기가 만든 회의만 알아서
    같은 일이 생깁니다.

    대상 회의 목록은 이미 이 쿼리가 갖고 있으니 여기서 실어 보냅니다.

    스레드가 안 붙은 회의는 뺍니다 — 알릴 곳이 없습니다. 웹에서 만들고 아직
    `/meeting-start` 를 안 친 회의가 여기 들어갑니다.
    """
    thread_ids = list(qs.exclude(meeting__discord_channel_id="")
                      .exclude(meeting__discord_channel_id=None)
                      .values_list("meeting__discord_channel_id", flat=True))

    if on:
        # 웹 경로(`/meetings/{id}/absence`)와 같은 값을 씁니다. 같은 행위가
        # 경로에 따라 `ABSENT` 와 `DELEGATED` 로 갈리면, 어디서 눌렀느냐에 따라
        # 화면 뱃지가 `불참` 과 `Bordo 대리 참석 예정` 으로 달라집니다.
        meeting_ids = list(qs.values_list("meeting_id", flat=True))
        changed = qs.update(delegated=True, attendance=Attendance.DELEGATED)

        # 예측도 같이 겁니다. 상태만 맞추고 파이프라인을 웹 경로에만 걸어 두면,
        # **Discord 로 대리 참석을 켠 사람은 준비 화면이 언제까지나 빈 칸**입니다.
        # 지금 살아 있는 대리 참석 경로가 대부분 이쪽입니다.
        _queue_debate_points(meeting_ids)
    else:
        # 되돌릴 때는 회의 상태로 나눕니다. 열리지도 않은 회의를 `PRESENT` 로
        # 두면 참석자 목록에 이미 와 있는 사람으로 그려집니다.
        changed = qs.filter(meeting__status=MeetingStatus.ACTIVE).update(
            delegated=False, attendance=Attendance.PRESENT)
        changed += qs.exclude(meeting__status=MeetingStatus.ACTIVE).update(
            delegated=False, attendance=Attendance.PENDING)
    # `thread_ids` 는 **더한 것**입니다. 옛 응답을 읽는 봇도 그대로 돕니다.
    return Response({"delegated": on, "meetings_updated": changed,
                     "thread_ids": thread_ids})


@internal(["POST"])
def delegate_on(request):
    return _set_delegate(request, True)


@internal(["POST"])
def delegate_off(request):
    return _set_delegate(request, False)


# ═══════════════════════════════════════════ 회의

#: 아직 안 열린 회의. 여기 있는 것만 스레드를 붙일 수 있습니다.
#
# `CONFIRMED` 를 빼면 안 됩니다 — 캘린더에서 일정을 확정하면 회의가 그 상태로
# 올라가는데(`apps/calendars/views.py`), 그게 가장 흔한 경로입니다.
# `SCHEDULED` 만 허용하면 **확정한 회의를 Discord 에서 못 엽니다.**
START_READY = ("DRAFT", "SCHEDULED", "CONFIRMED")


def _participant_rows(meeting):
    """봇이 스레드 첫 안내에 쓰는 참석자 목록."""
    from apps.meetings.models import MeetingParticipant

    return [{
        "discord_user_id": p.user.discord_user_id or None,
        "user_id": str(p.user_id),
        "user_name": p.user_name,
        "delegated": p.delegated,
        "attendance": p.attendance,
    } for p in (MeetingParticipant.objects
                .filter(meeting=meeting).select_related("user"))]


@internal(["GET"])
def meetings_scheduled(request):
    """
    아직 스레드가 안 붙은 **예정 회의** 목록.

    `/meeting-start` 의 자동완성이 씁니다. 이 경로가 생기기 전에는 봇이 회의를
    즉석에서 만들었는데, 웹이 원본이고 Discord 는 회의 공간을 제공하는 구조라
    **웹에서 만든 회의에 스레드만 붙이는** 쪽이 맞습니다 (이슈 #89).

    ## 왜 시각으로 좁히는가

    지난달 회의까지 자동완성에 뜨면 고를 수가 없습니다. 지금 시각 언저리만
    보여주고, **가까운 순**으로 정렬합니다 — 오름차순으로 두면 한참 전에 잡아
    둔 회의가 목록 맨 앞을 차지합니다.
    """
    from apps.meetings.models import Meeting

    team = _team_of(request.query_params.get("guild_id"))
    now = timezone.now()
    rows = list(Meeting.objects
                .filter(project__team=team, status__in=START_READY,
                        discord_channel_id="",
                        scheduled_at__range=(now - timedelta(hours=SCHEDULED_PAST_HOURS),
                                             now + timedelta(hours=SCHEDULED_AHEAD_HOURS)))
                .select_related("project")
                .order_by("scheduled_at")[:SCHEDULED_LIMIT])
    rows.sort(key=lambda m: abs((m.scheduled_at - now).total_seconds()))

    return Response(listing([{
        "meeting_id": str(m.id),
        "project_id": str(m.project_id),
        "project_name": m.project_name,
        "title": m.title,
        "status": m.status,
        "scheduled_at": m.scheduled_at,
        "participants": _participant_rows(m),
    } for m in rows]))


@internal(["POST"])
def meeting_start(request):
    """
    회의를 시작합니다 — 웹에서 만들어 둔 회의에 **스레드를 붙입니다.**

    `meeting_id` 가 오면 그 회의를 씁니다. 안 오면 옛 방식대로 즉석에서
    만듭니다(아래 참고).

    ## `meeting_id` 가 왔는데 못 찾으면 **즉석 생성으로 빠지지 않습니다**

    지금까지는 `meeting_id` 를 아예 안 읽어서, 봇이 보내도 무시하고 아무
    프로젝트에 제목 `Discord 회의` 짜리 빈 회의를 만들었습니다. 봇의 버그와
    정상 동작이 구별되지 않아 이슈 #89 가 생겼습니다.

    ## 즉석 생성은 옛 봇을 위해 남겨 둡니다

    봇이 새 방식으로 배포되기 전에 지우면 그 사이 회의 시작이 통째로 막힙니다.
    배포가 끝나면 지웁니다.
    """
    from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                      MeetingStatus)

    team = _team_of(request.data.get("guild_id"))

    thread_id = _require(request.data.get("thread_id")
                         or request.data.get("text_channel_id"), "thread_id")

    # 같은 스레드로 두 번 들어오면 기존 회의를 돌려줍니다. 봇이 재시도할 때
    # 회의가 두 개 생기면 발언이 갈라져 어느 쪽도 온전하지 않습니다.
    existing = Meeting.objects.filter(discord_channel_id=thread_id).first()
    if existing:
        return Response({"meeting_id": str(existing.id),
                         "project_id": str(existing.project_id),
                         "title": existing.title,
                         "participants": _participant_rows(existing),
                         "duplicate": True}, status=200)

    meeting_id = str(request.data.get("meeting_id") or "").strip()
    if meeting_id:
        return _attach_thread(team, meeting_id, thread_id)

    project = team.projects.order_by("created_at").first()
    if project is None:
        raise BordoError("PROJECT_NOT_FOUND", "팀에 프로젝트가 없습니다. 웹에서 먼저 만드십시오.")

    # 회의를 만든 사람이 필요합니다. 봇은 사용자가 아니므로 팀을 만든 사람으로
    # 둡니다 — 슬래시 명령을 실행한 사람을 넣으면 계정이 안 이어진 경우 막힙니다.
    creator = team.created_by

    title = (request.data.get("agenda") or request.data.get("title")
             or "Discord 회의")

    with transaction.atomic():
        meeting = Meeting.objects.create(
            project=project, project_name=project.name, title=str(title)[:200],
            status=MeetingStatus.ACTIVE, scheduled_at=timezone.now(),
            started_at=timezone.now(), discord_channel_id=thread_id,
            created_by=creator,
        )
        from apps.accounts.models import User

        rows = request.data.get("participants") or []
        # 빈 id 는 걸러냅니다. 그대로 조회하면 미연동 사용자 아무나 잡혀
        # 남의 회의에 등록됩니다.
        by_discord = {str(r.get("discord_user_id") or "").strip(): r
                      for r in rows if str(r.get("discord_user_id") or "").strip()}

        # 참석자마다 쿼리를 날리지 않습니다. 인원이 늘면 그대로 늘어납니다.
        # 계정을 아직 연결하지 않은 참석자는 여기서 빠집니다 — 회의 자체는
        # 진행돼야 하고, 그 사람은 나중에 연결하면 됩니다.
        for u in User.objects.filter(discord_user_id__in=by_discord.keys()):
            delegated = str(
                by_discord[u.discord_user_id].get("status") or "") == "delegated"
            MeetingParticipant.objects.update_or_create(
                meeting=meeting, user=u,
                defaults=dict(user_name=u.name, delegated=delegated,
                              # 웹 경로와 같은 값입니다 (`_set_delegate` 주석 참고).
                              attendance=(Attendance.DELEGATED if delegated
                                          else Attendance.PRESENT)))

    return Response({"meeting_id": str(meeting.id),
                     "project_id": str(project.id),
                     "title": meeting.title,
                     "participants": _participant_rows(meeting)}, status=201)


def _attach_thread(team, meeting_id, thread_id):
    """
    웹에서 만들어 둔 회의에 스레드를 붙이고 시작 상태로 올립니다.

    **참석자를 새로 만들지 않습니다.** 웹에서 회의를 만들 때 프로젝트 참여자
    전원이 이미 등록돼 있습니다. 여기서 봇이 보낸 멘션으로 덮으면 초대받은
    사람이 목록에서 사라집니다.
    """
    from apps.meetings.models import Meeting, MeetingStatus

    meeting = (Meeting.objects.filter(pk=meeting_id)
               .select_related("project").first())
    # 남의 팀 회의를 스레드에 붙이지 못하게 팀까지 봅니다. 없는 것과 남의 것을
    # 같은 오류로 답합니다 — 다르게 주면 회의 id 를 넣어 보는 것만으로 다른
    # 팀에 그런 회의가 있는지 알 수 있습니다.
    if meeting is None or meeting.project.team_id != team.id:
        raise BordoError("MEETING_NOT_FOUND", details={"meeting_id": meeting_id})

    if meeting.status not in START_READY:
        raise BordoError("MEETING_ALREADY_STARTED",
                         "이미 시작했거나 끝난 회의입니다.",
                         details={"meeting_id": meeting_id, "status": meeting.status})

    meeting.discord_channel_id = thread_id
    meeting.status = MeetingStatus.ACTIVE
    meeting.started_at = timezone.now()
    meeting.save(update_fields=["discord_channel_id", "status", "started_at",
                                "updated_at"])
    publish(meeting.project_id, "meeting.started",
            {"meeting_id": str(meeting.id), "thread_id": thread_id})
    return Response({"meeting_id": str(meeting.id),
                     "project_id": str(meeting.project_id),
                     "title": meeting.title,
                     "participants": _participant_rows(meeting)}, status=200)


@internal(["POST"])
def meeting_end(request):
    """
    회의를 끝내고 **백엔드가 요약을 만듭니다.**

    봇은 원본만 보냅니다. 봇이 요약하면 그 과정에서 빠진 내용이 영영 사라지고,
    나중에 대리인이 사람마다 다르게 정리해 주지 못합니다.
    """
    from apps.agent.services import briefing, flow
    from apps.meetings.models import Meeting, MeetingStatus

    # 빈 thread_id 로 조회하면 웹에서 만든 회의(discord_channel_id="")가 잡혀
    # 엉뚱한 팀의 회의가 강제 종료됩니다.
    thread_id = _require(request.data.get("thread_id"), "thread_id")

    # select_for_update로 상태 확인·전환을 잠급니다. 같은 스레드에 대해
    # /meeting-end가 거의 동시에 두 번 오면(더블클릭, 봇 여러 인스턴스 등)
    # 잠금 없이는 둘 다 status != ENDED를 보고 둘 다 아래 브리핑 생성까지
    # 진행해 요약이 두 번 만들어집니다. 뒤에 오는 요청은 앞 요청의 트랜잭션이
    # 끝날 때까지 여기서 기다렸다가, 이미 ENDED로 바뀐 걸 보고 duplicate로
    # 빠집니다. skip_locked를 안 쓰는 이유가 이것입니다 — outbox_events처럼
    # 건너뛰면 안 되고, 기다렸다가 최신 상태를 봐야 합니다.
    #
    # 잠금은 상태 전환까지만 잡습니다. 브리핑 생성은 LLM을 타 오래 걸릴 수
    # 있는데, 그 시간 내내 행을 잠가 두면 뒤에 오는 요청이 쓸데없이 오래
    # 기다립니다.
    with transaction.atomic():
        meeting = (Meeting.objects.select_for_update()
                  .filter(discord_channel_id=thread_id).first())
        if meeting is None:
            raise BordoError("MEETING_NOT_FOUND", "해당 스레드의 회의를 찾을 수 없습니다.")

        if meeting.status == MeetingStatus.ENDED:
            return Response({"meeting_id": str(meeting.id), "title": meeting.title,
                             "duplicate": True})

        ended_at = parse_dt(request.data.get("ended_at"), "ended_at") or timezone.now()
        meeting.status = MeetingStatus.ENDED
        meeting.ended_at = ended_at
        meeting.save(update_fields=["status", "ended_at", "updated_at"])

    # 플로우 진하기를 다시 계산합니다.
    #
    # 회의 중에 만든 엣지는 전부 1.0 으로 저장돼 있습니다 — 만들 때는 ended_at 이
    # 없어 "가장 최근" 이 곧 자기 자신이기 때문입니다. 여기서 구간이 확정되므로
    # 이때 한 번 채워야 그라데이션이 실제로 보입니다.
    #
    # 브리핑보다 먼저 합니다. 브리핑 생성은 LLM 을 타서 실패할 수 있는데,
    # 그것 때문에 화면 진하기까지 못 고치면 손해가 큽니다.
    try:
        flow.recompute_for_meeting(meeting)
    except Exception:                                          # noqa: BLE001
        logger.exception("플로우 진하기 재계산 실패 meeting=%s", meeting.id)

    # 요약과 브리핑은 여기서 만듭니다. 실패해도 회의 종료 자체는 되돌리지
    # 않습니다 — 종료가 안 되면 봇이 계속 발언을 넘깁니다.
    briefings = 0
    try:
        briefings = briefing.build_all(meeting)
    except Exception:                                          # noqa: BLE001
        logger.exception("회의 요약 생성 실패 meeting=%s", meeting.id)

    summary = getattr(meeting, "summary", None)
    return Response({
        "meeting_id": str(meeting.id),
        "title": meeting.title,
        "briefings": briefings,
        "summary": {
            "one_line": getattr(summary, "one_line", ""),
            "discovered_issues": getattr(summary, "discovered_issues", []),
            "changes": getattr(summary, "changes", []),
            "next_plans": getattr(summary, "next_plans", []),
        } if summary else None,
    })


# ═══════════════════════════════════════════ 발언 · 상태

#: Discord 멘션 표기. `<@123>` 과 별명 형태 `<@!123>` 둘 다 옵니다.
_MENTION = re.compile(r"<@!?(\d+)>")


def _resolve_mentions(body: str, mentions) -> str:
    """
    `<@1234567890>` 을 사람 이름으로 바꿉니다.

    ## 왜 필요한가

    봇은 `mentions` 를 실어 보내는데 서버가 안 읽고 있었습니다. 그래서 대상
    판정(`targeting.pick`)이 원문만 보고 **스노플레이크 숫자를 한국어 이름
    목록과 맞춰야** 했고, 대개 `아무에게도 향하지 않음` 을 골랐습니다 —
    멘션으로 부른 대리인이 입을 안 여는 것이 이 때문입니다. 가장 강한 신호를
    이미 받아 놓고 안 쓰고 있었습니다.

    ## 왜 별도 칸이 아니라 본문을 고치는가

    회의록·요약·논쟁점 예측이 전부 `Utterance.body` 를 읽습니다. 멘션을 옆
    칸에 두면 그 셋이 각자 합쳐야 하고, 지금도 요약에는 숫자가 그대로 실려
    나갑니다. 사람이 읽는 회의록에도 이름이 낫습니다.

    이은 계정이 없는 사람은 그대로 둡니다. 지우면 누구를 부른 것인지가
    사라지고, 아무 이름이나 넣으면 없는 사람이 회의록에 등장합니다.
    """
    from apps.accounts.models import User

    ids = {str(m) for m in (mentions or []) if str(m).strip()}
    ids |= set(_MENTION.findall(body or ""))
    if not ids:
        return body

    names = dict(User.objects.filter(discord_user_id__in=ids)
                 .values_list("discord_user_id", "name"))
    if not names:
        return body

    return _MENTION.sub(lambda m: names.get(m.group(1)) or m.group(0), body)


@internal(["POST"])
def discord_messages(request):
    """
    회의 발언을 그대로 저장하고 대리인을 깨웁니다.

    **원문을 그대로 둡니다.** 요약·해석은 뒤에서 합니다.
    """
    from apps.agent.tasks import run_agent_for_utterance
    from apps.meetings.models import Meeting, MeetingStatus, Utterance

    # 스레드가 아닌 채널의 메시지는 조용히 흘려보냅니다.
    #
    # 봇은 길드의 모든 메시지를 넘기는데, 스레드가 아니면 `thread_id` 가 `null`
    # 입니다(`bot/cogs/general.py`). 여기서 400 을 내면 **평소 잡담 한 줄마다**
    # 오류가 쌓여, 정작 봐야 할 오류가 그 안에 묻힙니다. 회의 밖 발언을 버리는
    # 것은 아래 `no_active_meeting` 과 같은 판단이라 응답도 같은 모양으로 냅니다.
    #
    # 조회 전에 빠져나가야 합니다 — 빈 값으로 조회하면 웹에서 만든 회의
    # (`discord_channel_id=""`)가 잡혀 발언이 남의 회의로 새어 들어갑니다.
    thread_id = str(request.data.get("thread_id") or "").strip()
    if not thread_id:
        return Response({"skipped": "not_a_thread"}, status=202)

    body = (request.data.get("content") or request.data.get("body") or "").strip()
    if not body:
        return Response({"skipped": "empty"}, status=202)

    meeting = Meeting.objects.filter(discord_channel_id=thread_id,
                                     status=MeetingStatus.ACTIVE).first()
    if meeting is None:
        # 회의 밖 잡담입니다. 저장하지 않습니다 — 회의록이 아닌 것이 섞이면
        # 대리인이 엉뚱한 맥락을 근거로 삼습니다.
        return Response({"skipped": "no_active_meeting"}, status=202)

    from apps.accounts.models import User
    speaker = User.objects.filter(
        discord_user_id=str(request.data.get("author_discord_id") or "")).first()

    body = _resolve_mentions(body, request.data.get("mentions"))

    utterance = Utterance.objects.create(
        meeting=meeting, participant=speaker,
        participant_name=(request.data.get("author")
                          or getattr(speaker, "name", "") or "알 수 없음")[:100],
        body=body,
        spoken_at=parse_dt(request.data.get("created_at"), "created_at"),
    )

    # 대리인은 비동기로 깨웁니다. 봇은 기다리지 않습니다.
    try:
        run_agent_for_utterance.delay(str(utterance.id))
    except Exception:                                          # noqa: BLE001
        # 브로커가 없어도 발언은 이미 저장됐습니다. 회의록이 남는 것이 먼저입니다.
        logger.exception("대리인 기동 실패 utterance=%s", utterance.id)

    return Response({"utterance_id": str(utterance.id)}, status=201)


@internal(["POST"])
def discord_presence(request):
    """
    온라인 여부. 대리 참석 판단의 보조 신호입니다.

    `discord_user_id` 없이 오면 **봇 자신의 생존 신호**입니다 (`on_ready` · `on_resumed`
    가 `{status, at}` 만 보냅니다). 사람 상태로 해석해 400 을 내던 것을, 캐시에 남겨
    웹의 연결 진단(`/teams/{id}/discord/status`)이 "봇이 살아 있다" 를 보여주게 합니다.
    """
    from apps.meetings.models import Attendance, MeetingParticipant, MeetingStatus

    from .web_views import BOT_PRESENCE_KEY, BOT_PRESENCE_TTL_SEC

    status = str(request.data.get("status") or "").lower()
    if not str(request.data.get("discord_user_id") or "").strip():
        cache.set(BOT_PRESENCE_KEY,
                  {"status": status or "online",
                   "at": str(request.data.get("at") or timezone.now().isoformat())},
                  timeout=BOT_PRESENCE_TTL_SEC)
        return Response({"recorded": "bot_presence"}, status=202)

    discord_user_id = _require(request.data.get("discord_user_id"), "discord_user_id")

    from apps.accounts.models import User
    user = User.objects.filter(discord_user_id=discord_user_id).first()
    if user is None:
        return Response({"skipped": "unlinked"}, status=202)

    # idle·dnd 는 자리에 있는 상태입니다. 비운 것은 offline·invisible 뿐입니다.
    # online 만 재실로 보면 잠깐 자리를 비운 표시만으로 결석 처리됩니다.
    present = status in ("online", "idle", "dnd")

    # 대리 참석을 켜 둔 사람은 건드리지 않습니다. 본인이 명시적으로 정한 것을
    # 접속 상태 같은 약한 신호로 뒤집으면 안 됩니다.
    changed = (MeetingParticipant.objects
               .filter(user=user, delegated=False,
                       meeting__status=MeetingStatus.ACTIVE)
               .update(attendance=(Attendance.PRESENT if present
                                   else Attendance.ABSENT)))
    return Response({"updated": changed})


# ═══════════════════════════════════════════ 대리인에게 질문

@internal(["POST"])
def deputy_ask(request):
    """
    `/ask-bordo` — 특정 사람의 대리인에게 묻습니다.

    회의 발언과 달리 **대상이 이미 정해져 있습니다.** 대상 판정을 건너뛰고 바로
    대리인을 부릅니다.
    """
    from apps.agent.services import react
    from apps.meetings.models import Meeting, MeetingStatus

    question = (request.data.get("question") or "").strip()
    if not question:
        raise BordoError("VALIDATION_ERROR", "question 은 필수입니다.")

    # 봇이 쓰는 이름과 명세의 이름이 다릅니다. 뜻이 같아 백엔드가 둘 다 받습니다 —
    # 봇을 고치는 동안 호출이 통째로 실패하는 것보다 낫습니다.
    target = _user_of(request.data.get("target_discord_id")
                      or request.data.get("target") or "")

    asker_id = (request.data.get("asker_discord_id")
                or request.data.get("requester_discord_id") or "")
    asker = None
    if asker_id:
        from apps.accounts.models import User
        asker = User.objects.filter(discord_user_id=str(asker_id)).first()

    # thread_id 는 선택입니다(DM 에서도 물을 수 있음). 다만 빈 값으로 조회하면
    # 웹에서 만든 회의(discord_channel_id="")가 잡혀 엉뚱한 회의에 묶입니다.
    thread_id = str(request.data.get("thread_id") or "").strip()
    meeting = (Meeting.objects.filter(discord_channel_id=thread_id,
                                      status=MeetingStatus.ACTIVE).first()
               if thread_id else None)

    outcome = react.run(
        principal=target, question=question, meeting=meeting,
        actor_id=getattr(asker, "id", None), asker=asker,
        project_id=getattr(meeting, "project_id", None),
    )
    return Response({
        "run_id": str(outcome.run.id),
        "answered": outcome.answered,
        "reason": outcome.reason,
        "body": outcome.text,
    }, status=200 if outcome.answered else 202)


# ═══════════════════════════════════════════ 발송함 (Outbox)
#
# 서버가 쓰고 봇이 가져갑니다.
#
# 대리인은 Discord 를 직접 부르지 않습니다. `OutboxEvent` 에 행 하나만 남기고
# 여기서 봇이 꺼내 갑니다 — 요청 트랜잭션 안에서 외부를 부르면 롤백돼도
# 메시지는 이미 나가 있기 때문입니다.
#
# 이 세 엔드포인트가 없어서 큐가 쌓이기만 하고 Discord 에는 한 글자도
# 나가지 않았습니다(이슈 #52).


@internal(["GET"])
def outbox_events(request):
    """
    보낼 것을 가져갑니다.

    ## 같은 것을 두 번 내보내지 않는다

    두 겹으로 막습니다.

    `select_for_update(skip_locked=True)` 는 **같은 순간** 두 요청이 겹치는 것을
    막습니다. 잠긴 행은 기다리지 않고 건너뜁니다.

    다만 이 잠금은 **PostgreSQL 에서만 실제로 걸립니다.** SQLite 는
    `has_select_for_update = False` 라 Django 가 오류 없이 조용히 무시합니다.
    개발용 SQLite 에서는 이 겹이 없는 셈이니, 동시성 문제를 여기서 재현하려
    하지 마십시오. 운영은 PostgreSQL 전제입니다.

    그것만으로는 부족합니다. 잠금은 트랜잭션이 끝나면 풀리므로, 봇이 게시하는
    동안(ACK 전) 다음 폴링이 들어오면 같은 행을 또 집어 갑니다. 그래서 가져간
    것의 `available_at` 을 **잠시 앞으로 밀어 둡니다.**

        가져감 → available_at 을 60초 뒤로 → 그 사이에는 조회에 안 걸림
        ACK    → SENT
        실패    → mark_failed() 가 자기 백오프로 다시 잡음
        봇이 죽음 → 60초 뒤 다시 보임 (잃어버리지 않음)

    상태를 하나 더 만들지 않은 이유가 이것입니다. `SENDING` 을 두면 봇이 그 상태로
    죽었을 때 **아무도 되돌리지 못해 영원히 멈춥니다.** 시각을 미는 방식은 저절로
    풀립니다.

    ## `DEAD` 는 여기서 안 나갑니다

    상한을 넘긴 것은 사람이 봐야 하는 상태입니다. 계속 내보내면 같은 실패를
    무한히 반복하고, 그냥 버리면 사용자는 대리인이 말한 줄 알고 있습니다.
    """
    from apps.agent.models import OutboxEvent

    try:
        limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
    except (TypeError, ValueError):
        raise BordoError("VALIDATION_ERROR", "limit 은 숫자여야 합니다.")

    now = timezone.now()
    with transaction.atomic():
        rows = list(OutboxEvent.objects
                    .select_for_update(skip_locked=True)
                    .filter(status=OutboxEvent.Status.PENDING, available_at__lte=now)
                    .order_by("available_at")[:limit])

        # 가져가는 것 자체를 한 번의 시도로 셉니다.
        #
        # 실패했을 때만 세면 봇이 게시하다 그냥 죽는 경우를 영영 못 셉니다.
        # 타임아웃이 지나 다시 보이고, 또 가져가고, 또 죽고를 **무한히 반복하면서
        # DEAD 에는 절대 도달하지 않습니다.** 하필 그 상황이 사람이 봐야 하는
        # 상황입니다.
        handed = [e for e in rows if e.mark_handed_out()]

        if handed:
            (OutboxEvent.objects
             .filter(id__in=[r.id for r in handed])
             .update(available_at=now + timedelta(seconds=VISIBILITY_TIMEOUT_SEC)))

    return Response(listing([{
        "id": str(e.id),
        "kind": e.kind,
        "channel_id": e.channel_id,
        "payload": e.payload,
        "attempts": e.attempts,
        "run_id": str(e.run_id) if e.run_id else None,
    } for e in handed]))


def _outbox_event(event_id):
    from apps.agent.models import OutboxEvent

    event = OutboxEvent.objects.filter(pk=event_id).first()
    if event is None:
        raise BordoError("STATE_NOT_FOUND", "발송 항목을 찾을 수 없습니다.")
    return event


@internal(["POST"])
def outbox_ack(request, event_id):
    """
    게시에 성공했습니다.

    두 번 불려도 안전합니다. 봇이 게시까지 하고 ACK 에서 네트워크가 끊기면
    다음 폴링에서 다시 시도하는데, 그때 이미 `SENT` 인 것을 오류로 돌려주면
    봇은 실패로 알고 또 게시합니다.
    """
    event = _outbox_event(event_id)
    if event.status != event.Status.SENT:
        event.mark_sent()
    return Response({"id": str(event.id), "status": event.status,
                     "sent_at": event.sent_at})


@internal(["POST"])
def outbox_fail(request, event_id):
    """
    게시에 실패했습니다.

    `mark_failed()` 가 지수 백오프로 `available_at` 을 밀고, 상한을 넘기면
    `DEAD` 로 둡니다. 같은 간격으로 반복하면 Discord 가 잠시 불안정할 때
    재시도가 몰려 상황을 더 나쁘게 만듭니다.
    """
    event = _outbox_event(event_id)
    if event.status != event.Status.SENT:
        event.mark_failed(str(request.data.get("error") or ""))
    return Response({"id": str(event.id), "status": event.status,
                     "attempts": event.attempts,
                     "available_at": event.available_at})


# ═══════════════════════════════════════════ 회의 참석 · 대리 상태
#
# 봇은 지금 대리 대상자를 **모듈 전역 set** 에, 진행 중 회의를 cog 의 dict 에
# 들고 있습니다. 재시작하면 통째로 사라지는데 서버에 되물을 경로가 없었습니다 —
# 대리 참석을 켜 둔 사람이 전부 직접 참석으로 잡힙니다.
#
# 진실의 원본을 서버 하나로 모읍니다(설계 2원칙, 서버 중심).


def _meeting_by_thread(thread_id: str):
    from apps.meetings.models import Meeting

    thread_id = _require(thread_id, "thread_id")
    meeting = (Meeting.objects.filter(discord_channel_id=thread_id)
               .select_related("project").order_by("-created_at").first())
    if meeting is None:
        raise BordoError("MEETING_NOT_FOUND",
                         "이 스레드에 연결된 회의가 없습니다.",
                         details={"thread_id": thread_id})
    return meeting


@internal(["GET"])
def meeting_participants(request):
    """
    이 회의에 **누가 불참이고 그 대리인이 무엇을 준비했는지.**

    봇이 회의를 열 때 `○○ 님은 Bordo 가 대신 참석합니다` 를 게시하고, 참석
    여부를 물을 대상(`asked` 가 False 인 사람)을 고르는 데 씁니다.

    `will_speak` 는 `targeting.candidates()` 와 **같은 조건**입니다. 봇이 자기
    나름의 규칙으로 판단하면 "대리인이 답할 사람" 이 서버와 갈려, 봇은 대신
    참석한다고 알렸는데 정작 대리인은 안 깨어납니다.
    """
    from apps.agent.services.flow import agent_display_names
    from apps.meetings.models import (Attendance, DebatePoint, DebateStance,
                                      MeetingParticipant)

    meeting = _meeting_by_thread(request.query_params.get("thread_id"))
    rows = list(MeetingParticipant.objects
                .filter(meeting=meeting).select_related("user"))

    names = agent_display_names([p.user_id for p in rows])
    point_count = DebatePoint.objects.filter(meeting=meeting).count()
    stance_count = dict(
        DebateStance.objects.filter(point__meeting=meeting)
        .values_list("user_id")
        .annotate(n=Count("id"))
        .values_list("user_id", "n"))

    results = []
    for p in rows:
        results.append({
            "user_id": str(p.user_id),
            "discord_user_id": p.user.discord_user_id or None,
            "name": p.user_name,
            "attendance": p.attendance,
            "delegated": p.delegated,
            "agent_name": names.get(p.user_id, ""),
            # targeting.candidates() 와 같은 식입니다.
            "will_speak": p.delegated and p.attendance != Attendance.PRESENT,
            # 아직 참석 여부를 안 정한 사람. 팝업으로 물어볼 대상입니다.
            "asked": p.attendance != Attendance.PENDING,
            "prepared": {"points": point_count,
                         "stances": stance_count.get(p.user_id, 0)},
            # 회의 한정 지시. 봇은 해석하지 않고 그대로 둡니다 — 대리인이 씁니다.
            "has_note": bool(p.delegate_prompt),
        })

    return Response(listing(results, extra={
        "meeting_id": str(meeting.id),
        "title": meeting.title,
        "status": meeting.status,
        "project_id": str(meeting.project_id),
    }))


@internal(["POST"])
def meeting_absence(request):
    """
    회의 **하나**에 대한 불참 등록 / 해제.

    `/internal/v1/delegate/on` 과 다릅니다. 그쪽은 그 사용자의 예정·진행 중 회의를
    **한꺼번에** 뒤집습니다 — 회의 시작 팝업에서 `불참하기` 를 누른 사람이 오늘
    남은 회의 전부에 대리인을 세우게 됩니다.

    웹의 `POST /api/v1/meetings/{id}/absence` 와 같은 일을 하며, 같은 함수를
    지납니다. 봇으로 들어왔다고 다른 상태가 되면 뱃지 문구가 경로에 따라 갈립니다.
    """
    from apps.meetings.prep import cancel_absence, register_absence

    meeting = _meeting_by_thread(request.data.get("thread_id"))
    user = _user_of(request.data.get("discord_user_id"))
    delegated = bool(request.data.get("delegated", True))

    if delegated:
        _, p, _ = register_absence(user, meeting.id)
    else:
        _, p = cancel_absence(user, meeting.id)

    return Response({"meeting_id": str(meeting.id), "user_id": str(user.id),
                     "delegated": p.delegated, "attendance": p.attendance})
