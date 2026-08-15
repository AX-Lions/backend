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
            "user_id": str(owner.id), "name": f"{owner.name}의 Bordo",
            "avatar_url": owner.avatar_url or None}


def server_node() -> dict:
    return {"id": "server", "kind": "SERVER", "user_id": None, "name": "서버",
            "avatar_url": None}


def record(meeting, *, from_node: dict, to_nodes: list[dict], label: str,
           content_type: str, surface: str = Surface.SERVICE,
           category: str = FlowCategory.MEETING, agenda=None,
           occurred_at=None) -> FlowEdge | None:
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
            occurred_at=occurred_at or timezone.now(),
        )
        edge.opacity = edge.compute_opacity()

        # 저장을 세이브포인트로 감쌉니다.
        #
        # 이 함수는 트랜잭션 안에서도 불립니다(briefing.build_for_user 는
        # @transaction.atomic). 거기서 DB 오류가 나면 **예외를 잡아도 트랜잭션은
        # 이미 오염됩니다** — PostgreSQL 이 트랜잭션을 abort 상태로 만들어 이후
        # 쿼리를 전부 거부합니다. 잡아서 넘어간다는 이 함수의 약속이 그대로
        # 깨지고, 브리핑이 통째로 날아갑니다.
        with transaction.atomic():
            edge.save()
        return edge
    except Exception:                                          # noqa: BLE001
        # 화살표 하나를 못 그렸다고 대리인의 답변이 취소되면 안 됩니다.
        logger.exception("플로우 엣지 기록 실패 meeting=%s", getattr(meeting, "id", None))
        return None


# ═══════════════════════════════════════════ 상황별 기록

def delegate_prompt_given(meeting, user, prompt: str):
    """
    회의 전 — 본인이 대리인에게 남긴 지시.

    발언마다 불리므로 **한 회의에 한 번만** 남깁니다. 매번 그리면 질문 열 개짜리
    회의에서 같은 화살표가 열 번 겹칩니다.
    """
    if not (prompt or "").strip():
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


def question_routed(meeting, *, asker, target, surface=Surface.DISCORD):
    """회의 중 — 질문이 누구의 대리인에게 향했는지."""
    if asker is None:
        return None
    return record(meeting,
                  from_node=user_node(asker), to_nodes=[agent_node(target)],
                  label="질문", content_type=FlowContentType.REQUEST,
                  surface=surface)


def answered(meeting, *, principal, audience: list, surface=Surface.DISCORD):
    """
    회의 중 — 대리인이 답했습니다.

    받는 쪽은 그 자리에 있던 사람들입니다. 질문자 한 명에게만 그리면 회의에서
    모두가 들은 사실이 화면에 안 남습니다.
    """
    return record(meeting,
                  from_node=agent_node(principal),
                  to_nodes=[user_node(u) for u in audience],
                  label="대리인 답변", content_type=FlowContentType.OPINION,
                  surface=surface)


def deferred(meeting, *, principal, asker, surface=Surface.DISCORD):
    """
    회의 중 — 유보했습니다.

    **답변과 다른 content_type 을 씁니다.** 화면에서 "답했다" 와 "확인이 필요하다" 가
    같은 색으로 보이면, 유보를 보여주는 의미가 사라집니다.
    """
    return record(meeting,
                  from_node=agent_node(principal),
                  to_nodes=[user_node(asker)] if asker else [server_node()],
                  label="본인 확인 필요", content_type=FlowContentType.ETC,
                  surface=surface)


def artifact_proposed(meeting, *, principal, kind: str, title: str):
    """회의 후 — 태스크·일정 후보가 서버에 쌓였습니다."""
    content = (FlowContentType.SCHEDULE if kind == "schedule"
               else FlowContentType.PLAN)
    return record(meeting,
                  from_node=agent_node(principal), to_nodes=[server_node()],
                  label=title or ("일정 제안" if kind == "schedule" else "할 일 제안"),
                  content_type=content)


def briefing_delivered(meeting, *, principal):
    """회의 후 — 불참자에게 브리핑이 전달됐습니다."""
    return record(meeting,
                  from_node=agent_node(principal), to_nodes=[user_node(principal)],
                  label="부재중 브리핑", content_type=FlowContentType.CONCLUSION)
