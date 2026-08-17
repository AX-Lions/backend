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
from apps.common.display import day_label, user_tz
from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import meeting_access, project_membership
from apps.common.views import listing
from config.errors import BordoError

from .models import (Agenda, AiBriefing, Attendance, BriefingConfirmation,
                     BriefingRequest, FlowCategory, FlowContentType, FlowEdge, FlowSource,
                     FlowFilterPreset, Meeting, MeetingDocumentRef,
                     MeetingParticipant, MeetingStatus, MeetingSummary, Surface,
                     Utterance)
from .serializers import (AgendaSerializer, AiBriefingSerializer,
                          BriefingConfirmationSerializer, BriefingRequestSerializer,
                          DocumentRefSerializer, FlowEdgeSerializer,
                          FlowFilterPresetSerializer, MeetingSerializer,
                          MeetingSummarySerializer, UtteranceSerializer)

# 작업 모드 필터 5칸과 1:1 입니다.
#
# DOCUMENT·PLAN 이 아닙니다 — 그 둘은 Figma 어디에도 없는 초기 설계 잔재입니다.
WORK_TYPES = {FlowContentType.WORK, FlowContentType.REVISION,
              FlowContentType.FEEDBACK, FlowContentType.SHARE,
              FlowContentType.AI_LOOKUP}
#: 작업 모드 필터에 그리는 순서.
WORK_TYPE_ORDER = [FlowContentType.WORK, FlowContentType.REVISION,
                   FlowContentType.FEEDBACK, FlowContentType.SHARE,
                   FlowContentType.AI_LOOKUP]
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


def _filtered_edges(scope, params, period=None):
    """
    필터는 전부 DB 에서 겁니다.

    플로우가 이 서비스의 차별점인데 필터를 파이썬에서 돌리면 회의가 길어질수록
    화면이 느려집니다.
    """
    cat = _resolve_category(params.get("category"))
    qs = FlowEdge.objects.filter(**scope, category=cat)

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

    # 작업 모드 `필터링 > 출처`. Github · Figma · Notion.
    #
    # surface(서비스/Discord)와 다른 축입니다 — surface 는 일이 일어난 곳,
    # source 는 산출물이 있는 곳입니다.
    sources = params.get("sources")
    if sources:
        wanted = [x.strip().upper() for x in sources.split(",") if x.strip()]
        bad = [x for x in wanted if x not in FlowSource.values]
        if bad:
            raise BordoError("VALIDATION_ERROR", "알 수 없는 출처입니다.",
                             details={"invalid": bad, "allowed": FlowSource.values})
        qs = qs.filter(source__in=wanted)

    since = params.get("since_minutes")
    if since:
        qs = qs.filter(occurred_at__gte=timezone.now() - timedelta(minutes=int(since)))

    # 기간은 반드시 DB 에서 자릅니다.
    #
    # 파이썬으로 거르면 프로젝트의 **모든** 엣지를 메모리에 올린 뒤 버리게 되고,
    # 프로젝트가 오래될수록 "이번 주" 를 봐도 느려집니다. `(project, category,
    # occurred_at)` 인덱스를 붙여 둔 이유가 이 필터입니다.
    if period:
        qs = qs.filter(occurred_at__range=period)

    people = params.get("participant_ids")
    if people:
        wanted = {p.strip() for p in people.split(",") if p.strip()}
        # participant_ids 가 JSON 배열이라 DB 마다 연산자가 달라, **여기서만**
        # 파이썬으로 거릅니다. 위에서 기간·종류로 이미 좁혀 둔 뒤라야 합니다.
        qs = [e for e in qs if wanted & set(e.participant_ids or [])]
    return cat, qs


