"""
홈 화면.

`GET /api/v1/home` 한 번으로 첫 화면 전체가 그려집니다.
카드·일정·요약·사이드바를 따로 부르면 첫 진입에서만 왕복이 6번 생깁니다.

스코프는 **팀을 가로지릅니다.** 프로젝트 하나짜리 집계는
`GET /api/v1/projects/{id}/dashboard` 가 따로 있습니다.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.display import full_stamp, short_stamp, time_range, user_tz
from apps.meetings.models import (AiBriefing, Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus, MeetingSummary)
from apps.orgs.models import Favorite, Project, ProjectMember, RecentProject, TeamMember
from apps.orgs.serializers import ProjectSummarySerializer
from apps.orgs.views import project_context


def _my_project_ids(user):
    """참여 중인 프로젝트 + 내가 관리자인 팀의 프로젝트."""
    joined = set(ProjectMember.objects.filter(user=user)
                 .values_list("project_id", flat=True))
    admin_teams = TeamMember.objects.filter(
        user=user, team_role__in=("OWNER", "ADMIN")).values_list("team_id", flat=True)
    if admin_teams:
        joined |= set(Project.objects.filter(team_id__in=list(admin_teams))
                      .values_list("id", flat=True))
    return list(joined)


def _shortcuts(user):
    """
    사이드바 하단 `바로가기버튼모음`.

    `Zero 바로가기` 에 방 id 를 실어 보내는 이유 — 없으면 홈에서 대리인 방으로
    가려고 채팅 사이드바를 통째로 한 번 더 불러야 합니다.

    Discord 주소도 서버가 조립합니다. 클라이언트가 guild_id 로 URL 을 만들면
    연결이 안 된 팀에서도 버튼이 활성화됩니다.
    """
    from apps.chat.services import ensure_ai_room

    room = ensure_ai_room(user)
    guild_id = ""       # A 담당(Discord 연동)이 붙으면 팀 연결에서 읽어옵니다.
    return {
        "agent_room_id": str(room.id),
        "discord": ({"guild_id": guild_id, "connected": True,
                     "url": f"https://discord.com/channels/{guild_id}"}
                    if guild_id else {"connected": False}),
    }


@api_view(["GET"])
def home(request):
    user = request.user
    now = timezone.now()
    # 표시용 문자열은 서버가 만듭니다. 팀원이 서로 다른 지역에 있는 것이 이
    # 서비스의 전제라, 브라우저 시간대로 찍으면 같은 회의를 사람마다 다른
    # 시각으로 보게 됩니다.
    tz = user_tz(user)
    project_ids = _my_project_ids(user)

    # ── 브리핑 대기 여부
    pending = (AiBriefing.objects.filter(user=user, read_at__isnull=True)
               .select_related("meeting").order_by("-created_at").first())
    briefing = {
        "exists": pending is not None,
        "meeting_id": str(pending.meeting_id) if pending else None,
        "always_open": user.always_open_briefing,
    }

    # ── 최근 회의 카드 5개 (별·진행률·불참 뱃지)
    meetings = list(Meeting.objects.filter(project_id__in=project_ids)
                    .order_by("-scheduled_at")[:5])
    meeting_ids = [m.id for m in meetings]
    fav_meetings = set(Favorite.objects.filter(
        user=user, target_type=Favorite.Target.MEETING, target_id__in=meeting_ids
    ).values_list("target_id", flat=True))
    my_attendance = dict(MeetingParticipant.objects
                         .filter(meeting_id__in=meeting_ids, user=user)
                         .values_list("meeting_id", "attendance"))
    progress_map = dict(Project.objects.filter(id__in=[m.project_id for m in meetings])
                        .values_list("id", "progress"))

    recent_meetings = [{
        "meeting_id": str(m.id),
        "project_id": str(m.project_id),
        "project_name": m.project_name,
        "title": m.title,
        "thumbnail_url": None,
        "is_favorite": m.id in fav_meetings,
        "progress": progress_map.get(m.project_id, 0),
        # 디자인의 `불참한 회의` 뱃지
        "missed": my_attendance.get(m.id) in (Attendance.ABSENT, Attendance.DELEGATED),
        "scheduled_at": m.scheduled_at,
        "displayed_at": full_stamp(m.scheduled_at, tz),
    } for m in meetings]

    # ── 오늘 일정 — 회의가 Discord 에서 열리므로 채널 정보를 같이 내려줍니다
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timezone.timedelta(days=1)
    today = (Meeting.objects
             .filter(project_id__in=project_ids, scheduled_at__range=(start, end))
             .exclude(status=MeetingStatus.ENDED)
             .order_by("scheduled_at")[:20])
    # 오늘 일정 행의 버튼이 `회의에 참여하지 않아요` / `대리 참석 중` 으로
    # 갈리므로, 내 참석 상태를 함께 내려줍니다. 회의마다 따로 부르면 세 줄짜리
    # 목록에 요청이 세 번 더 나갑니다.
    my_part = {
        str(p.meeting_id): p
        for p in MeetingParticipant.objects.filter(
            meeting__in=today, user=user).only(
                "meeting_id", "delegated", "delegate_prompt", "allowed_sources")
    }

    today_schedule = [{
        "at": m.scheduled_at,
        "ends_at": m.scheduled_at + timezone.timedelta(minutes=m.duration_min),
        "time_range": time_range(m.scheduled_at,
                                 m.scheduled_at + timezone.timedelta(minutes=m.duration_min),
                                 tz),
        "project_id": str(m.project_id),
        "project_name": m.project_name,
        "title": m.title,
        # 행에 `{project_name} · {location}` 으로 붙습니다. 채널 id 로 클라이언트가
        # 판단하게 두면 연결이 끊긴 회의에도 `Discord` 가 뜹니다.
        "location": "Discord" if m.discord_channel_id else "서비스",
        "meeting_id": str(m.id),
        "discord_channel_id": m.discord_channel_id or None,
        # 행마다 붙는 버튼. 회의가 Discord 에서 열리므로 뜻이 일정마다 달라
        # 서버가 정해 내려줍니다 — 클라이언트가 필드 조합으로 추측하면
        # 채널이 없는 회의에도 `참여하기` 가 뜹니다.
        "action": ({"kind": "JOIN_DISCORD", "label": "참여하기",
                    "url": f"https://discord.com/channels/{m.discord_channel_id}"}
                   if m.discord_channel_id
                   else {"kind": "OPEN_MEETING", "label": "보러가기", "url": None}),
        # 내 대리 참석 상태. 참석자가 아닌 회의(팀 공개 일정)면 `None` 입니다 —
        # `False` 로 두면 참석자가 아닌 사람에게도 불참 등록 버튼이 뜹니다.
        "delegation": (
            {"delegated": my_part[str(m.id)].delegated,
             "prompt": my_part[str(m.id)].delegate_prompt,
             # `null` 이면 고른 적 없음(제한 없음), `[]` 면 아무것도 안 씀.
             "sources": my_part[str(m.id)].allowed_sources}
            if str(m.id) in my_part else None),
    } for m in today]

    # ── 최근 회의 요약 카드
    last_ended = (Meeting.objects.filter(project_id__in=project_ids,
                                         status=MeetingStatus.ENDED)
                  .order_by("-ended_at").first())
    summary_card = None
    if last_ended:
        s = MeetingSummary.objects.filter(meeting=last_ended).first()
        # 이 회의가 어느 팀 것인지. 카드 첫 줄이 팀 이름입니다.
        team_name = (Project.objects.filter(pk=last_ended.project_id)
                     .values_list("team_name", flat=True).first() or "")
        att = (MeetingParticipant.objects
               .filter(meeting=last_ended, user=user)
               .values_list("attendance", flat=True).first())
        summary_card = {
            "meeting_id": str(last_ended.id),
            "team_name": team_name,
            "project_name": last_ended.project_name,
            "title": last_ended.title,
            "occurred_at": last_ended.ended_at,
            # 최근 회의 카드와 이름은 같지만 **형태가 다릅니다** — 이쪽은 폭이
            # 좁아 연도를 뺍니다. 상수 하나로 합치면 한쪽이 잘립니다.
            "displayed_at": short_stamp(last_ended.ended_at, tz),
            # 이 카드가 있는 이유 자체가 "자리를 비운 사이 무슨 일이 있었나" 라,
            # 내가 그 자리에 있었는지가 카드의 성격을 바꿉니다.
            "missed": att in (Attendance.ABSENT, Attendance.DELEGATED),
            # 뱃지에 그대로 찍히는 문구입니다. 불리언만 주면 클라이언트마다
            # 다른 낱말을 쓰게 되고, 화면 라벨을 바꿀 때 배포가 두 번 필요합니다.
            "status": "불참한 회의" if att in (Attendance.ABSENT,
                                          Attendance.DELEGATED) else "참석한 회의",
            "main_decisions": (s.changes if s else []) + (s.next_plans if s else []),
            # 화면 라벨이 `Zero 요약` 입니다. 대리인이 화자일 때의 호칭이 `Zero` 라
            # 응답 필드도 그 이름을 씁니다.
            "zero_summary": s.one_line if s else "",
        }

    # ── 사이드바
    projects = list(Project.objects.filter(id__in=project_ids).order_by("name"))
    ctx = project_context(user, projects)
    recent_ids = list(RecentProject.objects.filter(user=user, project_id__in=project_ids)
                      .order_by("-opened_at").values_list("project_id", flat=True)[:5])
    by_id = {p.id: p for p in projects}
    recent_projects = [by_id[i] for i in recent_ids if i in by_id]
    favorite_projects = [p for p in projects if p.id in ctx["favorite_ids"]]

    return Response({
        "user_name": user.name,
        # 사이드바 하단 프로필. 없으면 화면이 이름 첫 글자로 원을 그립니다 —
        # 그 판단을 하려면 값이 오기는 해야 합니다.
        "user_avatar_url": user.avatar_url or None,
        # 문구는 그대로 두고 `Zero 브리핑 보러가기` 버튼이 함께 뜹니다.
        # 문구를 갈아끼우면 이름으로 맞이하는 인사가 사라집니다.
        "greeting_mode": "BRIEFING_AVAILABLE" if briefing["exists"] else "WELCOME",
        "briefing_pending": briefing,
        "shortcuts": _shortcuts(user),
        "recent_meetings": recent_meetings,
        "today_schedule": today_schedule,
        "recent_meeting_summary": summary_card,
        "project_progress": ProjectSummarySerializer(projects, many=True, context=ctx).data,
        "recent_projects": ProjectSummarySerializer(recent_projects, many=True,
                                                    context=ctx).data,
        "favorite_projects": ProjectSummarySerializer(favorite_projects, many=True,
                                                      context=ctx).data,
    })


@api_view(["POST"])
def briefing_dismiss(request):
    """홈 팝업의 `취소` / `브리핑 보러가기` 결과."""
    always = bool(request.data.get("always_open", False))
    if always != request.user.always_open_briefing:
        request.user.always_open_briefing = always
        request.user.save(update_fields=["always_open_briefing", "updated_at"])
    return Response({"always_open": request.user.always_open_briefing})


@api_view(["PUT", "DELETE"])
def meeting_favorite(request, meeting_id):
    """홈 카드의 별 아이콘. 디자인에는 있는데 스펙에 없던 것을 채웠습니다."""
    from apps.common.permissions import meeting_access
    meeting_access(request.user, meeting_id)
    if request.method == "PUT":
        Favorite.objects.get_or_create(user=request.user,
                                       target_type=Favorite.Target.MEETING,
                                       target_id=meeting_id)
        return Response({"meeting_id": str(meeting_id), "is_favorite": True})
    Favorite.objects.filter(user=request.user, target_type=Favorite.Target.MEETING,
                            target_id=meeting_id).delete()
    return Response({"meeting_id": str(meeting_id), "is_favorite": False})
