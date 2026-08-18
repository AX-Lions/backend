"""
현재 상태 — 뷰와 MCP 가 함께 쓰는 규칙.

검증(진행률 0~100 · confidence 0~1 · enum · 날짜 · 순환 의존)을 뷰에 두면
`/mcp` 로 들어오는 쓰기가 같은 규칙을 **한 벌 더** 갖게 되고, 한쪽만 고치면
갈라집니다. 웹으로 올리든 개인 AI 가 올리든 같은 함수를 지나게 합니다.
"""
from django.db.models import Q

from apps.common.parsing import parse_dt
from config.errors import BordoError

from .models import ActivityEvent, PlanItem, ThoughtItem, Visibility, WorkItem, WorkStatus


class Rules:
    def __init__(self, model, kind, writable, required):
        self.model = model
        self.kind = kind                  # 이벤트 이름 접두사
        self.writable = writable          # 생성·수정에서 받는 필드
        self.required = required          # 생성 시 필수


RULES = {
    "work": Rules(
        WorkItem, "work",
        ("title", "category", "summary", "status", "progress", "blockers",
         "expected_end_at", "visibility"),
        ("title",)),
    "plan": Rules(
        PlanItem, "plan",
        ("title", "category", "priority", "planned_start_at", "planned_end_at",
         "dependencies", "status", "visibility"),
        ("title", "priority")),
    "thought": Rules(
        ThoughtItem, "thought",
        ("topic", "content", "category", "confidence", "requires_discussion",
         "status", "visibility"),
        ("topic", "content")),
}


def visible(qs, user):
    """
    비공개 항목은 **남에게 존재조차 안 보입니다.**

    관리자라고 예외를 두지 않습니다 — 개인의 미확정 생각까지 관리자가 들여다볼
    수 있으면 아무도 솔직하게 적지 않습니다.
    """
    return qs.filter(Q(visibility=Visibility.TEAM) | Q(owner=user))


def require_fields(rules, data):
    """필수 필드를 확인하며 문자열은 양끝 공백을 지웁니다."""
    for f in rules.required:
        value = data.get(f)
        if isinstance(value, str):
            value = value.strip()
            data[f] = value
        if not value:
            raise BordoError("VALIDATION_ERROR", f"{f} 은(는) 필수입니다.")


def apply_fields(obj, data, fields):
    changed = {}
    for f in fields:
        if f in data:
            old = getattr(obj, f, None)
            if old != data[f]:
                changed[f] = {"from": old, "to": data[f]}
            setattr(obj, f, data[f])
    return changed


def validate(rules, data, obj=None):
    """`data` 를 제자리에서 정규화합니다. 틀리면 `VALIDATION_ERROR`."""
    if rules.model is WorkItem and "progress" in data:
        try:
            p = int(data["progress"])
        except (TypeError, ValueError):
            raise BordoError("VALIDATION_ERROR", "progress 는 0~100 정수입니다.")
        if not 0 <= p <= 100:
            raise BordoError("VALIDATION_ERROR", "progress 는 0~100 입니다.",
                             details={"progress": data["progress"]})
        data["progress"] = p

    if rules.model is ThoughtItem and "confidence" in data:
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
                "status": (WorkStatus.values if rules.model is not ThoughtItem
                           else ThoughtItem.Status.values),
                "priority": [p for p in ("P0", "P1", "P2", "P3")],
                "visibility": Visibility.values,
            }[f]
            if data[f] not in allowed:
                raise BordoError("VALIDATION_ERROR", f"{f} 값이 올바르지 않습니다.",
                                 details={f: data[f], "allowed": list(allowed)})

    if rules.model is WorkItem and "blockers" in data:
        if not isinstance(data["blockers"], list) or \
                any(not isinstance(b, str) for b in data["blockers"]):
            raise BordoError("VALIDATION_ERROR", "blockers 는 문자열 배열입니다.")

    # 날짜는 여기서 파싱합니다. 문자열인 채로 create() 에 넘기면 DB 에는 들어가도
    # 메모리 인스턴스가 str 이라 곧바로 직렬화할 때 터집니다.
    for f in ("expected_end_at", "planned_start_at", "planned_end_at"):
        if f in data:
            data[f] = parse_dt(data[f], f)

    if rules.model is PlanItem:
        if "dependencies" in data:
            check_dependencies(data["dependencies"], obj)
        start = data.get("planned_start_at", getattr(obj, "planned_start_at", None))
        end = data.get("planned_end_at", getattr(obj, "planned_end_at", None))
        if start and end and end < start:
            raise BordoError("VALIDATION_ERROR",
                             "planned_end_at 이 planned_start_at 보다 앞섭니다.",
                             details={"planned_start_at": start, "planned_end_at": end})


def check_dependencies(deps, obj):
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


def log_activity(project, user, kind, obj, detail):
    ActivityEvent.objects.create(project=project, actor=user, kind=kind,
                                 target_id=obj.id, detail=detail or {})
