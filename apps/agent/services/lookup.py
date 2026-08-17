"""
대리인이 다른 대리인에게 묻기.

작업 플로우의 `AI 조회` 가 여기서 만들어집니다. 화면에서는 4단으로 보입니다.

    조회 이유 → 질문 → 확인된 내용 → 출처·시각

## 서버를 거칩니다

설계 2원칙입니다. **AI 끼리 직접 통신하지 않습니다.** 묻는 쪽 대리인이 상대
대리인을 부르는 것이 아니라, 서버가 상대의 실행을 새로 돌리고 결과를 양쪽에
기록합니다. `trace_id` 로 한 줄기를 잇고 `hop_count` 로 깊이를 셉니다.

## 무한 조회를 막습니다

A 의 대리인이 B 에게 묻고, B 의 대리인이 다시 A 에게 묻고… 를 그냥 두면 끝나지
않습니다. `max_hops`(기본 3)를 넘으면 더 묻지 않고 그대로 유보합니다.

## 답을 못 얻어도 기록은 남깁니다

상대가 유보하면 `answer` 가 빕니다. **그래도 남깁니다** — "물어봤는데 답을 못
받았다" 는 사실 자체가 화면에 있어야 합니다. 안 남기면 사용자는 자기 대리인이
알아보지도 않은 줄 압니다.
"""
from __future__ import annotations

import logging

from django.conf import settings as dj_settings
from django.db import transaction
from django.utils import timezone

from ..models import AgentLookup

logger = logging.getLogger("bordo.agent")

#: 주제 길이. 모델이 200자입니다.
_TOPIC_MAX = 200


def ask_peer(*, asker, target, topic: str, reason: str, question: str,
             project_id, run=None, source: str = "") -> AgentLookup | None:
    """
    상대 대리인에게 묻고 결과를 남깁니다.

    `(기록, )` 이 아니라 기록만 돌려줍니다. 호출부가 필요한 것은 "무엇을
    확인했는가" 이고 그건 `lookup.answer` 에 들어 있습니다.

    실패하면 `None` 입니다 — 조회 한 건을 못 했다고 부른 쪽 실행이 죽으면 안 됩니다.
    """
    from . import react

    if asker is None or target is None or asker.id == target.id:
        # 자기 자신에게 묻는 것은 조회가 아닙니다. 자기 기록은 그냥 검색하면 됩니다.
        return None

    hops = (run.hop_count if run else 0) + 1
    max_hops = dj_settings.BORDO.get("MAX_HOPS", 3)
    if hops > max_hops:
        # 여기서 끊지 않으면 대리인끼리 서로 묻는 것이 끝나지 않습니다.
        logger.info("조회 깊이 초과 asker=%s target=%s hops=%s",
                    asker.id, target.id, hops)
        return None

    try:
        outcome = react.run(
            principal=target,
            question=question,
            meeting=None,
            actor_id=asker.id,
            asker=asker,
            project_id=project_id,
            trace_id=getattr(run, "trace_id", None) or getattr(run, "id", None),
            hop_count=hops,
        )
    except Exception:                                          # noqa: BLE001
        logger.exception("피어 조회 실패 asker=%s target=%s", asker.id, target.id)
        return None

    with transaction.atomic():
        lookup = AgentLookup.objects.create(
            project_id=project_id,
            asker=asker, target=target,
            topic=(topic or question)[:_TOPIC_MAX],
            reason=reason or "",
            question=question,
            # 유보했으면 빕니다. 빈 채로 남기는 것이 안 남기는 것보다 낫습니다.
            answer=outcome.text if outcome.answered else "",
            source=source,
            run=run,
            occurred_at=timezone.now(),
        )
        draw_edge(lookup)
    return lookup


def draw_edge(lookup: AgentLookup) -> None:
    """
    작업 플로우에 `AI 조회` 화살표를 남깁니다.

    화살표는 **대리인 노드끼리** 잇습니다. 사람끼리가 아닙니다 — 물어본 것은
    사람이 아니라 대리인이고, 화면에서도 Bordo 프로필 사이에 그려집니다.

    실패해도 조회 기록은 남깁니다. 화살표 하나 때문에 기록이 사라지면 안 됩니다.
    """
    from apps.meetings.models import FlowCategory, FlowContentType, FlowEdge

    from . import flow

    try:
        src = flow.agent_node(lookup.asker)
        dst = flow.agent_node(lookup.target)

        # DB 쓰기를 세이브포인트로 감쌉니다.
        #
        # 이 함수는 `ask_peer()` 의 트랜잭션 안에서 불립니다. 세이브포인트 없이
        # 여기서 진짜 DB 오류가 나면 **예외를 잡아도 트랜잭션은 이미 오염됩니다.**
        # 바깥 블록이 예외 없이 끝나도 Django 가 `needs_rollback` 을 보고 통째로
        # 되돌리므로, **방금 만든 AgentLookup 까지 조용히 사라집니다.**
        # 그러면 `ask_peer()` 는 성공한 것처럼 객체를 돌려주는데 DB 에는 없습니다.
        #
        # `flow.record()` 에서 이미 한 번 겪은 것과 같은 자리입니다.
        with transaction.atomic():
            edge = FlowEdge.objects.create(
                project_id=lookup.project_id,
                meeting=None,                   # 작업 플로우는 회의가 없습니다
                category=FlowCategory.WORK,
                content_type=FlowContentType.AI_LOOKUP,
                source=lookup.source,
                from_node=src, to_nodes=[dst],
                participant_ids=[str(lookup.asker_id), str(lookup.target_id)],
                label=lookup.topic[:60],
                direction_label=f"{src['name']} → {dst['name']}"[:200],
                occurred_at=lookup.occurred_at,
            )
            # 화살표를 누르면 4단 상세로 갑니다.
            AgentLookup.objects.filter(pk=lookup.pk).update(edge=edge)
        lookup.edge = edge
    except Exception:                                          # noqa: BLE001
        logger.exception("AI 조회 화살표 기록 실패 lookup=%s", lookup.pk)
