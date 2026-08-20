"""
`project_id` 를 정하는 규칙.

쓰기 도구는 전부 프로젝트가 필요한데 1단계에는 읽기 도구가 없어 클라이언트가
그 값을 알 방법이 없습니다. 그래서 (1) 핸드셰이크 `instructions` 에 쓸 수 있는
프로젝트 목록을 실어 보내고, (2) 생략하면 **가장 최근에 연 프로젝트**로 갑니다.
기본값으로 정해졌다는 사실은 결과에 `resolved_by: "default"` 로 반드시 돌려줍니다 —
엉뚱한 데 들어가는 일이 조용히 일어나면 안 됩니다.
"""
from __future__ import annotations

from django.db.models import Q

from apps.common.permissions import project_membership
from apps.orgs.models import Project, RecentProject, TeamRole
from config.errors import BordoError


def writable_projects(user) -> list[Project]:
    """참여자이거나, 팀 OWNER/ADMIN 이라 `project_membership()` 을 통과하는 프로젝트."""
    admin_team_ids = user.team_memberships.filter(
        team_role__in=(TeamRole.OWNER, TeamRole.ADMIN)).values_list("team_id", flat=True)
    return list(Project.objects.filter(
        Q(members__user=user) | Q(team_id__in=list(admin_team_ids)),
        team__deleted_at__isnull=True,
    ).distinct().select_related("team").order_by("team_name", "name"))


def default_project(user) -> Project | None:
    recent = (RecentProject.objects.filter(user=user, project__deleted_at__isnull=True)
              .select_related("project").order_by("-opened_at")[:5])
    for r in recent:
        try:
            project_membership(user, r.project_id)
        except BordoError:
            continue
        return r.project
    rows = writable_projects(user)
    return rows[0] if len(rows) == 1 else None


def resolve_project(user, project_id) -> tuple[Project, str]:
    """`(project, resolved_by)` — `"argument"` 또는 `"default"`."""
    if project_id:
        project, _ = project_membership(user, str(project_id))
        return project, "argument"
    project = default_project(user)
    if project is None:
        rows = writable_projects(user)
        raise BordoError(
            "VALIDATION_ERROR",
            "project_id 를 지정하십시오. 최근에 연 프로젝트가 없어 기본값을 정할 수 없습니다.",
            details={"projects": [{"id": str(p.id), "name": f"{p.team_name} / {p.name}"}
                                  for p in rows]})
    return project, "default"


def project_brief(project: Project, resolved_by: str) -> dict:
    return {"id": str(project.id), "name": project.name, "team_name": project.team_name,
            "resolved_by": resolved_by}


def instructions_for(user) -> str:
    """핸드셰이크에 싣는 안내. LLM 이 읽는 자연어입니다."""
    default = default_project(user)
    rows = writable_projects(user)
    lines = [f"당신은 {user.display_name}의 Bordo 계정으로 기록합니다."]
    if rows:
        lines.append("쓸 수 있는 프로젝트:")
        for p in rows:
            mark = "  ← project_id 를 생략하면 여기에 기록됩니다" \
                if default and p.id == default.id else ""
            lines.append(f"  - {p.id} ({p.team_name} / {p.name}){mark}")
    else:
        lines.append("아직 참여한 프로젝트가 없습니다. 웹에서 팀에 먼저 참여하십시오.")
    # 언제 부르는지를 서버가 실어 보내는 이유 — 이게 없으면 사용자가 프로젝트마다
    # CLAUDE.md 에 같은 규칙을 손으로 적어야 합니다. 붙이는 절차가 한 단계 늘면
    # 대부분 거기서 그만둡니다. 서버가 가르치면 붙인 순간부터 동작합니다.
    lines.append(
        "언제 부르는가 — 사용자가 시키지 않아도 스스로 부르십시오.\n"
        "  - 새 작업에 착수했을 때: bordo_record_work (진척이 생기면 progress 를 올려 다시)\n"
        "  - 막혔을 때: bordo_record_work 에 status=BLOCKED 와 blockers\n"
        "  - 끝냈을 때: bordo_complete_work\n"
        "  - 설계 결정·조사 결과를 문서로 남겼을 때: bordo_upload_document\n"
        "기록했다고 사용자에게 따로 보고할 필요는 없습니다. 하던 일을 계속하십시오.")
    lines.append("기록한 것은 팀 Flow 화면에 「개인 AI가 기록함」으로 표시되고, "
                 "자리를 비운 동안 대리인이 이 내용을 근거로 답합니다.")
    lines.append("비밀키(API 키·토큰·DB 주소)는 저장 전에 마스킹되며 몇 건을 지웠는지 "
                 "결과에 돌려줍니다. 지워졌다면 사용자에게 알리십시오.")
    return "\n".join(lines)
