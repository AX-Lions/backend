"""
현재 상태 — work · plan · thought.

셋 다 모양이 거의 같아 목록/생성과 상세/수정/삭제를 한 벌로 짜고
모델만 갈아끼웁니다. 규칙 차이는 `services.RULES` 한 곳에 모았습니다.

**낙관적 업데이트가 허용되는 유일한 영역입니다.** 개인 상태라 충돌해도
잃을 게 적습니다. 승인·회의·동기화에서는 절대 쓰지 마십시오.
"""
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.permissions import project_membership
from apps.common.views import listing
from config.errors import BordoError

from .models import ActivityEvent, Visibility
from .serializers import PlanItemSerializer, ThoughtItemSerializer, WorkItemSerializer
from .services import (RULES, apply_fields, log_activity, require_fields, validate,
                       visible)

# 검증·활동 로그 규칙은 `services.py` 에 있습니다 — `/mcp` 쓰기가 같은 함수를 지납니다.
SERIALIZERS = {
    "work": WorkItemSerializer,
    "plan": PlanItemSerializer,
    "thought": ThoughtItemSerializer,
}


# ─────────────────────────────────────────── 목록 · 생성
def collection(request, project_id, key):
    spec, serializer = RULES[key], SERIALIZERS[key]
    project, _ = project_membership(request.user, project_id)

    if request.method == "GET":
        qs = visible(spec.model.objects.filter(project=project), request.user)

        owner = request.query_params.get("owner")
        if owner:
            qs = qs.filter(owner=request.user) if owner == "me" else qs.filter(owner_id=owner)
        status = request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        if key == "thought" and request.query_params.get("requires_discussion"):
            want = request.query_params["requires_discussion"].lower() in ("1", "true")
            qs = qs.filter(requires_discussion=want)

        rows = list(qs.select_related("owner")[:200])
        return Response(listing(serializer(rows, many=True).data))

    data = dict(request.data)
    require_fields(spec, data)
    validate(spec, data)

    payload = {f: data[f] for f in spec.writable if f in data}
    with transaction.atomic():
        obj = spec.model.objects.create(project=project, owner=request.user, **payload)
        log_activity(project, request.user, f"{spec.kind}.created", obj,
                     {"title": str(obj)})
    publish(project.id, f"{spec.kind}.created",
            {"id": str(obj.id), "owner_id": str(request.user.id)})
    return Response(serializer(obj).data, status=201)


def item(request, key, pk):
    spec, serializer = RULES[key], SERIALIZERS[key]
    obj = (spec.model.objects.filter(pk=pk)
           .select_related("project", "owner").first())
    if not obj:
        raise BordoError("STATE_NOT_FOUND", "항목을 찾을 수 없습니다.")
    project_membership(request.user, obj.project_id)
    # 남의 비공개 항목은 없는 것처럼 굽니다.
    if obj.visibility == Visibility.PRIVATE and obj.owner_id != request.user.id:
        raise BordoError("STATE_NOT_FOUND", "항목을 찾을 수 없습니다.")

    if request.method == "GET":
        return Response(serializer(obj).data)

    if obj.owner_id != request.user.id:
        raise BordoError("TEAM_ACCESS_DENIED",
                         "본인 것만 고치거나 지울 수 있습니다. "
                         "이건 `지금 내 상태` 라 남이 대신 쓰면 뜻이 없습니다.")

    if request.method == "DELETE":
        with transaction.atomic():
            log_activity(obj.project, request.user, f"{spec.kind}.deleted", obj,
                         {"title": str(obj)})
            obj.delete()
        publish(obj.project_id, f"{spec.kind}.deleted", {"id": str(pk)})
        return Response(status=204)

    data = dict(request.data)
    validate(spec, data, obj=obj)
    changed = apply_fields(obj, data, spec.writable)
    if not changed:
        return Response(serializer(obj).data)

    with transaction.atomic():
        obj.save()
        log_activity(obj.project, request.user, f"{spec.kind}.updated", obj, changed)
    publish(obj.project_id, f"{spec.kind}.updated",
            {"id": str(obj.id), "changed": list(changed)})
    body = serializer(obj).data
    body["changed"] = changed
    return Response(body)


# ─────────────────────────────────────────── 라우팅 진입점
@api_view(["GET", "POST"])
def work_items(request, project_id):
    return collection(request, project_id, "work")


@api_view(["GET", "PATCH", "DELETE"])
def work_item_detail(request, work_item_id):
    return item(request, "work", work_item_id)


@api_view(["GET", "POST"])
def plans(request, project_id):
    return collection(request, project_id, "plan")


@api_view(["GET", "PATCH", "DELETE"])
def plan_detail(request, plan_id):
    return item(request, "plan", plan_id)


@api_view(["GET", "POST"])
def thoughts(request, project_id):
    return collection(request, project_id, "thought")


@api_view(["GET", "PATCH", "DELETE"])
def thought_detail(request, thought_id):
    return item(request, "thought", thought_id)


@api_view(["GET"])
def activity(request, project_id):
    """
    `이 사람 이번 주에 뭐 했나`.

    상태는 덮어써도 이 로그는 남아, 진행률을 언제 얼마나 올렸는지 되짚을 수 있습니다.
    """
    project, _ = project_membership(request.user, project_id)
    qs = ActivityEvent.objects.filter(project=project).select_related("actor")
    actor = request.query_params.get("actor")
    if actor:
        qs = qs.filter(actor=request.user) if actor == "me" else qs.filter(actor_id=actor)
    kind = request.query_params.get("kind")
    if kind:
        qs = qs.filter(kind__startswith=kind)
    rows = list(qs[:100])
    return Response(listing([{
        "id": str(r.id), "kind": r.kind, "target_id": str(r.target_id),
        "actor_id": str(r.actor_id) if r.actor_id else None,
        "actor_name": r.actor.display_name if r.actor else "(탈퇴한 사용자)",
        "detail": r.detail, "occurred_at": r.occurred_at,
    } for r in rows]))