def _arrows(edges, type_order=None):
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

    # 뱃지 순서는 카테고리마다 다릅니다. 작업 모드에서 회의 순서를 쓰면
    # 다섯 종류가 전부 기본 순위로 밀려 DB 인출 순서대로 붙습니다.
    order = {t: i for i, t in enumerate(type_order or MEETING_TYPE_ORDER)}
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
    cat, edges = _filtered_edges({"meeting": meeting}, request.query_params)
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
        # `%-m` 은 glibc 확장이라 Windows 개발 환경에서 ValueError 로 죽습니다.
        # 서버에서는 돌고 개발자 PC 에서만 500 이 나 원인을 찾기 어렵습니다.
        "meeting_label": f"{day_label(meeting.scheduled_at, user_tz(request.user))} "
                         f"{meeting.title}",
        "category": cat,
        "nodes": nodes,
        "arrows": _arrows(edges, MEETING_TYPE_ORDER),
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
def project_flow(request, project_id):
    """
    작업(Work) 플로우.

    ## 왜 회의 조회와 따로 있는가

    회의 플로우는 회의 하나가 스코프인데, **작업 플로우는 기간이 스코프입니다.**
    화면 헤더가 `8.10 - 8.16 작업 흐름` 입니다. 붙일 회의가 없어 같은 경로를
    쓸 수 없습니다.

    ## 응답 모양은 회의와 같게 둡니다

    `meeting_label` 이 `period_label` 로 바뀌는 것 말고는 동일합니다.
    프론트가 차트 렌더러를 **하나만** 만들면 되도록 하기 위해서입니다.

    ## 진하기를 저장값으로 쓰지 않습니다

    회의는 끝나면 구간이 고정되지만, 작업 플로우의 구간은 **사용자가 고르는
    기간**입니다. 같은 엣지도 `8.10-8.16` 으로 볼 때와 `8.1-8.31` 로 볼 때
    진하기가 달라야 합니다. 그래서 조회할 때마다 계산합니다.
    """
    project, _ = project_membership(request.user, project_id)

    to_at = parse_dt(request.query_params.get("to"), "to") or timezone.now()
    from_at = (parse_dt(request.query_params.get("from"), "from")
               or to_at - timedelta(days=7))
    if from_at > to_at:
        raise BordoError("VALIDATION_ERROR", "from 이 to 보다 뒤입니다.")

    params = request.query_params.copy()
    params.setdefault("category", FlowCategory.WORK)
    cat, edges = _filtered_edges({"project": project}, params, period=(from_at, to_at))
    edges = list(edges)

    # 구간을 한 번만 구해 넘깁니다. 엣지마다 다시 계산하면 N+1 입니다.
    for e in edges:
        e.opacity = e.compute_opacity(oldest=from_at, newest=to_at)

    nodes, seen = [], set()
    for e in edges:
        for n in [e.from_node] + list(e.to_nodes or []):
            if n and n.get("id") not in seen:
                seen.add(n.get("id"))
                nodes.append(n)

    order = WORK_TYPE_ORDER if cat == FlowCategory.WORK else MEETING_TYPE_ORDER
    present = {e.content_type for e in edges}
    from apps.orgs.models import ProjectMember
    members = ProjectMember.objects.filter(project=project).select_related("user")

    return Response({
        "project_id": str(project.id),
        # 서버가 문자열까지 만듭니다. 클라이언트가 만들면 시간대·표기가 갈립니다.
        "period_label": f"{from_at.month}.{from_at.day} - {to_at.month}.{to_at.day} "
                        f"{'작업' if cat == FlowCategory.WORK else '회의'} 흐름",
        "from": from_at, "to": to_at,
        "category": cat,
        "nodes": nodes,
        "arrows": _arrows(edges, order),
        "filter_options": {
            "participants": [{"id": str(m.user_id), "label": m.user.name, "kind": "USER"}
                             for m in members],
            "content_types": [t for t in order if t in present] or sorted(present),
            "surfaces": sorted({e.surface for e in edges}),
            # 실제로 존재하는 출처만 내려줍니다. 전체를 주면 체크해도 안 걸리는
            # 항목이 생겨 사용자가 헷갈립니다.
            "sources": sorted({e.source for e in edges if e.source}),
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

    # 작업 플로우 엣지에는 회의가 없습니다. 회의로만 권한을 보면
    # **정당한 프로젝트 참여자가 404 를 받습니다** — 화살표를 눌러도 안 열립니다.
    if edge.meeting_id:
        meeting_access(request.user, edge.meeting_id)
    else:
        project_membership(request.user, edge.project_id)

    body = {"edge": FlowEdgeSerializer(edge).data,
            "delivery_context": [], "document": None, "agenda": None,
            # `AI 조회` 화살표의 4단 상세로 가는 길입니다.
            #
            # 상세 자체는 `/agent-lookups/{id}` 가 이미 내려주는데, **엣지에서
            # 그 id 로 갈 방법이 없었습니다.** 화살표를 눌러도 빈 패널이 열립니다.
            #
            # 여기서 내용까지 합치지 않는 이유는 `apps.agent` 를 import 해야
            # 해서입니다 — `AgentLookup` 이 이쪽 `FlowEdge` 를 참조하므로
            # 순환이 됩니다. id 만 주고 프론트가 한 번 더 부릅니다.
            "lookup_id": None}
    lookup = edge.lookups.first()
    if lookup is not None:
        body["lookup_id"] = str(lookup.id)
    if edge.document:
        body["document"] = DocumentRefSerializer(edge.document).data
        body["delivery_context"] = edge.document.delivery_context
    if edge.agenda:
        body["agenda"] = AgendaSerializer(
            edge.agenda, context={"edge_map": {edge.agenda_id: [edge.id]}}).data
    return Response(body)


# ─────────────────────────────────────────── AI 브리핑
@api_view(["GET"])
def ai_briefing(request, meeting_id):
    meeting = meeting_access(request.user, meeting_id)
    briefing = AiBriefing.objects.filter(meeting=meeting, user=request.user).first()
    if not briefing:
        raise BordoError("STATE_NOT_FOUND", "아직 브리핑이 준비되지 않았습니다.")
    questions = PendingQuestion.objects.filter(meeting=meeting, target_user=request.user,
                                               answered_at__isnull=True)
    tz = user_tz(request.user)
    needs = [{"question_id": str(q.id), "asker_name": q.asker_name, "title": q.title,
              "body": q.body, "asked_at": q.created_at,
              # 화면은 `임수연 · 14:32` 한 줄입니다. 시각 환산을 클라이언트에
              # 맡기면 시간대가 다른 팀원끼리 같은 질문을 다른 시각으로 봅니다.
              "meta": f"{q.asker_name} · {q.created_at.astimezone(tz):%H:%M}",
              "chat_room_id": str(q.chat_room_id) if q.chat_room_id else None}
             for q in questions]

    confirmations = BriefingConfirmation.objects.filter(meeting=meeting,
                                                        user=request.user,
                                                        confirmed_at__isnull=True)
    requests_to_me = BriefingRequest.objects.filter(meeting=meeting, user=request.user,
                                                    accepted_at__isnull=True)

    if briefing.read_at is None:
        briefing.read_at = timezone.now()
        briefing.save(update_fields=["read_at"])
    return Response(AiBriefingSerializer(briefing, context={
        "needs_answer": needs,
        "needs_confirmation": BriefingConfirmationSerializer(confirmations, many=True).data,
        "requests_to_me": BriefingRequestSerializer(requests_to_me, many=True).data,
    }).data)


@api_view(["POST"])
def briefing_confirmation_confirm(request, confirmation_id):
    """
    `확인이 필요해요` 카드를 확인 처리합니다. 목록에서 빠집니다.

    **본인 것만 처리합니다.** 같은 변경을 여러 사람이 봐야 하는데 한 사람이
    눌렀다고 전부 사라지면, 아직 못 본 사람은 그 변경을 영영 모르고 지나갑니다.
    남의 카드를 지목하면 403 이 아니라 404 입니다 — 403 은 "그런 게 있긴 하다"를
    흘립니다.
    """
    card = BriefingConfirmation.objects.filter(pk=confirmation_id,
                                               user=request.user).first()
    if card is None:
        raise BordoError("STATE_NOT_FOUND", "확인할 항목을 찾을 수 없습니다.")

    if card.confirmed_at is None:
        card.confirmed_at = timezone.now()
        card.save(update_fields=["confirmed_at", "updated_at"])
    return Response({"confirmation_id": str(card.id), "confirmed_at": card.confirmed_at})


@api_view(["POST"])
def briefing_request_accept(request, request_id):
    """
    `나에게 요청한 내용` 을 태스크로 받습니다.

    **`TODO` 로 시작합니다.** 사람이 직접 눌러서 받은 것이라 승인 단계가 필요
    없습니다. 여기서 `PENDING_APPROVAL` 을 만들면 승인 큐가 남의 회의 요청으로
    가득 차 승인이라는 행위가 뜻을 잃습니다.
    """
    from apps.tasks.models import Task, TaskStatus

    card = BriefingRequest.objects.filter(pk=request_id, user=request.user).first()
    if card is None:
        raise BordoError("STATE_NOT_FOUND", "요청을 찾을 수 없습니다.")

    # 두 번 눌러도 태스크가 둘 생기지 않습니다. 목록에서 사라지기 전에 연타하는
    # 경우가 실제로 있습니다.
    if card.task_id:
        return Response({"request_id": str(card.id),
                         "task": {"id": str(card.task_id), "status": card.task.status}})

    with transaction.atomic():
        task = Task.objects.create(
            project_id=card.meeting.project_id,
            title=card.title[:200],
            description=card.note,
            status=TaskStatus.TODO,
            assignee=request.user,
            created_by=request.user,
            source_meeting_id=card.meeting_id,
        )
        card.task = task
        card.accepted_at = timezone.now()
        card.save(update_fields=["task", "accepted_at", "updated_at"])

    publish(card.meeting.project_id, "task.created", {"task_id": str(task.id)})
    return Response({"request_id": str(card.id),
                     "task": {"id": str(task.id), "status": task.status}})


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
