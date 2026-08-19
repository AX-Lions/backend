"""팀 · 프로젝트 · 즐겨찾기."""
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.permissions import project_membership, team_membership
from apps.common.views import listing
from config.errors import BordoError

from .models import (Favorite, InviteCode, Project, ProjectMember, RecentProject,
                     Team, TeamMember, TeamRole)
from .serializers import (InviteCodeSerializer, ProjectSerializer,
                          ProjectSummarySerializer, TeamMemberSerializer, TeamSerializer)

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)


def project_context(user, projects):
    """즐겨찾기·최근 열람을 한 번에 모아 N+1 을 피합니다."""
    ids = [p.id for p in projects]
    fav = set(Favorite.objects.filter(
        user=user, target_type=Favorite.Target.PROJECT, target_id__in=ids
    ).values_list("target_id", flat=True))
    recent = dict(RecentProject.objects.filter(user=user, project_id__in=ids)
                  .values_list("project_id", "opened_at"))
    return {"favorite_ids": fav, "recent_map": recent}


# ─────────────────────────────────────────── 팀
@api_view(["GET", "POST"])
def teams(request):
    if request.method == "GET":
        memberships = (TeamMember.objects.filter(user=request.user)
                       .select_related("team").order_by("team__name"))
        rows = []
        for m in memberships:
            if m.team.deleted_at:
                continue
            m.team._my_role = m.team_role
            rows.append(TeamSerializer(m.team).data)
        return Response(listing(rows))

    name = (request.data.get("name") or "").strip()
    if not name:
        raise BordoError("VALIDATION_ERROR", "팀 이름은 필수입니다.")
    with transaction.atomic():
        team = Team.objects.create(
            name=name,
            description=request.data.get("description", "") or "",
            timezone=_team_timezone(request.data.get("timezone")),
            created_by=request.user,
            category_keys=request.data.get("categories") or [],
            member_count=1,
        )
        TeamMember.objects.create(team=team, user=request.user, team_role=TeamRole.OWNER)
    team._my_role = TeamRole.OWNER
    return Response(TeamSerializer(team).data, status=201)


@api_view(["GET", "PATCH", "DELETE"])
def team_detail(request, team_id):
    if request.method == "GET":
        m = team_membership(request.user, team_id)
        m.team._my_role = m.team_role
        return Response(TeamSerializer(m.team).data)

    if request.method == "PATCH":
        m = team_membership(request.user, team_id, roles=ADMINS)
        team = m.team
        for field, attr in (("name", "name"), ("description", "description"),
                            ("categories", "category_keys")):
            if field in request.data:
                setattr(team, attr, request.data[field])
        team.save()
        team._my_role = m.team_role
        return Response(TeamSerializer(team).data)

    m = team_membership(request.user, team_id, roles=(TeamRole.OWNER,))
    if request.data.get("confirm_name") != m.team.name:
        raise BordoError("VALIDATION_ERROR",
                         "confirm_name 에 팀 이름을 정확히 적어야 합니다.",
                         details={"expected": m.team.name})
    deleted_at = m.team.soft_delete()
    grace = timezone.now() + timedelta(days=30)
    return Response({"id": str(m.team.id), "deleted_at": deleted_at,
                     "restorable_until": grace})


@api_view(["GET"])
def team_members(request, team_id):
    team_membership(request.user, team_id)
    rows = (TeamMember.objects.filter(team_id=team_id)
            .select_related("user").order_by("user__name"))
    return Response(listing([TeamMemberSerializer(m).data for m in rows]))


def _team_timezone(value) -> str:
    """
    팀 기준 시간대. 없는 지역 이름은 400 입니다.

    조용히 기본값으로 바꾸면 사용자는 골랐다고 생각하는데 서버에는 다른 값이
    들어갑니다. 빈 값은 "안 고름" 이라 그대로 둡니다 — 되돌릴 길을 막지 않습니다.
    """
    name = str(value or "").strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except Exception:                                          # noqa: BLE001
        raise BordoError("VALIDATION_ERROR", "알 수 없는 시간대입니다.",
                         details={"timezone": name})
    return name


