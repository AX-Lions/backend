"""
유보를 사용자에게 남기기.

판정은 `judge.py` 가 끝냈습니다. 여기서는 그 결과를 **본인이 돌아왔을 때 볼 수 있는
형태**로 바꿉니다. 남기지 않으면 대리인이 침묵한 사실 자체가 사라집니다.

## `defer` 스킬을 만들지 않은 이유

설계 초안에는 `defer` 를 스킬 목록에 넣었습니다. 구현하면서 뺐습니다.

스킬은 **LLM 이 부르는 것**입니다. 유보를 스킬로 두면 모델이 스스로 유보 여부를
정하게 되고, 판정을 코드로 뺀 의미가 사라집니다. 반대로 모델이 유보를 **부르지
않기로** 결정할 수도 있습니다.

유보는 판정 결과이지 모델의 선택지가 아닙니다. 그래서 루프가 직접 남깁니다.
"""
from __future__ import annotations

import logging

from django.db import transaction

from ..models import PendingQuestion

logger = logging.getLogger("bordo.agent")

#: 제목 길이. 모델 필드가 200자입니다.
_TITLE_MAX = 200
#: 목록에서 한눈에 들어오는 길이. 넘으면 잘라 씁니다.
_TITLE_SOFT = 60


def _title_of(question: str) -> str:
    """
    질문에서 목록에 뜰 한 줄을 만듭니다.

    LLM 을 부르지 않습니다. 요약을 맡기면 호출이 한 번 더 늘고, 무엇보다
    **질문이 다르게 적히면** 본인이 "내가 이런 질문을 받았나" 하고 헷갈립니다.
    원문을 그대로 자르는 편이 정확합니다.
    """
    text = " ".join((question or "").split())
    if not text:
        return "확인이 필요한 질문"
    if len(text) <= _TITLE_SOFT:
        return text[:_TITLE_MAX]
    return text[:_TITLE_SOFT].rstrip() + "…"


def _body_of(question: str, reason_message: str, evidence: list[dict]) -> str:
    """
    본문에는 **무엇을 물었고 왜 답하지 않았는지**를 함께 적습니다.

    사유만 있으면 무슨 질문이었는지 되짚을 수 없고, 질문만 있으면 대리인이
    왜 가만히 있었는지 알 수 없습니다.
    """
    parts = [f"질문: {(question or '').strip()}", "", f"보류 사유: {reason_message}"]

    names = [e.get("title_snapshot", "") for e in (evidence or [])[:5]
             if e.get("title_snapshot")]
    if names:
        parts += ["", "참고한 기록:"] + [f"- {n}" for n in names]
    return "\n".join(parts)


@transaction.atomic
def record(*, run, question: str, reason_message: str,
           evidence: list[dict] | None = None, asker=None) -> PendingQuestion | None:
    """
    유보를 `PendingQuestion` 으로 남깁니다.

    회의가 없는 실행(웹 대화)에서는 만들지 않습니다 — 모델이 회의를 필수로 요구하고,
    무엇보다 **본인과의 대화에서 나온 유보는 되물을 상대가 자기 자신**이라
    목록에 쌓아 둘 이유가 없습니다.
    """
    if run.meeting_id is None:
        return None

    # 같은 실행이 두 번 남기지 않습니다. 재시도나 중복 호출에서 목록이 불어나면
    # 사용자는 같은 질문을 여러 번 답해야 합니다.
    existing = PendingQuestion.objects.filter(run=run).first()
    if existing:
        return existing

    return PendingQuestion.objects.create(
        meeting_id=run.meeting_id,
        run=run,
        asker=asker,
        asker_name=getattr(asker, "name", "") or "",
        target_user=run.user,
        title=_title_of(question),
        body=_body_of(question, reason_message, evidence or []),
    )
