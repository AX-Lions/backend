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


@api_view(["GET"])
def home(request):
    user = request.user
    now = timezone.now()
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
    } for m in meetings]

    # ── 오늘 일정 — 회의가 Discord 에서 열리므로 채널 정보를 같이 내려줍니다
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timezone.timedelta(days=1)
    today = (Meeting.objects
             .filter(project_id__in=project_ids, scheduled_at__range=(start, end))
             .exclude(status=MeetingStatus.ENDED)
             .order_by("scheduled_at")[:20])
    today_schedule = [{
        "at": m.scheduled_at,
        "ends_at": m.scheduled_at + timezone.timedelta(minutes=m.duration_min),
        "project_id": str(m.project_id),
        "project_name": m.project_name,
        "title": m.title,
        "meeting_id": str(m.id),
        "channel": "Discord" if m.discord_channel_id else None,
        "discord_channel_id": m.discord_channel_id or None,
    } for m in today]

    # ── 최근 회의 요약 카드
    last_ended = (Meeting.objects.filter(project_id__in=project_ids,
                                         status=MeetingStatus.ENDED)
                  .order_by("-ended_at").first())
    summary_card = None
    if last_ended:
        s = MeetingSummary.objects.filter(meeting=last_ended).first()
        att = my_attendance.get(last_ended.id)
        summary_card = {
            "meeting_id": str(last_ended.id),
            "project_name": last_ended.project_name,
            "title": last_ended.title,
            "ended_at": last_ended.ended_at,
            "missed": att in (Attendance.ABSENT, Attendance.DELEGATED),
            "main_decisions": (s.changes if s else []) + (s.next_plans if s else []),
            "agent_summary": s.one_line if s else "",
            "main_opinions": s.main_opinions if s else [],
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
        "greeting_mode": "BRIEFING_AVAILABLE" if briefing["exists"] else "WELCOME",
        "briefing_pending": briefing,
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