def _invite_ttl(data) -> timedelta:
    """
    초대 코드 유효기간.

    화면은 `expires_in_hours` 를 보내는데 서버가 `valid_days` 만 읽어, 72시간으로
    만든 코드가 조용히 7일짜리가 됐습니다. 400 도 안 나서 만든 사람은 사흘 뒤
    닫힐 줄 알고 공유합니다.

    시간 쪽을 먼저 봅니다 — 더 정밀한 단위가 이깁니다.
    """
    hours = data.get("expires_in_hours")
    if hours is not None:
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            raise BordoError("VALIDATION_ERROR", "expires_in_hours 는 정수입니다.",
                             details={"expires_in_hours": hours})
        if hours <= 0:
            raise BordoError("VALIDATION_ERROR", "유효기간은 0보다 커야 합니다.",
                             details={"expires_in_hours": hours})
        return timedelta(hours=hours)
    return timedelta(days=int(data.get("valid_days", 7)))


@api_view(["POST"])
def invite_codes(request, team_id):
    team_membership(request.user, team_id, roles=ADMINS)
    code = f"BRD-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    inv = InviteCode.objects.create(
        code=code, team_id=team_id,
        default_role=request.data.get("default_role", TeamRole.MEMBER),
        max_uses=int(request.data.get("max_uses", 10)),
        expires_at=timezone.now() + _invite_ttl(request.data),
    )
    return Response(InviteCodeSerializer(inv).data, status=201)


@api_view(["POST"])
def join_team(request):
    code = (request.data.get("code") or "").strip()
    inv = InviteCode.objects.filter(code=code).select_related("team").first()
    if not inv:
        raise BordoError("TEAM_INVITE_INVALID", details={"code": code})
    if not inv.is_usable:
        raise BordoError("TEAM_INVITE_EXPIRED",
                         details={"expired_at": inv.expires_at})
    if TeamMember.objects.filter(team=inv.team, user=request.user).exists():
        raise BordoError("TEAM_ALREADY_MEMBER", details={"team_id": str(inv.team_id)})

    with transaction.atomic():
        TeamMember.objects.create(team=inv.team, user=request.user,
                                  team_role=inv.default_role)
        InviteCode.objects.filter(pk=inv.pk).update(used_count=F("used_count") + 1)
        Team.objects.filter(pk=inv.team_id).update(member_count=F("member_count") + 1)
    return Response({"team_id": str(inv.team_id), "team_name": inv.team.name,
                     "joined": True, "team_role": inv.default_role,
                     "joined_at": timezone.now()})


# ─────────────────────────────────────────── 프로젝트
@api_view(["GET", "POST"])
def projects(request, team_id):
    team_membership(request.user, team_id)
    if request.method == "GET":
        rows = list(Project.objects.filter(team_id=team_id).order_by("name"))
        ctx = project_context(request.user, rows)
        return Response(listing(ProjectSummarySerializer(rows, many=True, context=ctx).data))

    m = team_membership(request.user, team_id)
    name = (request.data.get("name") or "").strip()
    if not name:
        raise BordoError("VALIDATION_ERROR", "프로젝트 이름은 필수입니다.")
    with transaction.atomic():
        project = Project.objects.create(
            team=m.team, team_name=m.team.name, name=name,
            description=request.data.get("description") or "",
            created_by=request.user,
        )
        member_ids = request.data.get("member_ids")
        if member_ids:
            valid = set(TeamMember.objects.filter(team_id=team_id, user_id__in=member_ids)
                        .values_list("user_id", flat=True))
            invalid = set(map(str, member_ids)) - {str(v) for v in valid}
            if invalid:
                raise BordoError("APPROVAL_REQUIRED",
                                 "팀 밖 사용자는 프로젝트에 넣을 수 없습니다.",
                                 details={"not_in_team": sorted(invalid)})
            users = list(valid) + [request.user.id]
        else:
            users = list(TeamMember.objects.filter(team_id=team_id)
                         .values_list("user_id", flat=True))
        ProjectMember.objects.bulk_create(
            [ProjectMember(project=project, user_id=u) for u in set(users)],
            ignore_conflicts=True)
        project.member_count = ProjectMember.objects.filter(project=project).count()
        project.save(update_fields=["member_count"])
    return Response(ProjectSerializer(project, context={"favorite_ids": set()}).data,
                    status=201)


