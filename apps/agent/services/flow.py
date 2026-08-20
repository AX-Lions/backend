"""
플로우 엣지 기록.

`FlowEdge` 는 **핵심 화면 두 개 중 하나의 유일한 데이터원**입니다. 지금까지 조회 API 만
있고 만드는 코드가 없어, 시드가 심은 회의 말고는 화면이 비어 있었습니다.

## 무엇을 남기고 무엇을 남기지 않는가

2차 회의에서 정한 것 — **"모든 AI 통신" 이 아니라 "의미 있는 정보 변화"** 입니다.

    남긴다    사전 지시 · 질문이 대리인에게 향함 · 답변 · 유보 · 후보 산출물 · 브리핑
    안 남긴다  AI 내부 추론 · 검색 호출 하나하나 · 시스템 로그

검색을 부를 때마다 화살표를 그리면 회의 하나에 수십 개가 쌓이고, **정작 무슨 일이
있었는지가 안 보입니다.** Flow 는 백엔드 로그 시각화가 아니라 협업 맥락 시각화입니다.

## 노드는 세 종류입니다

    USER    사람
    AGENT   대리인. **서비스와 Discord 를 하나로 봅니다** — 출처는 surface 로 남깁니다
    SERVER  서버에 저장된 것(계획·태스크). "어디로 갔는가" 를 보여주기 위한 자리입니다

노드 모양은 `seed_demo.py` 와 **글자 하나까지 같아야 합니다.** 프론트가 `id` 로
노드를 합치기 때문에, 표기가 갈리면 같은 사람의 대리인이 화면에 두 개로 그려집니다.

## 기록이 실패해도 본 작업은 계속합니다

화살표 하나를 못 그렸다고 대리인의 답변이 취소되면 안 됩니다. 화면이 덜 그려질 뿐입니다.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.meetings.models import FlowCategory, FlowContentType, FlowEdge, Surface

logger = logging.getLogger("bordo.agent")

#: 라벨 길이. 모델이 60자입니다.
_LABEL_MAX = 60


def user_node(user) -> dict:
    return {"id": str(user.id), "kind": "USER",
            "user_id": str(user.id), "name": user.name,
            "avatar_url": user.avatar_url or None}


def agent_node(owner) -> dict:
    """
    대리인 노드.

    서비스 대리인과 Discord 대리인을 **같은 노드**로 둡니다. 2차 회의에서
    "Flow 상에서 하나의 AI 대리인으로 통합 표현" 으로 정했습니다. 나눠 그리면
    사용자는 자기 대리인이 둘인 줄 압니다.

    `id` 표기와 호칭은 `seed_demo.py` 를 그대로 따릅니다.
    호칭이 `AI` 가 아니라 **`{이름}의 Bordo`** 인 이유는 `CLAUDE.md` 에 적혀
    있습니다 — `AI 대리인` 은 화면 어디에도 없는 낱말입니다.
    """
    return {"id": f"{owner.id}:agent", "kind": "AGENT",
            "user_id": str(owner.id), "name": agent_display_name(owner),
            "avatar_url": owner.avatar_url or None}


def agent_display_name(owner) -> str:
    """
    대리인 호칭. 본인이 정한 이름이 있으면 그것을 씁니다.

    **여기 한 군데서만 만듭니다.** 노드·AI 조회 상세·채팅방 제목이 각자 조립하면
    이름을 바꿨을 때 화면마다 다른 이름이 뜹니다.

    설정 행이 없어도 돌아야 합니다 — 설정 화면에 한 번도 안 들어간 사용자의
    대리인이 이름 때문에 안 그려지면 안 됩니다.
    """
    from ..models import AgentSettings

    row = AgentSettings.objects.filter(user_id=owner.id).only("agent_name").first()
    name = (row.agent_name.strip() if row else "")
    return name or f"{owner.name}의 Bordo"


def agent_display_names(user_ids) -> dict:
    """
    여러 사람 몫을 한 번에. 규칙은 `agent_display_name()` 과 같습니다.

    채팅 사이드바는 대리인 방을 여러 개 그립니다. 방마다 위 함수를 부르면
    목록 하나에 조회가 방 수만큼 붙습니다.
    """
    ids = list(user_ids)
    if not ids:
        return {}

    from apps.accounts.models import User

    from ..models import AgentSettings

    # 탈퇴한 사람과의 방도 목록에는 남습니다. `all_objects` 로 봐야 이름 없는
    # 방이 되지 않습니다.
    names = dict(User.all_objects.filter(id__in=ids).values_list("id", "name"))
    custom = dict(AgentSettings.objects.filter(user_id__in=ids)
                  .values_list("user_id", "agent_name"))
    return {uid: ((custom.get(uid) or "").strip()
                  or f"{names.get(uid, '')}의 Bordo")
            for uid in ids}


def server_node() -> dict:
    return {"id": "server", "kind": "SERVER", "user_id": None, "name": "서버",
            "avatar_url": None}


def record(meeting, *, from_node: dict, to_nodes: list[dict], label: str,
           content_type: str, surface: str = Surface.SERVICE,
           category: str = FlowCategory.MEETING, agenda=None,
           occurred_at=None, says: list[tuple[str, str]] | None = None) -> FlowEdge | None:
    """
    화살표 하나를 남깁니다.

    회의가 없으면 만들지 않습니다 — Flow 는 회의 화면이고, 회의 밖 대화는
    그릴 자리가 없습니다.
    """
    if meeting is None:
        return None

    to_nodes = [n for n in (to_nodes or []) if n]
    if not to_nodes:
        # 받는 쪽이 없는 화살표는 그릴 수 없습니다.
        return None

    try:
        names = ", ".join(n["name"] for n in to_nodes[:3])
        if len(to_nodes) > 3:
            names += f" 외 {len(to_nodes) - 3}명"

        edge = FlowEdge(
            meeting=meeting,
            # 작업 플로우와 공통 스코프입니다. 회의 엣지도 채워 둬야
            # 프로젝트 단위 조회에서 빠지지 않습니다.
            project_id=meeting.project_id,
            category=category,
            content_type=content_type,
            surface=surface,
            from_node=from_node,
            to_nodes=to_nodes,
            # 필터가 JSON 배열 안을 뒤지지 않도록 따로 뽑아 둡니다.
            participant_ids=[n["user_id"] for n in [from_node] + to_nodes
                             if n.get("user_id")],
            label=label[:_LABEL_MAX],
            direction_label=f"{from_node['name']} → {names}"[:200],
            # 4명째부터는 접어서 보여줍니다. 화살표에 이름이 다 들어가면 읽히지 않습니다.
            extra_participant_count=max(0, len(to_nodes) - 3),
            agenda=agenda,
            # 카드 본문에 찍히는 실제 대사입니다. 비워 두면 우측 패널이
            # 제목과 창구(`Discord`)만 남은 빈 카드가 됩니다.
            delivery_context=[{"participant_name": who, "utterance": what}
                              for who, what in (says or []) if what],
            occurred_at=occurred_at or timezone.now(),
        )
        # 진행 중인 회의에서는 아직 의미가 없습니다. `compute_opacity()` 의
        # `newest` 가 `now` 라 방금 만든 엣지는 언제나 비율 1.0 이 나옵니다.
        # 실제 값은 회의가 끝날 때 `recompute_for_meeting()` 이 다시 채웁니다.
        edge.opacity = edge.compute_opacity()

        # 저장을 세이브포인트로 감쌉니다.
        #
        # 이 함수는 트랜잭션 안에서도 불립니다(briefing.build_for_user 는
        # @transaction.atomic). 거기서 DB 오류가 나면 **예외를 잡아도 트랜잭션은
        # 이미 오염됩니다** — 이후 쿼리가 전부 TransactionManagementError 로
        # 터집니다. 잡아서 넘어간다는 이 함수의 약속이 그대로 깨집니다.
        with transaction.atomic():
            edge.save()
        return edge
    except Exception:                                          # noqa: BLE001
        # 화살표 하나를 못 그렸다고 대리인의 답변이 취소되면 안 됩니다.
        logger.exception("플로우 엣지 기록 실패 meeting=%s", getattr(meeting, "id", None))
        return None


def recompute_for_meeting(meeting) -> int:
    """
    회의가 끝났을 때 진하기를 다시 계산합니다.

    **이게 없으면 그라데이션이 사실상 동작하지 않습니다.**

    엣지를 만드는 시점에는 회의가 진행 중이라 `ended_at` 이 없고,
    `compute_opacity()` 는 `newest` 를 `now` 로 잡습니다. 방금 찍은
    `occurred_at` 과 같은 순간이라 비율이 언제나 1.0 입니다. 회의 중 화살표가
    거의 전부이므로, 다시 계산하지 않으면 화면이 통째로 균일합니다.

    회의가 끝나면 구간이 고정되므로 이때 한 번만 계산하면 됩니다 — 이후로는
    언제 열어도 같은 그림입니다. 조회 시각으로 계산하면 내일 열었을 때
    그림이 달라집니다.
    """
    edges = list(FlowEdge.objects.filter(meeting=meeting))
    if not edges:
        return 0

    # 구간을 한 번만 구해 넘깁니다. 엣지마다 회의를 다시 읽으면 N+1 입니다.
    newest = meeting.ended_at or timezone.now()
    oldest = meeting.started_at or meeting.scheduled_at

    for e in edges:
        e.opacity = e.compute_opacity(oldest=oldest, newest=newest)
    FlowEdge.objects.bulk_update(edges, ["opacity"])
    return len(edges)


def agenda_for(meeting, text: str):
    """
    이 발언이 어느 안건에 속하는지 **짐작**합니다.

    화면 좌측 `인덱스` 에서 안건을 누르면 관련 화살표로 점프합니다
    (`GET /meetings/{id}/indexes` 의 `related_edge_ids`). 그 연결이 지금 비어
    있어서 인덱스가 아무 데도 못 갑니다.

    ## 짐작이라고 적어 두는 이유

    발언과 안건을 잇는 **명시적인 신호가 없습니다.** Discord 에서 "지금부터
    2번 안건" 이라고 선언하지 않습니다. 그래서 제목과 발언의 낱말 겹침으로
    고릅니다 — 검색 스킬이 쓰는 것과 같은 방식입니다.

    ## 애매하면 비웁니다

    두 안건이 같은 점수면 **아무것도 고르지 않습니다.** 틀린 안건에 붙으면
    사용자가 인덱스를 눌렀을 때 엉뚱한 곳으로 갑니다 — 비어 있는 것보다 나쁩니다.

    ## 알려진 한계

    낱말 두 개가 겹쳐야 하므로 **제목이 한 낱말인 안건은 영영 안 걸립니다**
    (`로그인` 같은). 지금 시안의 안건은 `진행 상황 공유` · `백엔드 개발 논의`
    처럼 두 낱말 이상이라 문제가 안 되지만, 짧은 제목이 흔해지면 다시 봐야
    합니다. 문턱을 낮추는 것보다 이대로 두는 편이 나은 이유는 위와 같습니다.

    ## 발언마다 한 번만 부르십시오

    안건 목록을 읽고 제목을 전부 토큰으로 쪼갭니다. 질문 화살표와 답변 화살표에
    각각 부르면 **같은 조회와 같은 계산이 두 번** 돕니다. 호출부에서 한 번 구해
    양쪽에 넘기십시오.
    """
    from apps.agent.services.skills.search_records import _tokens
    from apps.meetings.models import Agenda

    words = set(_tokens(text or ""))
    if not words:
        return None

    best, best_score, tied = None, 0, False
    for agenda in Agenda.objects.filter(meeting=meeting):
        score = len(words & set(_tokens(agenda.title)))
        if score > best_score:
            best, best_score, tied = agenda, score, False
        elif score == best_score and score > 0:
            tied = True

    # 겹치는 낱말이 하나뿐이면 우연일 가능성이 큽니다.
    if best_score < 2 or tied:
        return None
    return best


# ═══════════════════════════════════════════ 상황별 기록

def delegate_prompt_given(meeting, user, prompt: str):
    """
    회의 전 — 본인이 대리인에게 남긴 지시.

    발언마다 불리므로 **한 회의에 한 번만** 남깁니다. 매번 그리면 질문 열 개짜리
    회의에서 같은 화살표가 열 번 겹칩니다.

    "있는지 보고 없으면 넣는다" 는 그대로 두면 뚫립니다. 회의 중 발언 두 개가
    다른 워커에서 동시에 처리되면 둘 다 "없다" 를 보고 둘 다 넣습니다.
    그 사람의 참가자 행을 잠가 순서를 만듭니다 — 락 범위가 (회의, 사람) 하나라
    다른 회의나 다른 참석자의 처리를 막지 않습니다.

    FlowEdge 에 유니크 제약을 거는 방법도 있지만, 그러려면 `apps.meetings` 에
    마이그레이션을 넣어야 합니다. 화살표 중복이라는 화면 문제를 잡자고 공용 앱의
    스키마를 건드릴 일은 아닙니다.
    """
    from apps.meetings.models import MeetingParticipant

    if not (prompt or "").strip():
        return None

    with transaction.atomic():
        locked = (MeetingParticipant.objects
                  .select_for_update()
                  .filter(meeting=meeting, user=user)
                  .first())
        if locked is None:
            return None
        already = FlowEdge.objects.filter(
            meeting=meeting, label="사전 지시",
            from_node__id=str(user.id)).exists()
        if already:
            return None
        return record(meeting,
                      from_node=user_node(user), to_nodes=[agent_node(user)],
                      label="사전 지시", content_type=FlowContentType.REQUEST,
                      occurred_at=meeting.scheduled_at)


#: 발언을 여섯 칸 중 어디로 넣을지 가르는 낱말.
#:
#: 앞에 오는 종류가 이깁니다 — `시안 마감은 8월 18일로 확정하겠습니다` 는
#: 일정이자 결론인데, 회의에서 사람이 기억하는 것은 "정해졌다" 쪽입니다.
_SPEECH_RULES = (
    (FlowContentType.CONCLUSION, "결론",
     ("확정", "결정", "하기로", "정리하면", "합의", "그렇게 가", "여기까지")),
    (FlowContentType.SCHEDULE, "일정",
     ("일정", "마감", "연장", "미루", "당기", "언제까지", "데드라인", "주까지", "일까지")),
    (FlowContentType.CHANGE, "변동사항",
     ("바뀝", "바꾸", "바꿔", "변경", "수정", "걷어내", "빼겠", "대신")),
    (FlowContentType.REQUEST, "요청사항",
     ("?", "주세요", "부탁", "요청", "해주", "해 주", "가능할까", "될까요", "여쭤")),
)

#: 이보다 짧으면 화살표를 그리지 않습니다.
#:
#: `넵` · `ㅇㅋ` 까지 그리면 회의 하나에 화살표가 수십 개가 되고, 정작 무엇이
#: 오갔는지가 그 안에 묻힙니다. 이 화면은 회의록이 아니라 맥락 지도입니다.
_MIN_SPEECH = 12


def classify_speech(body: str) -> tuple[str, str] | None:
    """발언 하나를 화면 필터 여섯 칸 중 하나로 넣습니다. 너무 짧으면 `None`."""
    text = (body or "").strip()
    if len(text) < _MIN_SPEECH:
        return None
    for content_type, label, keywords in _SPEECH_RULES:
        if any(k in text for k in keywords):
            return content_type, label
    return FlowContentType.OPINION, "의견"


def utterance_recorded(meeting, utterance, *, agenda=None) -> FlowEdge | None:
    """
    **사람이 회의에서 한 말**을 화살표로 남깁니다.

    ## 이게 없으면 판에 대리인 화살표만 뜹니다

    전에는 대리인이 낀 사건(질문 라우팅 · 답변 · 후보 산출물 · 브리핑)만
    그렸습니다. 그러면 회의를 새로 열어 판을 봐도 사람 노드 사이에 선이 하나도
    없어, **대리인 발언이 회의 맥락 없이 공중에 뜹니다.** 화면의 필터 여섯 칸도
    두세 칸만 채워지고, 좌측 시간순 인덱스는 서너 줄로 끝납니다.

    받는 쪽은 **그 자리에 있던 나머지 참석자**입니다. 회의 발언은 특정인에게
    거는 말이라도 모두가 듣습니다 — 한 사람만 그리면 나머지가 그 결정을 모르는
    것처럼 보입니다. 자리를 비운 사람은 본인이 아니라 **대리인 노드**로 받습니다.
    실제로 그 자리에서 듣고 있던 것은 대리인이기 때문입니다.
    """
    from apps.meetings.models import Attendance, MeetingParticipant

    if utterance is None or utterance.is_agent or utterance.participant is None:
        return None

    kind = classify_speech(utterance.body)
    if kind is None:
        return None
    content_type, label = kind

    rows = (MeetingParticipant.objects
            .filter(meeting=meeting)
            .exclude(user_id=utterance.participant_id)
            .select_related("user")
            .order_by("user_id"))
    to_nodes = [agent_node(p.user) if p.attendance == Attendance.DELEGATED
                else user_node(p.user) for p in rows]
    if not to_nodes:
        return None

    return record(meeting,
                  from_node=user_node(utterance.participant), to_nodes=to_nodes,
                  label=label, content_type=content_type,
                  surface=Surface.DISCORD, agenda=agenda,
                  occurred_at=utterance.spoken_at,
                  says=[(utterance.participant_name or utterance.participant.name,
                         utterance.body)])


def question_routed(meeting, *, asker, target, agenda=None,
                    surface=Surface.DISCORD, quote=""):
    """회의 중 — 질문이 누구의 대리인에게 향했는지."""
    if asker is None:
        return None
    return record(meeting,
                  from_node=user_node(asker), to_nodes=[agent_node(target)],
                  label="질문", content_type=FlowContentType.REQUEST,
                  surface=surface, agenda=agenda,
                  says=[(asker.name, quote)] if quote else None)


def answered(meeting, *, principal, audience: list, agenda=None,
             surface=Surface.DISCORD, quote=""):
    """
    회의 중 — 대리인이 답했습니다.

    받는 쪽은 그 자리에 있던 사람들입니다. 질문자 한 명에게만 그리면 회의에서
    모두가 들은 사실이 화면에 안 남습니다.
    """
    return record(meeting,
                  from_node=agent_node(principal),
                  to_nodes=[user_node(u) for u in audience],
                  label="대리인 답변", content_type=FlowContentType.OPINION,
                  surface=surface, agenda=agenda,
                  says=([(agent_display_name(principal), quote)] if quote else None))


def deferred(meeting, *, principal, asker, surface=Surface.DISCORD):
    """
    회의 중 — 유보했습니다.

    ## 화살표를 그리지 않습니다

    한동안 `기타`(ETC) 로 그렸는데 **잘못이었습니다.**

    유보는 필터로 걸러 보는 것이 아니라 **사용자가 반드시 답해야 하는 일**입니다.
    필터 축에 넣으면 `기타` 체크를 끈 사람에게는 안 보이고, 켠 사람에게도
    다른 화살표들과 같은 무게로 섞입니다. 둘 다 "답해야 한다" 와 어긋납니다.

        필터 축   의견 · 요청사항 · 변동사항 · 일정 · 결론 · 기타
        알림 축   유보 질문 ← 여기

    (2026-08-17 디자인 확인. 필터 6칸에 `유보` 가 없는 것은 누락이 아니라 의도입니다.)

    유보 자체는 `PendingQuestion` 으로 이미 남고, 브리핑의 `답변이 필요해요`
    섹션과 알림으로 사용자에게 갑니다. **플로우에 한 번 더 그릴 이유가 없습니다.**

    함수를 지우지 않고 남겨 둔 이유는, 유보가 났다는 사실을 플로우에서도 보여줄
    자리가 생기면(디자인 논의 중) 여기만 고치면 되기 때문입니다.
    """
    return None


def artifact_proposed(meeting, *, principal, kind: str, title: str):
    """
    회의 후 — 태스크·일정 후보가 서버에 쌓였습니다.

    ## 태스크 후보를 `결론` 으로 그리는 이유

    한동안 `계획`(`PLAN`) 으로 그렸는데, 회의 모드 필터는 여섯 칸이고
    거기에 `계획` 이 없습니다. 필터 목록은 실제로 등장한 종류만 내려주므로,
    태스크 후보가 하나 쌓이는 순간 **화면에 없는 칸이 새로 뜹니다.**

    태스크 후보가 쌓였다는 것은 회의에서 무엇이 정해졌는지에 가깝습니다.
    `요청사항` 도 후보였지만 그쪽은 누가 하기로 한 것이 정해졌을 때이고,
    후보 단계는 아직 사람이 승인하기 전입니다.

    일정 쪽은 그대로 둡니다 — 필터에 `일정` 칸이 있습니다.
    """
    content = (FlowContentType.SCHEDULE if kind == "schedule"
               else FlowContentType.CONCLUSION)
    return record(meeting,
                  from_node=agent_node(principal), to_nodes=[server_node()],
                  label=title or ("일정 제안" if kind == "schedule" else "할 일 제안"),
                  content_type=content)


def briefing_delivered(meeting, *, principal):
    """회의 후 — 불참자에게 브리핑이 전달됐습니다."""
    return record(meeting,
                  from_node=agent_node(principal), to_nodes=[user_node(principal)],
                  label="부재중 브리핑", content_type=FlowContentType.CONCLUSION)
