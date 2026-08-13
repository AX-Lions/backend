"""
현재 상태 — work · plan · thought.

셋 다 모양이 거의 같아 목록/생성과 상세/수정/삭제를 한 벌로 짜고
모델만 갈아끼웁니다. 규칙 차이는 아래 `SPECS` 한 곳에 모았습니다.

**낙관적 업데이트가 허용되는 유일한 영역입니다.** 개인 상태라 충돌해도
잃을 게 적습니다. 승인·회의·동기화에서는 절대 쓰지 마십시오.
"""
from django.db import transaction
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import project_membership
from apps.common.views import listing
from config.errors import BordoError

from .models import ActivityEvent, PlanItem, ThoughtItem, Visibility, WorkItem, WorkStatus
from .serializers import PlanItemSerializer, ThoughtItemSerializer, WorkItemSerializer


class Spec:
    def __init__(self, model, serializer, kind, writable, required):
        self.model = model
        self.serializer = serializer
        self.kind = kind                  # 이벤트 이름 접두사
        self.writable = writable          # 생성·수정에서 받는 필드
        self.required = required          # 생성 시 필수


SPECS = {
    "work": Spec(
        WorkItem, WorkItemSerializer, "work",
        ("title", "category", "summary", "status", "progress", "blockers",
         "expected_end_at", "visibility"),
        ("title",)),
    "plan": Spec(
        PlanItem, PlanItemSerializer, "plan",
        ("title", "category", "priority", "planned_start_at", "planned_end_at",
         "dependencies", "status", "visibility"),
        ("title", "priority")),
    "thought": Spec(
        ThoughtItem, ThoughtItemSerializer, "thought",
        ("topic", "content", "category", "confidence", "requires_discussion",
         "status", "visibility"),
        ("topic", "content")),
}


# ─────────────────────────────────────────── 공통
def _visible(qs, user):
    """
    비공개 항목은 **남에게 존재조차 안 보입니다.**

    관리자라고 예외를 두지 않습니다 — 개인의 미확정 생각까지 관리자가 들여다볼
    수 있으면 아무도 솔직하게 적지 않습니다.
    """
    return qs.filter(Q(visibility=Visibility.TEAM) | Q(owner=user))


def _apply(obj, data, fields):
    changed = {}
    for f in fields:
        if f in data:
            old = getattr(obj, f, None)
            if old != data[f]:
                changed[f] = {"from": old, "to": data[f]}
            setattr(obj, f, data[f])
    return changed


def _validate(spec, data, obj=None):
    if spec.model is WorkItem and "progress" in data:
        try:
            p = int(data["progress"])
        except (TypeError, ValueError):
            raise BordoError("VALIDATION_ERROR", "progress 는 0~100 정수입니다.")
        if not 0 <= p <= 100:
            raise BordoError("VALIDATION_ERROR", "progress 는 0~100 입니다.",
                             details={"progress": data["progress"]})
        data["progress"] = p

    if spec.model is ThoughtItem and "confidence" in data:
        try:
            c = float(data["confidence"])
        except (TypeError, ValueError):
            raise BordoError("VALIDATION_ERROR", "confidence 는 0~1 실수입니다.")
        if not 0.0 <= c <= 1.0:
            raise BordoError("VALIDATION_ERROR", "confidence 는 0~1 입니다.",
                             details={"confidence": data["confidence"]})
        data["confidence"] = c

    for f in ("status", "priority", "visibility"):
        if f in data:
            allowed = {
                "status": (WorkStatus.values if spec.model is not ThoughtItem
                           else ThoughtItem.Status.values),
                "priority": [p for p in ("P0", "P1", "P2", "P3")],
                "visibility": Visibility.values,
            }[f]
            if data[f] not in allowed:
                raise BordoError("VALIDATION_ERROR", f"{f} 값이 올바르지 않습니다.",
                                 details={f: data[f], "allowed": list(allowed)})

    # 날짜는 여기서 파싱합니다. 문자열인 채로 create() 에 넘기면 DB 에는 들어가도
    # 메모리 인스턴스가 str 이라 곧바로 직렬화할 때 터집니다.
    for f in ("expected_end_at", "planned_start_at", "planned_end_at"):
        if f in data:
            data[f] = parse_dt(data[f], f)

    if spec.model is PlanItem:
        if "dependencies" in data:
            _check_dependencies(data["dependencies"], obj)
        start = data.get("planned_start_at", getattr(obj, "planned_start_at", None))
        end = data.get("planned_end_at", getattr(obj, "planned_end_at", None))
        if start and end and end < start:
            raise BordoError("VALIDATION_ERROR",
                             "planned_end_at 이 planned_start_at 보다 앞섭니다.",
                             details={"planned_start_at": start, "planned_end_at": end})


