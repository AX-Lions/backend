"""
태스크.

`status` 는 PATCH 로 못 바꿉니다. `approve` · `reject` · `start` · `block` ·
`complete` 전용 엔드포인트만 상태를 움직입니다. 호출 자체가 승인 행위라
별도 승인 게이트를 두지 않습니다.

프로젝트 `progress` 는 여기서 파생됩니다. 태스크가 움직일 때마다 다시 셉니다 —
화면이 진행률을 직접 계산하면 서버가 아는 값과 어긋납니다.
"""
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import project_membership
from apps.common.views import listing
from apps.orgs.models import Project, ProjectMember, TeamRole
from config.errors import BordoError

from .models import TRANSITIONS, Task, TaskEvent, TaskStatus
from .serializers import TaskSerializer

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)
#: 진행률에 안 세는 상태. 반려된 것까지 분모에 넣으면 진행률이 영영 100 이 안 됩니다.
EXCLUDED_FROM_PROGRESS = (TaskStatus.REJECTED, TaskStatus.PENDING_APPROVAL)


def recalc_progress(project):
    """
    프로젝트 진행률 = 완료 / (승인·반려 제외 전체).

    승인 대기를 분모에서 빼는 이유 — AI 가 후보를 열 개 만들면 진행률이
    갑자기 떨어집니다. 사람이 승인해서 실제 할 일이 된 것만 셉니다.
    """
    agg = (Task.objects.filter(project=project)
           .exclude(status__in=EXCLUDED_FROM_PROGRESS)
           .aggregate(total=Count("id"),
                      done=Count("id", filter=Q(status=TaskStatus.COMPLETED))))
    total, done = agg["total"] or 0, agg["done"] or 0
    value = round(done * 100 / total) if total else 0
    if value != project.progress:
        Project.objects.filter(pk=project.pk).update(progress=value)
        project.progress = value
    return value


def _load(user, task_id):
    task = (Task.objects.filter(pk=task_id)
            .select_related("project", "assignee").first())
    if not task:
        raise BordoError("STATE_NOT_FOUND", "태스크를 찾을 수 없습니다.")
    _, member = project_membership(user, task.project_id)
    return task, member


def _may_manage(task, member, user):
    """담당자 · 만든 사람 · 팀 관리자."""
    return (task.assignee_id == user.id
            or task.created_by_id == user.id
            or member.team_role in ADMINS)


def _move(task, action, user, *, detail=None):
    allowed, target = TRANSITIONS[action]
    if task.status not in allowed:
        raise BordoError(
            "APPROVAL_REQUIRED",
            f"지금 상태에서는 할 수 없습니다.",
            details={"current": task.status, "action": action,
                     "allowed_from": sorted(allowed)}, status=409)
    previous, task.status = task.status, target
    TaskEvent.objects.create(task=task, actor=user, action=action,
                             from_status=previous, to_status=target,
                             detail=detail or {})
    return previous


# ─────────────────────────────────────────── 목록 · 생성
@api_view(["GET", "POST"])
def tasks(request, project_id):
    project, member = project_membership(request.user, project_id)

    if request.method == "GET":
        qs = Task.objects.filter(project=project).select_related("assignee")
        status = request.query_params.get("status")
        if status:
            if status not in TaskStatus.values:
                raise BordoError("VALIDATION_ERROR", "status 값이 올바르지 않습니다.",
                                 details={"allowed": list(TaskStatus.values)})
            qs = qs.filter(status=status)
        assignee = request.query_params.get("assignee")
        if assignee == "me":
            qs = qs.filter(assignee=request.user)
        elif assignee == "none":
            qs = qs.filter(assignee__isnull=True)
        elif assignee:
            qs = qs.filter(assignee_id=assignee)
        meeting = request.query_params.get("source_meeting")
        if meeting:
            qs = qs.filter(source_meeting_id=meeting)
        return Response(listing(TaskSerializer(list(qs[:200]), many=True).data))

    title = (request.data.get("title") or "").strip()
    if not title:
        raise BordoError("VALIDATION_ERROR", "title 은 필수입니다.")

    by_agent = bool(request.data.get("created_by_agent", False))
    assignee_id = request.data.get("assignee_id")
    if assignee_id and not ProjectMember.objects.filter(
            project=project, user_id=assignee_id).exists():
        raise BordoError("PROJECT_ACCESS_DENIED",
                         "이 프로젝트 참여자에게만 맡길 수 있습니다.",
                         details={"assignee_id": str(assignee_id)})

    task = Task.objects.create(
        project=project, title=title,
        description=request.data.get("description") or "",
        priority=request.data.get("priority") or "P2",
        assignee_id=assignee_id or None,
        due_at=parse_dt(request.data.get("due_at"), "due_at"),
        created_by=request.user, created_by_agent=by_agent,
        source_meeting_id=request.data.get("source_meeting") or None,
        # 클라이언트가 status 를 보내도 씹습니다. 이게 설계 1원칙입니다.
        status=TaskStatus.PENDING_APPROVAL if by_agent else TaskStatus.TODO)
    TaskEvent.objects.create(task=task, actor=request.user, action="create",
                             to_status=task.status)
    recalc_progress(project)
    publish(project.id, "task.created",
            {"task_id": str(task.id), "status": task.status,
             "created_by_agent": by_agent})

    body = TaskSerializer(task).data
    if task.status == TaskStatus.PENDING_APPROVAL:
        # 누가 승인해야 하는지 화면이 바로 알 수 있게 같이 줍니다.
        body["approval_required_from"] = [str(task.assignee_id)] if task.assignee_id \
            else [str(u) for u in ProjectMember.objects
                  .filter(project=project).values_list("user_id", flat=True)]
    return Response(body, status=201)


