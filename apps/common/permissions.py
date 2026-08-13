"""스코프 권한 — 팀 멤버십, 프로젝트 참여, 역할."""
from config.errors import BordoError


def team_membership(user, team_id, roles=None):
    from apps.orgs.models import TeamMember
    m = TeamMember.objects.filter(team_id=team_id, user=user).select_related("team").first()
    if not m or m.team.deleted_at:
        # 비멤버에게는 존재 여부도 알리지 않습니다.
        raise BordoError("TEAM_ACCESS_DENIED", details={"team_id": str(team_id)})
    if roles and m.team_role not in roles:
        raise BordoError("TEAM_ACCESS_DENIED",
                         f"{' 또는 '.join(roles)} 권한이 필요합니다.",
                         details={"your_role": m.team_role, "required": list(roles)})
    return m


def project_membership(user, project_id, roles=None):
    """
    프로젝트 참여자 확인.

    프로젝트 참여자가 아니어도 팀의 OWNER/ADMIN 이면 통과시킵니다 —
    관리자가 자기 팀 프로젝트를 못 들여다보면 운영이 안 됩니다.
    """
    from apps.orgs.models import Project, ProjectMember, TeamRole
    project = Project.objects.filter(pk=project_id).select_related("team").first()
    if not project:
        raise BordoError("PROJECT_NOT_FOUND", details={"project_id": str(project_id)})

    member = team_membership(user, project.team_id)      # 팀 밖이면 여기서 막힙니다
    is_participant = ProjectMember.objects.filter(project=project, user=user).exists()
    if not is_participant and member.team_role not in (TeamRole.OWNER, TeamRole.ADMIN):
        raise BordoError("PROJECT_ACCESS_DENIED", details={"project_id": str(project_id)})

    if roles and member.team_role not in roles:
        raise BordoError("TEAM_ACCESS_DENIED",
                         f"{' 또는 '.join(roles)} 권한이 필요합니다.",
                         details={"your_role": member.team_role})
    return project, member


def meeting_access(user, meeting_id):
    from apps.meetings.models import Meeting
    meeting = (Meeting.objects.filter(pk=meeting_id)
               .select_related("project", "project__team").first())
    if not meeting:
        raise BordoError("MEETING_NOT_FOUND", details={"meeting_id": str(meeting_id)})
    project_membership(user, meeting.project_id)
    return meeting
