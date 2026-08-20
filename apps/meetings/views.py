"""
회의 · 플로우 화면.

이 화면이 서비스의 차별점이라 조회 경로를 특히 얕게 잡았습니다.
플로우 그래프는 `flow_edge` 한 테이블만 읽으면 그려집니다 — 노드 이름과
방향 표기가 행 안에 들어 있어 사용자 테이블을 조인하지 않습니다.
"""
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.agent.models import PendingQuestion
from apps.agent.services.flow import agent_display_name
from apps.common.display import day_label, user_tz
from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import meeting_access, project_membership
from apps.common.views import listing
from apps.orgs.models import ProjectMember
from config.errors import BordoError

from .models import (Agenda, AiBriefing, Attendance, BriefingConfirmation,
                     BriefingRequest, FlowCategory, FlowContentType, FlowEdge,
                     FlowFilterPreset, FlowSource, Meeting, MeetingParticipant,
                     MeetingStatus, MeetingSummary, Surface, Utterance)
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

    from apps.orgs.models import ProjectMember

    title = (request.data.get("title") or "").strip()
    if not title:
        raise BordoError("VALIDATION_ERROR", "title 은 필수입니다.")
    # 들어오는 자리에서 파싱합니다. 그대로 `create()` 에 넣으면 DB 에는 들어가지만
    # 메모리 인스턴스는 문자열이라 바로 직렬화할 때 터집니다 — 잘못된 값이
    # 400 이 아니라 500 으로 나갔습니다.
    scheduled_at = parse_dt(request.data.get("scheduled_at"), "scheduled_at",
                            required=True)

    # 참석자를 고를 수 있습니다.
    #
    # 화면(`NewMeetingDialog`)에 참석자 피커가 있는데 서버가 이 키를 안 읽어,
    # 골라도 프로젝트 전원이 들어갔습니다. 옆에 나란히 있는 일정 만들기
    # (`POST /projects/{id}/calendar/events`)는 이미 받고 있어, 똑같이 생긴
    # 다이얼로그 둘이 다르게 동작했습니다.
    participant_ids = request.data.get("participant_ids") or []
    if participant_ids:
        valid = set(map(str, ProjectMember.objects
                        .filter(project=project, user_id__in=participant_ids)
                        .values_list("user_id", flat=True)))
        outsiders = [str(u) for u in participant_ids if str(u) not in valid]
        if outsiders:
            raise BordoError("PROJECT_ACCESS_DENIED",
                             "프로젝트 참여자만 회의에 넣을 수 있습니다.",
                             details={"not_in_project": outsiders})
    else:
        # 안 보내면 지금까지처럼 전원입니다. 빈 배열을 "아무도 아님" 으로 읽으면
        # 이 키를 모르는 클라이언트가 참석자 없는 회의를 만듭니다.
        valid = set(map(str, ProjectMember.objects.filter(project=project)
                        .values_list("user_id", flat=True)))

    with transaction.atomic():
        meeting = Meeting.objects.create(
            project=project, project_name=project.name, title=title,
            scheduled_at=scheduled_at,
            duration_min=int(request.data.get("duration_min", 60)),
            discord_channel_id=request.data.get("discord_channel_id", "") or "",
            created_by=request.user,
        )
        members = (ProjectMember.objects.filter(project=project, user_id__in=valid)
                   .select_related("user"))
        MeetingParticipant.objects.bulk_create([
            MeetingParticipant(meeting=meeting, user=m.user, user_name=m.user.name)
            for m in members])
        MeetingSummary.objects.create(meeting=meeting)
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

    ## 보낸 것만 바꿉니다

    한 요청에 뜻이 셋 실려 있습니다 — 대리 참석 켜고 끄기 · 사전 지시 · 자료 범위.
    전에는 셋 중 하나만 바꾸려는 호출이 **나머지를 지웠습니다.**

        p.delegate_prompt = request.data.get("prompt", "") or ""

    키가 없어도 빈 문자열로 덮었습니다. 「일정 관련 결정은 제 확인을 받아 주세요」
    를 적어 둔 사람이 나중에 자료 범위만 고치면 그 지시가 사라졌고, 화면에는
    저장됐다고 떴습니다. 대리인은 그 지시를 못 받은 채 회의에 들어갑니다.

    `sources` 는 원래 키 유무를 봤는데 `prompt` 만 안 봤습니다. 규칙을 맞춥니다.

    ## `enabled` 는 그대로 둡니다

    키가 없으면 여전히 켜기입니다. 이건 부분 갱신 규칙에서 벗어나지만, 이
    엔드포인트를 부르는 것 자체가 「맡긴다」 는 뜻으로 쓰여 왔고 `POST .../delegate`
    에 빈 본문을 보내는 호출이 실제로 있습니다(`scripts/smoke_test.py`).
    여기서 뜻을 바꾸면 **맡겼다고 생각한 회의가 조용히 안 맡겨집니다** —
    지금 고치려는 것보다 나쁜 종류의 실패입니다.

    새로 붙는 곳은 `POST /meetings/{id}/absence` 를 쓰십시오. `delegated` 플래그의
    단일 진입점은 그쪽이고, 이 함수는 홈 팝업이 붙어 있던 옛 경로입니다.
    """
    meeting = meeting_access(request.user, meeting_id)
    p = MeetingParticipant.objects.filter(meeting=meeting, user=request.user).first()
    if not p:
        raise BordoError("STATE_NOT_FOUND", "이 회의의 참석자가 아닙니다.")

    enabled = bool(request.data.get("enabled", True))
    p.delegated = enabled
    p.attendance = Attendance.DELEGATED if enabled else Attendance.PENDING
    changed = ["delegated", "attendance", "updated_at"]

    # 보낸 것만 담습니다. 안 바뀐 칸을 `update_fields` 에 넣으면 같은 행을
    # 동시에 고치는 다른 요청의 결과를 옛 값으로 되돌려 씁니다.
    if "prompt" in request.data:
        p.delegate_prompt = request.data.get("prompt") or ""
        changed.append("delegate_prompt")

    if "sources" in request.data:
        p.allowed_sources = _clean_sources(request.data["sources"])
        changed.append("allowed_sources")

    p.save(update_fields=changed)
    return Response({"meeting_id": str(meeting.id), "user_id": str(request.user.id),
                     "delegated": p.delegated, "attendance": p.attendance,
                     "prompt": p.delegate_prompt,
                     "sources": p.allowed_sources})


def _clean_sources(raw):
    """
    이 회의에서 대리인이 근거로 쓸 자료 종류.

    ## 빈 목록과 안 보낸 것을 가릅니다

        키가 없음   지금 값을 그대로 둔다
        null        고른 적 없음으로 되돌린다 (제한 없음)
        []          **아무 자료도 쓰지 않는다**
        ["work"]    작업 현황만

    빈 목록을 "제한 없음" 으로 읽으면 전부 끈 사람의 대리인이 **모든 자료를
    보게 됩니다.** 정반대로 동작하는 셈이라 빈 목록을 그대로 존중합니다.

    모르는 값은 400 입니다. 조용히 버리면 사용자는 골랐다고 생각하는데
    대리인은 그 자료를 안 봅니다.
    """
    if raw is None:
        return None

    if not isinstance(raw, list):
        raise BordoError("VALIDATION_ERROR", "sources 는 배열이어야 합니다.")

    allowed = MeetingParticipant.SOURCE_CHOICES
    picked, bad = [], []
    for item in raw:
        value = str(item or "").strip().lower()
        if value in allowed:
            if value not in picked:
                picked.append(value)
        else:
            bad.append(item)

    if bad:
        raise BordoError("VALIDATION_ERROR", "고를 수 없는 자료 종류입니다.",
                         details={"sources": bad, "allowed": list(allowed)})
    return picked


# ─────────────────────────────────────────── 플로우
def _flag(raw, default):
    """쿼리의 불리언. 안 오면 기본값이고, `false` · `0` · `no` 만 거짓입니다."""
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in ("false", "0", "no")


def _work_period(params):
    """
    작업 플로우의 조회 구간. 기본은 최근 7일입니다.

    `project_flow`(캔버스) · `indexes`(좌측 인덱스) · `flow_participant`(우측
    패널)가 **같은 값을 써야 합니다.** 따로 두면 한쪽만 고쳤을 때 인덱스에 있는
    문서를 눌러도 강조될 화살표가 판에 없고, 패널의 숫자와 캔버스의 숫자가
    갈립니다 — 같은 화면에서 다른 값을 보여 주면 어느 쪽이 맞는지 알 수 없습니다.
    """
    to_at = parse_dt(params.get("to"), "to") or timezone.now()
    from_at = parse_dt(params.get("from"), "from") or to_at - timedelta(days=7)
    if from_at > to_at:
        raise BordoError("VALIDATION_ERROR", "from 이 to 보다 뒤입니다.")
    return from_at, to_at


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

    # 기본 구간은 좌측 인덱스 · 우측 패널과 한 곳에서 정합니다.
    from_at, to_at = _work_period(request.query_params)

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
    """
    좌측 인덱스. 작업 모드는 문서, 회의 모드는 안건.

    ## 작업 모드는 회의가 아니라 프로젝트·기간으로 봅니다

    경로에 회의 id 가 붙어 있지만 **작업 엣지에는 회의가 없습니다**
    (`project_flow` 참고 — 작업 플로우의 스코프는 기간입니다). 회의로 좁히면
    조건에 맞는 행이 하나도 없어 이 목록은 언제나 빈 배열이었습니다.
    회의 id 는 어느 프로젝트인지를 알아내는 데만 씁니다.

    ## 판과 **같은 필터**를 지납니다

    전에는 인덱스가 필터를 안 태워서, 내용 필터를 걸면 목록에는 남아 있는데
    그 항목이 가리키는 화살표가 판에 없었습니다. 누르면 강조될 것이 없어
    화면은 "눌렀는데 아무 일이 없다" 가 됩니다.

    ## 걷어내는 것은 **필터가 걸렸을 때만**

    필터가 걸렸는데 관련 화살표가 하나도 없는 항목은 뺍니다. 남겨 두고 빈
    배열을 주면 누를 수는 있는데 아무 일도 안 일어나는 줄이 남습니다.

    필터가 없는 기본 상태에서는 안건을 그대로 다 보여줍니다. 안건과 화살표를
    잇는 자동 매칭이 아직 없어서, 여기서도 걷어내면 회의 모드 인덱스가 늘
    빈 목록이 됩니다 — 이슈 #77 로 한 번 고친 상태로 되돌아갑니다. 안건은
    화살표가 안 붙어 있어도 그 자체로 회의의 목차입니다.
    """
    meeting = meeting_access(request.user, meeting_id)
    params = request.query_params
    filtered = any(params.get(k) for k in
                   ("content_types", "surfaces", "sources", "participant_ids",
                    "since_minutes"))

    if _resolve_category(params.get("category")) == FlowCategory.MEETING:
        _, edges = _filtered_edges({"meeting": meeting}, params)
        edge_map = {}
        for e in edges:
            if e.agenda_id:
                edge_map.setdefault(e.agenda_id, []).append(e.id)
        rows = Agenda.objects.filter(meeting=meeting)
        return Response(listing([{
            "id": str(a.id), "label": a.title, "kind": "AGENDA",
            "related_edge_ids": [str(i) for i in edge_map.get(a.id, [])],
        } for a in rows if not filtered or a.id in edge_map]))

    from_at, to_at = _work_period(params)
    cat, edges = _filtered_edges({"project_id": meeting.project_id}, params,
                                 period=(from_at, to_at))
    edge_map = {}
    for e in edges:
        if e.work_document_id:
            edge_map.setdefault(e.work_document_id, []).append(e.id)

    from apps.documents.models import Document, Visibility

    # 비공개 문서는 목록에서 뺍니다. 작업 플로우는 팀 관점 화면이라 제목만
    # 스쳐도 비공개로 둔 뜻이 사라집니다 — 남의 것이면 존재도 알리지 않습니다.
    docs = Document.objects.filter(id__in=list(edge_map.keys())).filter(
        Q(visibility=Visibility.TEAM) | Q(owner=request.user))
    return Response(listing([{
        "id": str(d.id), "label": d.title, "kind": "DOCUMENT",
        "related_edge_ids": [str(i) for i in edge_map[d.id]],
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
    # 종류를 먼저 봅니다. 나머지 화살표에는 조회 기록이 붙을 수 없는데,
    # 그것까지 매번 물으면 화살표를 누를 때마다 헛조회가 한 번씩 나갑니다.
    if edge.content_type == FlowContentType.AI_LOOKUP:
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


@api_view(["GET"])
def flow_participant(request, project_id, user_id):
    """
    플로우 화면 우측 패널 — **사람 한 명이 이 기간에 무엇을 주고받았는가.**

    화면에서 노드(사람)를 누르면 열립니다.

        {이름}의 작업          이름 · 역할 · 출처 태그
        작업 한눈에 보기        한 문단 요약
        칩                     작업 3 · 피드백 5 · 수정 6 · 공유 1
        전달한 내용             종류별로 묶인 목록 (→ 받은 사람 · 시각)

    ## 왜 새 엔드포인트인가

    `GET /flow-edges/{id}` 는 화살표 **하나**짜리입니다. 이 패널은 사람 하나에
    걸린 화살표 전부를 종류별로 묶어야 해서, 프론트가 만들려면 화살표 수만큼
    요청을 보내야 합니다. 다섯 명이 각각 열 건이면 쉰 번입니다.

    ## 기간을 받습니다

    작업 플로우는 기간이 스코프입니다(`project_flow` 와 같은 규칙). 기간을 안
    받으면 패널의 숫자와 캔버스의 숫자가 갈립니다 — 같은 화면에서 다른 값을
    보여 주면 어느 쪽이 맞는지 알 수 없습니다.

    ## 요약을 지어내지 않습니다

    `summary` 는 **집계한 사실만** 문장으로 만듭니다. LLM 을 부르지 않습니다 —
    부르면 근거 없는 문장이 섞이고, 그건 이 서비스가 유보로 막아 둔 자리입니다.
    """
    project, _ = project_membership(request.user, project_id)

    # 기본 구간은 좌측 인덱스 · 우측 패널과 한 곳에서 정합니다.
    from_at, to_at = _work_period(request.query_params)

    person = (ProjectMember.objects
              .filter(project=project, user_id=user_id)
              .select_related("user").first())
    if person is None:
        # 이 프로젝트 사람이 아니면 **404** 입니다. 403 을 주면 "그런 사람이 있긴
        # 하다" 가 새어 나갑니다.
        raise BordoError("STATE_NOT_FOUND", "이 프로젝트 참여자가 아닙니다.")
    user = person.user

    # 사람의 화살표는 `from_node` 안의 `user_id` 로 찾습니다. 대리인 노드도 같은
    # `user_id` 를 들고 있어(`{uuid}:agent`), 본인 것과 대리인 것이 함께 잡힙니다 —
    # 화면에서도 둘은 한 사람으로 묶여 보입니다.
    rows = [e for e in FlowEdge.objects
            .filter(project=project, category=FlowCategory.WORK,
                    occurred_at__gte=from_at, occurred_at__lte=to_at)
            .order_by("-occurred_at")
            if (e.from_node or {}).get("user_id") == str(user_id)]

    tz = user_tz(request.user)
    counts, groups = {}, {}
    for e in rows:
        counts[e.content_type] = counts.get(e.content_type, 0) + 1
        groups.setdefault(e.content_type, []).append({
            "edge_id": str(e.id),
            "title": e.label,
            "direction_label": e.direction_label,
            # 화면에 그대로 찍히는 문자열은 서버가 완성합니다. 클라이언트가
            # 포맷하면 브라우저 시간대로 나가 같은 항목을 사람마다 다르게 봅니다.
            "occurred_label": timezone.localtime(e.occurred_at, tz).strftime("%y.%m.%d %H:%M"),
            "occurred_at": e.occurred_at,
            "source": e.source or None,
            "source_url": e.source_url or None,
        })

    label = {c.value: c.label for c in FlowContentType}
    count_chips = [{"content_type": k, "label": label.get(k, k), "count": v}
                   for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]

    # `발언` 은 화살표가 아니라 화살표 **안에서** 입을 연 횟수라 거를 묶음이
    # 없다. `content_type` 을 안 넣는 이유가 그것이다 — 넣으면 화면이 필터
    # 버튼으로 그리는데, 누르면 걸리는 게 하나도 없어 패널이 통째로 빈다
    # (이슈 #139). 대리인이 대신 한 발언도 본인 몫으로 센다 — 이 패널이
    # 이미 본인 노드·대리인 노드를 같은 사람으로 묶어 보여 주고 있어서,
    # 화살표 집계와 같은 기준을 따른다.
    utterance_count = Utterance.objects.filter(
        meeting__project=project, participant_id=user_id,
        spoken_at__gte=from_at, spoken_at__lte=to_at).count()
    if utterance_count:
        count_chips.insert(0, {"key": "utterance", "label": "발언", "count": utterance_count})

    return Response({
        "project_id": str(project.id),
        "user": {
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url or None,
            # 화면의 이름 옆 태그. 역할이 비어 있으면 칸을 아예 안 그리도록
            # None 을 줍니다 — 빈 문자열을 주면 빈 태그가 그려집니다.
            "role": user.project_role or None,
            "agent_name": agent_display_name(user),
        },
        "period_label": f"{day_label(from_at, tz)} - {day_label(to_at, tz)}",
        "counts": count_chips,
        "summary": _participant_summary(user.name, counts, label),
        "groups": [{"content_type": k, "label": label.get(k, k), "items": v}
                   for k, v in groups.items()],
    })


def _participant_summary(name: str, counts: dict, label: dict) -> str:
    """
    `작업 한눈에 보기` 한 문단.

    **집계한 사실만 씁니다.** LLM 을 부르면 "잘 진행되고 있습니다" 같은 근거 없는
    문장이 섞이는데, 그건 이 서비스가 유보로 막아 둔 바로 그 자리입니다.

    아무것도 없을 때 빈 문자열을 주지 않습니다. 화면이 빈 칸을 그리면 사용자는
    못 불러온 것으로 읽습니다.
    """
    if not counts:
        return f"{name} 님은 이 기간에 남긴 작업 기록이 없습니다."

    parts = [f"{label.get(k, k)} {v}건"
             for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return f"{name} 님은 이 기간에 " + ", ".join(parts) + "을 남겼습니다."


@api_view(["GET"])
def meeting_participant_flow(request, meeting_id, node_id):
    """
    회의 스코프 플로우 우측 패널 — **노드 하나(사람 또는 그 사람의 대리인)가
    이 회의에서 무엇을 주고받았는가.** 판에서 사람 노드를 누르면 열립니다.

    `flow_participant()`(작업 모드)와 다른 점 셋 (이슈 #136):

    - 스코프가 **회의 하나**입니다(기간이 아닙니다).
    - `node_id`가 UUID가 아닐 수 있습니다. 대리인 노드는
      `{user_id}:agent` 형태라(`apps/agent/services/flow.py`의
      `agent_node()`) URL에서 uuid 컨버터로 못 받습니다 — 문자열로 받아
      여기서 콜론으로 가릅니다.
    - `groups`(보낸 것만) 대신 `sent`·`received`를 **따로** 줍니다. 이
      패널의 알약 줄이 "전달한 내용"과 "전달받은 내용"을 함께 거르는
      필터라, 받은 쪽이 아예 없으면 그 종류의 알약 자체가 안 생깁니다.
    """
    meeting = meeting_access(request.user, meeting_id)

    is_agent = node_id.endswith(":agent")
    raw_user_id = node_id[:-len(":agent")] if is_agent else node_id
    try:
        user_id = uuid.UUID(raw_user_id)
    except (ValueError, AttributeError, TypeError):
        # node_id 자체가 UUID 모양이 아니면 참석자일 수가 없습니다. 여기서
        # 걸러야 아래 쿼리가 잘못된 값으로 500 을 내지 않습니다.
        raise BordoError("STATE_NOT_FOUND", "이 회의 참석자가 아닙니다.")

    participant = (MeetingParticipant.objects
                  .filter(meeting=meeting, user_id=user_id)
                  .select_related("user").first())
    if participant is None:
        # 이 회의 참석자가 아니면 404 입니다. 403 을 주면 "그런 사람이
        # 있긴 하다" 가 새어 나갑니다.
        raise BordoError("STATE_NOT_FOUND", "이 회의 참석자가 아닙니다.")
    user = participant.user

    name = agent_display_name(user) if is_agent else user.name
    kind = "AGENT" if is_agent else "USER"

    edges = (FlowEdge.objects
            .filter(meeting=meeting, category=FlowCategory.MEETING)
            .order_by("-occurred_at"))

    tz = user_tz(request.user)
    label = {c.value: c.label for c in FlowContentType}

    def item(e):
        return {
            "edge_id": str(e.id),
            "title": e.label,
            "direction_label": e.direction_label,
            # 화면에 그대로 찍히는 문자열은 서버가 완성합니다. 클라이언트가
            # 포맷하면 브라우저 시간대로 나가 같은 항목을 사람마다 다르게 봅니다.
            "occurred_label": timezone.localtime(e.occurred_at, tz).strftime("%y.%m.%d %H:%M"),
            "occurred_at": e.occurred_at,
            "source": e.source or None,
            "source_url": e.source_url or None,
        }

    counts, sent_groups, received_groups = {}, {}, {}
    for e in edges:
        frm_id = (e.from_node or {}).get("id")
        to_ids = {n.get("id") for n in (e.to_nodes or []) if n}

        sent = frm_id == node_id
        received = node_id in to_ids
        if not sent and not received:
            continue

        counts[e.content_type] = counts.get(e.content_type, 0) + 1
        if sent:
            sent_groups.setdefault(e.content_type, []).append(item(e))
        if received:
            received_groups.setdefault(e.content_type, []).append(item(e))

    def as_groups(groups):
        return [{"content_type": k, "label": label.get(k, k), "items": v}
                for k, v in groups.items()]

    count_chips = [{"content_type": k, "label": label.get(k, k), "count": v}
                   for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]

    # `발언` 은 화살표가 아니라 화살표 **안에서** 입을 연 횟수라 거를 묶음이
    # 없다. `content_type` 을 안 넣는 이유가 그것이다 — 넣으면 화면이 필터
    # 버튼으로 그리는데 누르면 걸리는 게 하나도 없어 패널이 통째로 빈다(#139).
    #
    # 작업 모드(`flow_participant`)와 다른 점 — 거기는 한 사람에 노드가 하나라
    # 대리인 발언까지 본인 몫으로 합친다. 회의 모드는 본인 노드와 대리인
    # 노드가 **따로 그려지므로** 발언도 갈라야 한다. 안 가르면 두 노드가 같은
    # 숫자를 들고 있어, 대리인이 대신 말한 건수를 본인도 말한 것처럼 읽는다.
    utterance_count = Utterance.objects.filter(
        meeting=meeting, participant_id=user_id, is_agent=is_agent).count()
    if utterance_count:
        count_chips.insert(0, {"key": "utterance", "label": "발언",
                               "count": utterance_count})

    return Response({
        "node_id": node_id,
        "name": name,
        "kind": kind,
        "headline": _meeting_participant_headline(name, sent_groups, received_groups),
        "counts": count_chips,
        "sent": as_groups(sent_groups),
        "received": as_groups(received_groups),
    })


def _meeting_participant_headline(name: str, sent_groups: dict, received_groups: dict) -> str:
    """`_participant_summary()`의 회의 스코프 버전. 보낸 것·받은 것을 모두
    사실만 집계해 한 문장으로 만듭니다 — LLM 을 부르지 않습니다."""
    sent_total = sum(len(v) for v in sent_groups.values())
    received_total = sum(len(v) for v in received_groups.values())
    if not sent_total and not received_total:
        return f"{name} 님은 이 회의에서 남긴 기록이 없습니다."

    parts = []
    if sent_total:
        parts.append(f"보낸 것 {sent_total}건")
    if received_total:
        parts.append(f"받은 것 {received_total}건")
    return f"{name} 님은 이 회의에서 " + " · ".join(parts) + "을 남겼습니다."


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

    # `mark_read=false` 로 읽음 처리를 끕니다.
    #
    # 플로우 화면은 브리핑 패널을 열든 말든 회의를 열 때 이것을 부릅니다. 그래서
    # 회의 화면에 잠깐 들른 것만으로 홈의 `Bordo 브리핑 보러가기` 가 사라졌습니다 —
    # 사용자는 읽은 적이 없는데 읽은 것이 됩니다. 서버는 패널을 열었는지 알 수
    # 없으므로 **부르는 쪽이 알려 줘야 합니다.**
    #
    # 기본은 여전히 읽음입니다. 기본을 끄면 이 값을 안 보내는 쪽에서는 브리핑이
    # 영영 안 읽힌 상태로 남아 홈 팝업이 매번 뜹니다.
    if briefing.read_at is None and _flag(request.query_params.get("mark_read"), True):
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