@api_view(["GET", "PATCH", "DELETE"])
def task_detail(request, task_id):
    task, member = _load(request.user, task_id)

    if request.method == "GET":
        body = TaskSerializer(task).data
        body["events"] = [{"action": e.action, "from": e.from_status,
                           "to": e.to_status, "detail": e.detail,
                           "actor_id": str(e.actor_id) if e.actor_id else None,
                           "occurred_at": e.occurred_at}
                          for e in task.events.all()[:50]]
        return Response(body)

    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자 · 만든 사람 · 관리자만 가능합니다.")

    if request.method == "DELETE":
        if task.created_by_agent and task.status == TaskStatus.PENDING_APPROVAL:
            raise BordoError(
                "APPROVAL_REQUIRED",
                "AI 후보는 지우지 말고 반려하십시오. 지우면 아무 신호도 안 남아 "
                "대리인이 같은 후보를 또 만듭니다.",
                details={"use": f"POST /api/v1/tasks/{task.id}/reject"}, status=409)
        deleted_at = task.soft_delete()
        recalc_progress(task.project)
        publish(task.project_id, "task.deleted", {"task_id": str(task.id)})
        return Response({"id": str(task.id), "deleted_at": deleted_at,
                         "restorable_until": timezone.now() + timezone.timedelta(days=30)})

    # ── PATCH — status 는 여기서 못 바꿉니다
    if "status" in request.data:
        raise BordoError(
            "VALIDATION_ERROR",
            "status 는 PATCH 로 바꿀 수 없습니다. "
            "approve · reject · start · block · complete 를 쓰십시오.",
            details={"endpoints": [f"/api/v1/tasks/{task.id}/{a}"
                                   for a in TRANSITIONS]})
    for f in ("title", "description", "priority"):
        if f in request.data:
            setattr(task, f, request.data[f])
    if "due_at" in request.data:
        task.due_at = parse_dt(request.data["due_at"], "due_at")
    task.save()
    publish(task.project_id, "task.updated", {"task_id": str(task.id)})
    return Response(TaskSerializer(task).data)


# ─────────────────────────────────────────── 상태 전이
@api_view(["POST"])
def approve(request, task_id):
    """
    `PENDING_APPROVAL → TODO`.

    **호출 자체가 승인 행위**라 별도 승인 게이트가 없습니다.
    담당자나 관리자만 부를 수 있습니다.
    """
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자 또는 관리자만 승인할 수 있습니다.")

    with transaction.atomic():
        previous = _move(task, "approve", request.user)
        # 승인하면서 담당자·기한을 같이 손볼 수 있습니다.
        if "assignee_id" in request.data:
            aid = request.data["assignee_id"]
            if aid and not ProjectMember.objects.filter(
                    project_id=task.project_id, user_id=aid).exists():
                raise BordoError("PROJECT_ACCESS_DENIED", "참여자가 아닙니다.")
            task.assignee_id = aid or None
        if "due_at" in request.data:
            task.due_at = parse_dt(request.data["due_at"], "due_at")
        task.approved_by, task.approved_at = request.user, timezone.now()
        task.save()
        recalc_progress(task.project)

    publish(task.project_id, "task.approved",
            {"task_id": str(task.id), "approved_by": str(request.user.id)})
    body = TaskSerializer(task).data
    body.update({"previous_status": previous, "approved_by": str(request.user.id),
                 "notification": {"sent_to": [str(task.assignee_id)]
                                  if task.assignee_id else [],
                                  "channel": "discord_dm"}})
    return Response(body)


