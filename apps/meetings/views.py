"""
회의 · 플로우 화면.

이 화면이 서비스의 차별점이라 조회 경로를 특히 얕게 잡았습니다.
플로우 그래프는 `flow_edge` 한 테이블만 읽으면 그려집니다 — 노드 이름과
방향 표기가 행 안에 들어 있어 사용자 테이블을 조인하지 않습니다.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.agent.models import PendingQuestion
from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import meeting_access, project_membership
from apps.common.views import listing
from config.errors import BordoError

from .models import (Agenda, AiBriefing, Attendance, BriefingConfirmation,
                     BriefingRequest, FlowCategory, FlowContentType, FlowEdge,
                     FlowFilterPreset, Meeting, MeetingDocumentRef,
                     MeetingParticipant, MeetingStatus, MeetingSummary, Surface,
                     Utterance)
from .serializers import (AgendaSerializer, AiBriefingSerializer, DocumentRefSerializer,
                          FlowEdgeSerializer, FlowFilterPresetSerializer,
                          MeetingSerializer, MeetingSummarySerializer, UtteranceSerializer)

WORK_TYPES = {FlowContentType.DOCUMENT, FlowContentType.PLAN}
MEETING_TYPES = {FlowContentType.OPINION, FlowContentType.REQUEST,
                 FlowContentType.CHANGE, FlowContentType.SCHEDULE,
                 FlowContentType.CONCLUSION, FlowContentType.ETC}
#: 화면 필터에 그리는 순서. 와이어프레임 `필터링 > 내용` 의 위에서 아래 순입니다.
MEETING_TYPE_ORDER = [FlowContentType.OPINION, FlowContentType.REQUEST,
                      FlowContentType.CHANGE, FlowContentType.SCHEDULE,
                      FlowContentType.CONCLUSION, FlowContentType.ETC]


# ─────────────────────────────────────────── 회의
@api_view(["GET", "POST"])
def meetings(request, project_id):
    project, _ = project_membership(request.user, project_id)
    if request.method == "GET":
        rows = (Meeting.objects.filter(project=project)
                .prefetch_related("participants")[:50])
        return Response(listing(MeetingSerializer(rows, many=True).data))

    title = (request.data.get("title") or "").strip()
    scheduled_at = request.data.get("scheduled_at")
    if not title or not scheduled_at:
        raise BordoError("VALIDATION_ERROR", "title 과 scheduled_at 은 필수입니다.")
    with transaction.atomic():
        meeting = Meeting.objects.create(
            project=project, project_name=project.name, title=title,
            scheduled_at=scheduled_at,
            duration_min=int(request.data.get("duration_min", 60)),
            discord_channel_id=request.data.get("discord_channel_id", "") or "",
            created_by=request.user,
        )
        from apps.orgs.models import ProjectMember
        members = (ProjectMember.objects.filter(project=project)
                   .select_related("user"))
        MeetingParticipant.objects.bulk_create([
            MeetingParticipant(meeting=meeting, user=m.user, user_name=m.user.name)
            for m in members])
        MeetingSummary.objects.create(meeting=meeting)
    meeting.refresh_from_db()
    return Response(MeetingSerializer(meeting).data, status=201)


@api_view(["GET", "PATCH", "DELETE"])
def meeting_detail(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)

    if request.method == "GET":
        return Response(MeetingSerializer(meeting).data)

    if request.method == "PATCH":
        if meeting.is_locked:
            raise BordoError("MEETING_LOCKED",
                             details={"status": meeting.status})
        want = request.headers.get("If-Match")
        if want and int(want.strip('"')) != meeting.version:
            raise BordoError("REFERENCED_BY_OTHERS", "그사이 다른 사람이 수정했습니다.",
                             details={"current_version": meeting.version}, status=409)
        for f in ("title", "scheduled_at", "duration_min", "discord_channel_id"):
            if f in request.data:
                setattr(meeting, f, request.data[f])
        meeting.version += 1
        meeting.save()
        return Response(MeetingSerializer(meeting).data)

    if meeting.status == MeetingStatus.ACTIVE:
        raise BordoError("MEETING_NOT_ACTIVE",
                         "진행 중인 회의는 삭제할 수 없습니다. 먼저 종료하십시오.", status=409)
    deleted_at = meeting.soft_delete()
    return Response({"id": str(meeting.id), "deleted_at": deleted_at,
                     "restorable_until": timezone.now() + timedelta(days=30)})


@api_view(["POST"])
def delegate(request, meeting_id):
    """
    대리 참석 활성화 / 해제.

    토글이라 PATCH 로 두는 편이 맞지만, 활성화 시점에 대리인 설정 스냅샷을 남기고
    참석 상태를 함께 바꾸는 부수효과가 있어 POST 로 둡니다.
    """
    meeting = meeting_access(request.user, meeting_id)
    p = MeetingParticipant.objects.filter(meeting=meeting, user=request.user).first()
    if not p:
        raise BordoError("STATE_NOT_FOUND", "이 회의의 참석자가 아닙니다.")
    enabled = bool(request.data.get("enabled", True))
    p.delegated = enabled
    p.delegate_prompt = request.data.get("prompt", "") or ""
    p.attendance = Attendance.DELEGATED if enabled else Attendance.PENDING
    p.save(update_fields=["delegated", "delegate_prompt", "attendance", "updated_at"])
    return Response({"meeting_id": str(meeting.id), "user_id": str(request.user.id),
                     "delegated": p.delegated, "attendance": p.attendance,
                     "prompt": p.delegate_prompt})


# ─────────────────────────────────────────── 플로우
def _resolve_category(raw):
    cat = (raw or FlowCategory.MEETING).upper()
    if cat not in FlowCategory.values:
        raise BordoError("VALIDATION_ERROR", "category 는 WORK 또는 MEETING 입니다.")
    return cat


def _filtered_edges(meeting, params):
    """
    필터는 전부 DB 에서 겁니다.

    플로우가 이 서비스의 차별점인데 필터를 파이썬에서 돌리면 회의가 길어질수록
    화면이 느려집니다.
    """
    cat = _resolve_category(params.get("category"))
    qs = FlowEdge.objects.filter(meeting=meeting, category=cat)

    types = params.get("content_types")
    if types:
        wanted = [t.strip().upper() for t in types.split(",") if t.strip()]
        allowed = WORK_TYPES if cat == FlowCategory.WORK else MEETING_TYPES
        bad = [t for t in wanted if t not in {a.value for a in allowed}]
        if bad:
            raise BordoError("VALIDATION_ERROR",
                             f"{cat} 모드에서 쓸 수 없는 content_type 입니다.",
                             details={"invalid": bad,
                                      "allowed": [a.value for a in allowed]})
        qs = qs.filter(content_type__in=wanted)

    surfaces = params.get("surfaces")
    if surfaces:
        qs = qs.filter(surface__in=[s.strip().upper() for s in surfaces.split(",")])

    since = params.get("since_minutes")
    if since:
        qs = qs.filter(occurred_at__gte=timezone.now() - timedelta(minutes=int(since)))

    people = params.get("participant_ids")
    if people:
        wanted = {p.strip() for p in people.split(",") if p.strip()}
        # participant_ids 가 JSON 배열이라 DB 마다 연산자가 달라, 여기서만 파이썬으로 거릅니다.
        qs = [e for e in qs if wanted & set(e.participant_ids or [])]
    return cat, qs


def _arrows(edges):
    """
    낱개 전달을 화면의 화살표로 묶습니다.

    와이어프레임의 화살표는 사람 쌍마다 **하나**이고 그 위에 `의견 3`
    `요청사항 5` 처럼 종류별 개수 뱃지가 붙습니다. 낱개를 그대로 그리면
    두 사람 사이에 선이 열 개 겹쳐 그림이 뭉갭니다.

    묶는 걸 서버가 하는 이유 — 필터가 걸린 상태에서 클라이언트가 세면
    화면마다 숫자가 갈립니다. 집계 기준은 한 곳에만 있어야 합니다.
    """
    buckets = {}
    for e in edges:
        frm = (e.from_node or {}).get("id")
        tos = tuple(n.get("id") for n in (e.to_nodes or []))
        key = (frm, tos)
        b = buckets.setdefault(key, {
            "id": f"{frm}->{'|'.join(t for t in tos if t)}",
            "from_node_id": frm,
            "to_node_ids": list(tos),
            "direction_label": e.direction_label,
            "counts": {},
            "total_count": 0,
            "avatars": [],
            "extra_participant_count": e.extra_participant_count,
            "latest_occurred_at": e.occurred_at,
            "opacity": e.opacity,
            "surfaces": set(),
        })
        slot = b["counts"].setdefault(e.content_type,
                                     {"content_type": e.content_type,
                                      "label": FlowContentType(e.content_type).label,
                                      "count": 0, "edge_ids": []})
        slot["count"] += 1
        slot["edge_ids"].append(str(e.id))
        b["total_count"] += 1
        b["surfaces"].add(e.surface)
        if e.occurred_at and e.occurred_at > b["latest_occurred_at"]:
            b["latest_occurred_at"] = e.occurred_at
            b["opacity"] = e.opacity            # 진하기는 가장 최근 것을 따릅니다
        for n in (e.to_nodes or []):
            url = n.get("avatar_url")
            if url and url not in b["avatars"]:
                b["avatars"].append(url)

    order = {t: i for i, t in enumerate(MEETING_TYPE_ORDER)}
    out = []
    for b in buckets.values():
        counts = sorted(b["counts"].values(),
                        key=lambda c: order.get(c["content_type"], 99))
        out.append({
            "id": b["id"], "from_node_id": b["from_node_id"],
            "to_node_ids": b["to_node_ids"],
            "direction_label": b["direction_label"],
            "counts": counts, "total_count": b["total_count"],
            "participant_avatar_urls": b["avatars"][:4],
            "extra_participant_count": b["extra_participant_count"],
            "latest_occurred_at": b["latest_occurred_at"],
            "opacity": b["opacity"],
            "surfaces": sorted(b["surfaces"]),
        })
    out.sort(key=lambda a: a["latest_occurred_at"])
    return out


@api_view(["GET"])
def flow(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    cat, edges = _filtered_edges(meeting, request.query_params)
    edges = list(edges)

    nodes, seen = [], set()
    for e in edges:
        for n in [e.from_node] + list(e.to_nodes or []):
            if n and n.get("id") not in seen:
                seen.add(n.get("id"))
                nodes.append(n)

    participants = list(meeting.participants.select_related("user"))
    present = {e.content_type for e in edges}
    return Response({
        "meeting_id": str(meeting.id),
        "meeting_label": f"{meeting.scheduled_at:%-m/%-d} {meeting.title}",
        "category": cat,
        "nodes": nodes,
        "arrows": _arrows(edges),
        "filter_options": {
            # 실제로 존재하는 값만 내려줍니다. 전체 목록을 주면 체크해도 안 걸리는
            # 항목이 생겨 사용자가 헷갈립니다.
            "participants": [{"id": str(p.user_id), "label": p.user_name, "kind": "USER"}
                             for p in participants],
            "content_types": [t for t in MEETING_TYPE_ORDER if t in present]
                             or sorted(present),
            "surfaces": sorted({e.surface for e in edges}),
        },
    })


@api_view(["GET"])
def indexes(request, meeting_id):
    """좌측 인덱스. 작업 모드는 문서, 회의 모드는 안건."""
    meeting = meeting_access(request.user, meeting_id)
    cat = _resolve_category(request.query_params.get("category"))
    edges = FlowEdge.objects.filter(meeting=meeting, category=cat)

    if cat == FlowCategory.MEETING:
        edge_map = {}
        for e in edges:
            if e.agenda_id:
                edge_map.setdefault(e.agenda_id, []).append(e.id)
        rows = Agenda.objects.filter(meeting=meeting)
        return Response(listing([{
            "id": str(a.id), "label": a.title, "kind": "AGENDA",
            "related_edge_ids": [str(i) for i in edge_map.get(a.id, [])],
        } for a in rows]))

    edge_map = {}
    for e in edges:
        if e.document_id:
            edge_map.setdefault(e.document_id, []).append(e.id)
    docs = MeetingDocumentRef.objects.filter(id__in=list(edge_map.keys()))
    return Response(listing([{
        "id": str(d.id), "label": d.title, "kind": "DOCUMENT",
        "related_edge_ids": [str(i) for i in edge_map.get(d.id, [])],
    } for d in docs]))


@api_view(["GET"])
def summary_table(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    summary, _ = MeetingSummary.objects.get_or_create(meeting=meeting)
    return Response(MeetingSummarySerializer(summary).data)


@api_view(["GET"])
def context(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    rows = Utterance.objects.filter(meeting=meeting)
    return Response(listing(UtteranceSerializer(rows, many=True).data))


@api_view(["GET"])
def agendas(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    edge_map = {}
    for e in FlowEdge.objects.filter(meeting=meeting).exclude(agenda_id=None):
        edge_map.setdefault(e.agenda_id, []).append(e.id)
    rows = Agenda.objects.filter(meeting=meeting)
    return Response(listing(
        AgendaSerializer(rows, many=True, context={"edge_map": edge_map}).data))


@api_view(["GET", "PATCH", "DELETE"])
def agenda_detail(request, meeting_id, agenda_id):
    meeting = meeting_access(request.user, meeting_id)
    agenda = Agenda.objects.filter(meeting=meeting, pk=agenda_id).first()
    if not agenda:
        raise BordoError("STATE_NOT_FOUND", "안건을 찾을 수 없습니다.")
    edge_ids = list(FlowEdge.objects.filter(agenda=agenda).values_list("id", flat=True))

    if request.method == "GET":
        return Response(AgendaSerializer(
            agenda, context={"edge_map": {agenda.id: edge_ids}}).data)

    if request.method == "PATCH":
        for f in ("title", "sort_order", "category", "status", "content"):
            if f in request.data:
                setattr(agenda, f, request.data[f])
        agenda.save()
        return Response(AgendaSerializer(
            agenda, context={"edge_map": {agenda.id: edge_ids}}).data)

    if meeting.is_locked:
        raise BordoError("MEETING_LOCKED", "시작된 회의의 안건은 지울 수 없습니다.")
    if edge_ids:
        raise BordoError("REFERENCED_BY_OTHERS",
                         "이 안건으로 오간 논의가 있어 지울 수 없습니다.",
                         details={"edge_ids": [str(i) for i in edge_ids]})
    agenda.delete()
    return Response(status=204)


@api_view(["GET"])
def flow_edge_detail(request, edge_id):
    """화살표를 클릭했을 때 우측에 뜨는 내용."""
    edge = (FlowEdge.objects.filter(pk=edge_id)
            .select_related("meeting", "agenda", "document").first())
    if not edge:
        raise BordoError("STATE_NOT_FOUND", "화살표를 찾을 수 없습니다.")
    meeting_access(request.user, edge.meeting_id)

    body = {"edge": FlowEdgeSerializer(edge).data,
            "delivery_context": [], "document": None, "agenda": None}
    if edge.document:
        body["document"] = DocumentRefSerializer(edge.document).data
        body["delivery_context"] = edge.document.delivery_context
    if edge.agenda:
        body["agenda"] = AgendaSerializer(
            edge.agenda, context={"edge_map": {edge.agenda_id: [edge.id]}}).data
    return Response(body)


# ─────────────────────────────────────────── AI 브리핑
def _chips(meeting, user):
    """
    `회의 한눈에 보기` 아래 정보 위치 칩.

    요약을 읽고 곧바로 "그게 회의 어디였지"로 건너뛰는 통로라, 개수와 함께
    **어느 화살표를 포커싱할지**(edge_ids)를 같이 내려줍니다. 클라이언트가
    다시 물어보면 칩을 누르는 순간 왕복이 한 번 더 생깁니다.
    """
    chips = {}
    # 회의 모드만 셉니다. 칩은 `회의 한눈에 보기` 아래 붙는 것이라
    # 작업 모드(문서·계획)까지 섞으면 요약과 숫자가 안 맞습니다.
    for e in (FlowEdge.objects.filter(meeting=meeting, category=FlowCategory.MEETING)
              .only("id", "content_type")):
        c = chips.setdefault(e.content_type,
                             {"content_type": e.content_type,
                              "label": FlowContentType(e.content_type).label,
                              "count": 0, "edge_ids": []})
        c["count"] += 1
        c["edge_ids"].append(str(e.id))
    order = {t: i for i, t in enumerate(MEETING_TYPE_ORDER)}
    return sorted(chips.values(), key=lambda c: order.get(c["content_type"], 99))


def _match(needle, *fields):
    return not needle or any(needle in (f or "").lower() for f in fields)


@api_view(["GET"])
def ai_briefing(request, meeting_id):
    """
    우측 사이드바 `Zero 브리핑` 전체.

    섹션 순서는 와이어프레임 그대로 —
    회의 한눈에 보기(+칩) → 확인이 필요해요 → 답변이 필요해요 → 나에게 요청한 내용.
    """
    meeting = meeting_access(request.user, meeting_id)
    briefing = AiBriefing.objects.filter(meeting=meeting, user=request.user).first()
    if not briefing:
        raise BordoError("STATE_NOT_FOUND", "아직 브리핑이 준비되지 않았습니다.")

    q = (request.query_params.get("q") or "").strip().lower()

    confirmations = [{
        "id": str(c.id), "title": c.title, "body": c.body,
        "edge_id": str(c.edge_id) if c.edge_id else None,
        "agenda_id": str(c.agenda_id) if c.agenda_id else None,
        "occurred_at": c.occurred_at, "confirmed_at": c.confirmed_at,
    } for c in BriefingConfirmation.objects.filter(
        meeting=meeting, user=request.user, confirmed_at__isnull=True)
        if _match(q, c.title, c.body)]

    requests_to_me = [{
        "id": str(r.id), "title": r.title, "requester_name": r.requester_name,
        "note": r.note, "due_at": r.due_at,
        "edge_id": str(r.edge_id) if r.edge_id else None,
        "task_id": str(r.accepted_task_id) if r.accepted_task_id else None,
    } for r in BriefingRequest.objects.filter(meeting=meeting, user=request.user)
        if _match(q, r.title, r.requester_name, r.note)]

    needs = [{"question_id": str(x.id), "asker_name": x.asker_name, "title": x.title,
              "body": x.body, "asked_at": x.created_at,
              "chat_room_id": str(x.chat_room_id) if x.chat_room_id else None,
              "answered_at": x.answered_at}
             for x in PendingQuestion.objects.filter(
                 meeting=meeting, target_user=request.user, answered_at__isnull=True)
             if _match(q, x.title, x.body, x.asker_name)]

    if briefing.read_at is None:
        briefing.read_at = timezone.now()
        briefing.save(update_fields=["read_at"])

    return Response(AiBriefingSerializer(briefing, context={
        "needs_answer": needs,
        "location_chips": _chips(meeting, request.user),
        "needs_confirmation": confirmations,
        "requests_to_me": requests_to_me,
    }).data)


@api_view(["POST"])
def briefing_confirm(request, confirmation_id):
    """`확인이 필요해요` 카드를 확인 처리합니다. 확인은 사람마다 따로 남습니다."""
    row = (BriefingConfirmation.objects.filter(pk=confirmation_id, user=request.user)
           .select_related("meeting").first())
    if not row:
        raise BordoError("STATE_NOT_FOUND", "확인 항목을 찾을 수 없습니다.")
    meeting_access(request.user, row.meeting_id)
    if row.confirmed_at is None:
        row.confirmed_at = timezone.now()
        row.save(update_fields=["confirmed_at", "updated_at"])
    return Response({"id": str(row.id), "title": row.title, "body": row.body,
                     "edge_id": str(row.edge_id) if row.edge_id else None,
                     "agenda_id": str(row.agenda_id) if row.agenda_id else None,
                     "occurred_at": row.occurred_at,
                     "confirmed_at": row.confirmed_at})


@api_view(["POST"])
def briefing_request_accept(request, request_id):
    """
    `나에게 요청한 내용` 을 태스크로 받습니다.

    사람이 직접 받아들인 것이므로 `TODO` 로 시작합니다 — AI 후보가 아니라
    승인 단계를 거칠 이유가 없습니다.
    """
    from apps.tasks.models import Task, TaskEvent, TaskStatus

    row = (BriefingRequest.objects.filter(pk=request_id, user=request.user)
           .select_related("meeting").first())
    if not row:
        raise BordoError("STATE_NOT_FOUND", "요청을 찾을 수 없습니다.")
    meeting = meeting_access(request.user, row.meeting_id)
    if row.accepted_task_id:
        raise BordoError("DUPLICATE_EVENT", "이미 받은 요청입니다.",
                         details={"task_id": str(row.accepted_task_id)})

    due_at = parse_dt(request.data.get("due_at"), "due_at") or row.due_at
    with transaction.atomic():
        task = Task.objects.create(
            project_id=meeting.project_id, title=row.title,
            description=row.note or f"{row.requester_name}님이 회의에서 요청했습니다.",
            priority=request.data.get("priority") or "P1",
            assignee=request.user, due_at=due_at,
            created_by=request.user, created_by_agent=False,
            source_meeting=meeting, status=TaskStatus.TODO)
        TaskEvent.objects.create(task=task, actor=request.user,
                                 action="accept_request", to_status=task.status,
                                 detail={"briefing_request_id": str(row.id)})
        row.accepted_task_id = task.id
        row.save(update_fields=["accepted_task_id", "updated_at"])

    from apps.tasks.serializers import TaskSerializer
    from apps.tasks.views import recalc_progress
    recalc_progress(meeting.project)
    publish(meeting.project_id, "task.created",
            {"task_id": str(task.id), "from_briefing_request": str(row.id)})
    return Response({"request_id": str(row.id), "task": TaskSerializer(task).data})


@api_view(["GET"])
def meeting_pending_questions(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    rows = PendingQuestion.objects.filter(meeting=meeting, target_user=request.user,
                                          answered_at__isnull=True)
    return Response(listing([{
        "question_id": str(q.id), "asker_name": q.asker_name, "title": q.title,
        "body": q.body, "asked_at": q.created_at,
        "chat_room_id": str(q.chat_room_id) if q.chat_room_id else None} for q in rows]))


# ─────────────────────────────────────────── 필터 프리셋
@api_view(["GET", "POST"])
def flow_filters(request):
    if request.method == "GET":
        rows = FlowFilterPreset.objects.filter(user=request.user)
        return Response(listing(FlowFilterPresetSerializer(rows, many=True).data))
    s = FlowFilterPresetSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    s.save(user=request.user)
    return Response(s.data, status=201)


@api_view(["PATCH", "DELETE"])
def flow_filter_detail(request, preset_id):
    preset = FlowFilterPreset.objects.filter(pk=preset_id, user=request.user).first()
    if not preset:
        raise BordoError("STATE_NOT_FOUND", "프리셋을 찾을 수 없습니다.")
    if request.method == "DELETE":
        preset.delete()
        return Response(status=204)
    s = FlowFilterPresetSerializer(preset, data=request.data, partial=True)
    s.is_valid(raise_exception=True)
    s.save()
    return Response(s.data)