@api_view(["GET", "PATCH", "DELETE"])
def project_detail(request, project_id):
    project, member = project_membership(request.user, project_id)

    if request.method == "GET":
        RecentProject.objects.update_or_create(user=request.user, project=project)
        ctx = project_context(request.user, [project])
        return Response(ProjectSerializer(project, context=ctx).data)

    if request.method == "PATCH":
        if member.team_role not in ADMINS and project.created_by_id != request.user.id:
            raise BordoError("TEAM_ACCESS_DENIED", "프로젝트를 수정할 권한이 없습니다.")
        want = request.headers.get("If-Match")
        if want and int(want.strip('"')) != project.version:
            raise BordoError("REFERENCED_BY_OTHERS",
                             "그사이 다른 사람이 수정했습니다.",
                             details={"current_version": project.version}, status=409)
        for f in ("name", "description"):
            if f in request.data:
                setattr(project, f, request.data[f])
        project.version += 1
        project.save()
        ctx = project_context(request.user, [project])
        return Response(ProjectSerializer(project, context=ctx).data)

    if member.team_role not in ADMINS:
        raise BordoError("TEAM_ACCESS_DENIED", "OWNER 또는 ADMIN 만 삭제할 수 있습니다.")
    if request.data.get("confirm_name") != project.name:
        raise BordoError("VALIDATION_ERROR",
                         "confirm_name 에 프로젝트 이름을 정확히 적어야 합니다.",
                         details={"expected": project.name})
    from apps.meetings.models import Meeting, MeetingStatus
    if Meeting.objects.filter(project=project, status=MeetingStatus.ACTIVE).exists():
        raise BordoError("MEETING_NOT_ACTIVE",
                         "진행 중인 회의가 있어 삭제할 수 없습니다. 회의를 먼저 종료하십시오.",
                         status=409)
    deleted_at = project.soft_delete()
    return Response({"id": str(project.id), "deleted_at": deleted_at,
                     "restorable_until": timezone.now() + timedelta(days=30)})


@api_view(["GET"])
def project_members(request, project_id):
    project, _ = project_membership(request.user, project_id)
    rows = (ProjectMember.objects.filter(project=project)
            .select_related("user").order_by("user__name"))
    return Response(listing([{
        "user_id": str(r.user_id), "name": r.user.name,
        "avatar_url": r.user.avatar_url, "project_role": r.user.project_role,
        "has_agent": hasattr(r.user, "agent_settings"),
    } for r in rows]))


@api_view(["PUT", "DELETE"])
def project_favorite(request, project_id):
    project_membership(request.user, project_id)
    if request.method == "PUT":
        Favorite.objects.get_or_create(user=request.user,
                                       target_type=Favorite.Target.PROJECT,
                                       target_id=project_id)
        return Response({"project_id": str(project_id), "is_favorite": True})
    Favorite.objects.filter(user=request.user, target_type=Favorite.Target.PROJECT,
                            target_id=project_id).delete()
    return Response({"project_id": str(project_id), "is_favorite": False})


@api_view(["GET"])
def my_recent_projects(request):
    limit = int(request.query_params.get("limit", 5))
    recent = (RecentProject.objects.filter(user=request.user)
              .select_related("project").order_by("-opened_at")[:limit])
    rows = [r.project for r in recent if not r.project.deleted_at]
    ctx = project_context(request.user, rows)
    return Response(listing(ProjectSummarySerializer(rows, many=True, context=ctx).data))


@api_view(["GET"])
def my_favorite_projects(request):
    ids = Favorite.objects.filter(user=request.user,
                                  target_type=Favorite.Target.PROJECT
                                  ).values_list("target_id", flat=True)
    rows = list(Project.objects.filter(id__in=list(ids)).order_by("name"))
    ctx = project_context(request.user, rows)
    return Response(listing(ProjectSummarySerializer(rows, many=True, context=ctx).data))