@api_view(["POST"])
def reject(request, task_id):
    """
    AI 후보를 거절합니다.

    **반려 사유는 다음 회의 보고서를 만들 때 컨텍스트로 참조됩니다** —
    그래서 사유가 필수입니다. 이유 없이 지우면 대리인이 같은 걸 또 제안합니다.
    """
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자 또는 관리자만 반려할 수 있습니다.")
    reason = (request.data.get("reason") or "").strip()
    if not reason:
        raise BordoError("VALIDATION_ERROR",
                         "reason 은 필수입니다. 사유가 다음 회의 보고서의 근거가 됩니다.")

    with transaction.atomic():
        previous = _move(task, "reject", request.user, detail={"reason": reason})
        task.rejected_reason = reason
        task.save(update_fields=["status", "rejected_reason", "updated_at"])
        recalc_progress(task.project)

    publish(task.project_id, "task.rejected", {"task_id": str(task.id)})
    return Response({"id": str(task.id), "status": task.status,
                     "previous_status": previous,
                     "rejected_by": str(request.user.id), "reason": reason,
                     "rejected_at": timezone.now()})


@api_view(["POST"])
def start(request, task_id):
    """`TODO / BLOCKED → IN_PROGRESS`. 계약에는 없지만 없으면 화면이 막힙니다."""
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자만 시작할 수 있습니다.")
    with transaction.atomic():
        previous = _move(task, "start", request.user)
        task.save(update_fields=["status", "updated_at"])
    publish(task.project_id, "task.started", {"task_id": str(task.id)})
    body = TaskSerializer(task).data
    body["previous_status"] = previous
    return Response(body)


@api_view(["POST"])
def block(request, task_id):
    """막힘 처리. 이유가 대리인 답변의 근거가 되므로 받아 둡니다."""
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자만 가능합니다.")
    reason = (request.data.get("reason") or "").strip()
    with transaction.atomic():
        previous = _move(task, "block", request.user, detail={"reason": reason})
        task.save(update_fields=["status", "updated_at"])
    publish(task.project_id, "task.blocked",
            {"task_id": str(task.id), "reason": reason})
    body = TaskSerializer(task).data
    body["previous_status"] = previous
    return Response(body)


@api_view(["POST"])
def complete(request, task_id):
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자 또는 관리자만 완료할 수 있습니다.")
    note = request.data.get("note") or ""

    with transaction.atomic():
        previous = _move(task, "complete", request.user, detail={"note": note})
        task.completed_at, task.completion_note = timezone.now(), note
        task.save(update_fields=["status", "completed_at", "completion_note",
                                 "updated_at"])
        progress = recalc_progress(task.project)

    publish(task.project_id, "task.completed",
            {"task_id": str(task.id), "project_progress": progress})
    return Response({"id": str(task.id), "status": task.status,
                     "previous_status": previous,
                     "completed_by": str(request.user.id), "note": note,
                     "project_progress": progress,
                     "completed_at": task.completed_at})


@api_view(["POST"])
def assign(request, task_id):
    """담당자 변경. 프로젝트 참여자에게만 맡길 수 있습니다."""
    task, member = _load(request.user, task_id)
    if not _may_manage(task, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "담당자 · 만든 사람 · 관리자만 가능합니다.")
    if "assignee_id" not in request.data:
        raise BordoError("VALIDATION_ERROR", "assignee_id 는 필수입니다.")

    new_id = request.data["assignee_id"]
    if new_id and not ProjectMember.objects.filter(
            project_id=task.project_id, user_id=new_id).exists():
        raise BordoError("PROJECT_ACCESS_DENIED",
                         "이 프로젝트 참여자에게만 맡길 수 있습니다. "
                         "먼저 프로젝트에 추가하십시오.",
                         details={"assignee_id": str(new_id)})

    previous = task.assignee_id
    task.assignee_id = new_id or None
    with transaction.atomic():
        task.save(update_fields=["assignee", "updated_at"])
        TaskEvent.objects.create(
            task=task, actor=request.user, action="assign",
            from_status=task.status, to_status=task.status,
            detail={"from": str(previous) if previous else None,
                    "to": str(new_id) if new_id else None})
    publish(task.project_id, "task.assigned",
            {"task_id": str(task.id), "assignee_id": str(new_id) if new_id else None})
    return Response({"id": str(task.id), "assignee_id": str(new_id) if new_id else None,
                     "previous_assignee_id": str(previous) if previous else None,
                     "status": task.status, "changed_by": str(request.user.id),
                     "notification": {"sent_to": [str(new_id)] if new_id else [],
                                      "channel": "discord_dm"},
                     "updated_at": task.updated_at})


@api_view(["GET"])
def my_tasks(request):
    """
    팀을 가로지르는 `내 할 일`.

    홈에서 프로젝트마다 따로 부르지 않아도 되도록 한 번에 내려줍니다.
    """
    qs = (Task.objects.filter(assignee=request.user)
          .exclude(status__in=(TaskStatus.COMPLETED, TaskStatus.REJECTED))
          .select_related("project", "assignee").order_by("due_at", "-created_at"))
    rows = list(qs[:100])
    body = TaskSerializer(rows, many=True).data
    for item, task in zip(body, rows):
        item["project_name"] = task.project.name
    return Response(listing(body))