def _check_dependencies(deps, obj):
    """
    순환 의존을 막습니다.

    A→B→A 를 허용하면 간트에서 시작할 수 있는 일이 하나도 없는 계획이 만들어지고,
    화면은 그걸 조용히 빈 목록으로 그립니다.
    """
    if not isinstance(deps, list):
        raise BordoError("VALIDATION_ERROR", "dependencies 는 배열입니다.")
    if not deps:
        return
    if obj and str(obj.id) in {str(d) for d in deps}:
        raise BordoError("VALIDATION_ERROR", "자기 자신을 선행 작업으로 둘 수 없습니다.")

    known = {str(p.id): [str(d) for d in (p.dependencies or [])]
             for p in PlanItem.objects.filter(id__in=deps)}
    missing = [str(d) for d in deps if str(d) not in known]
    if missing:
        raise BordoError("STATE_NOT_FOUND", "없는 계획을 선행으로 지정했습니다.",
                         details={"plan_ids": missing})
    if not obj:
        return

    # obj 를 거쳐 돌아오는 경로가 있는지 폭 우선으로 확인합니다.
    graph = {str(p.id): [str(d) for d in (p.dependencies or [])]
             for p in PlanItem.objects.filter(project=obj.project)}
    graph[str(obj.id)] = [str(d) for d in deps]
    seen, stack = set(), [str(obj.id)]
    while stack:
        node = stack.pop()
        for nxt in graph.get(node, []):
            if nxt == str(obj.id):
                raise BordoError("VALIDATION_ERROR",
                                 "순환 의존입니다. 선행 관계가 돌아옵니다.",
                                 details={"cycle_at": node})
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)


def _log(project, user, kind, obj, detail):
    ActivityEvent.objects.create(project=project, actor=user, kind=kind,
                                 target_id=obj.id, detail=detail or {})


# ─────────────────────────────────────────── 목록 · 생성
def collection(request, project_id, key):
    spec = SPECS[key]
    project, _ = project_membership(request.user, project_id)

    if request.method == "GET":
        qs = _visible(spec.model.objects.filter(project=project), request.user)

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
        return Response(listing(spec.serializer(rows, many=True).data))

    data = dict(request.data)
    for f in spec.required:
        value = data.get(f)
        if isinstance(value, str):
            value = value.strip()
            data[f] = value
        if not value:
            raise BordoError("VALIDATION_ERROR", f"{f} 은(는) 필수입니다.")
    _validate(spec, data)

    payload = {f: data[f] for f in spec.writable if f in data}
    with transaction.atomic():
        obj = spec.model.objects.create(project=project, owner=request.user, **payload)
        _log(project, request.user, f"{spec.kind}.created", obj, {"title": str(obj)})
    publish(project.id, f"{spec.kind}.created",
            {"id": str(obj.id), "owner_id": str(request.user.id)})
    return Response(spec.serializer(obj).data, status=201)


def item(request, key, pk):
    spec = SPECS[key]
    obj = (spec.model.objects.filter(pk=pk)
           .select_related("project", "owner").first())
    if not obj:
        raise BordoError("STATE_NOT_FOUND", "항목을 찾을 수 없습니다.")
    project_membership(request.user, obj.project_id)
    # 남의 비공개 항목은 없는 것처럼 굽니다.
    if obj.visibility == Visibility.PRIVATE and obj.owner_id != request.user.id:
        raise BordoError("STATE_NOT_FOUND", "항목을 찾을 수 없습니다.")

    if request.method == "GET":
        return Response(spec.serializer(obj).data)

    if obj.owner_id != request.user.id:
        raise BordoError("TEAM_ACCESS_DENIED",
                         "본인 것만 고치거나 지울 수 있습니다. "
                         "이건 `지금 내 상태` 라 남이 대신 쓰면 뜻이 없습니다.")

    if request.method == "DELETE":
        with transaction.atomic():
            _log(obj.project, request.user, f"{spec.kind}.deleted", obj,
                 {"title": str(obj)})
            obj.delete()
        publish(obj.project_id, f"{spec.kind}.deleted", {"id": str(pk)})
        return Response(status=204)

    data = dict(request.data)
    _validate(spec, data, obj=obj)
    changed = _apply(obj, data, spec.writable)
    if not changed:
        return Response(spec.serializer(obj).data)

    with transaction.atomic():
        obj.save()
        _log(obj.project, request.user, f"{spec.kind}.updated", obj, changed)
    publish(obj.project_id, f"{spec.kind}.updated",
            {"id": str(obj.id), "changed": list(changed)})
    body = spec.serializer(obj).data
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
