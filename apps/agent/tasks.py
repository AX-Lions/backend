"""
대리인 진입점.

Discord 담당(A)이 `Utterance` 를 저장한 직후 부릅니다.
`CLAUDE.md` 의 계약 그대로 **반환값 없음, 대기 없음** 입니다.

    run_agent_for_utterance(utterance_id)

봇은 결과를 기다리지 않습니다. 대리인이 답하든 유보하든 결과는 `AgentRun` 과
`OutboxEvent` 에 쓰이고, 봇은 큐를 폴링해 가져갑니다.

## 왜 여기서 예외를 삼키는가

발언 하나가 대리인을 죽이면 **다음 발언도 처리되지 않습니다.** 회의가 진행되는
동안 조용히 멈춰 있는 것이 가장 나쁩니다. 실패는 `AgentRun.FAILED` 로 남기고
다음 발언을 받습니다.
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("bordo.agent")


@shared_task(name="agent.run_for_utterance")
def run_agent_for_utterance(utterance_id: str) -> None:
    from apps.meetings.models import Utterance

    from .services import react, targeting
    from .services.skills import SkillContext
    from .services.skills.act import SpeakInMeetingSkill

    utterance = (Utterance.objects
                 .filter(pk=utterance_id)
                 .select_related("meeting", "participant")
                 .first())
    if utterance is None:
        logger.warning("발언을 찾을 수 없습니다: %s", utterance_id)
        return

    try:
        target = targeting.pick(utterance)
    except Exception:                                          # noqa: BLE001
        logger.exception("대상 판정 실패 utterance=%s", utterance_id)
        return

    if target is None:
        # 회의 발언 대부분은 질문이 아닙니다. 아무도 안 부르는 것이 정상입니다.
        return

    try:
        outcome = react.run(
            principal=target.user,
            question=utterance.body,
            meeting=utterance.meeting,
            actor_id=utterance.participant_id,
            asker=utterance.participant,
            delegate_prompt=target.delegate_prompt or "",
        )
    except Exception:                                          # noqa: BLE001
        # 여기서 터지면 다음 발언도 처리되지 않습니다. 회의 중에 조용히 멈춰 있는
        # 것이 가장 나쁩니다.
        logger.exception("대리인 실행 실패 utterance=%s", utterance_id)
        return

    if outcome.error:
        # 기술적 실패는 회의에 말하지 않습니다. "지금 오류가 났습니다" 를 대리인이
        # 떠들면 회의가 어지러워지고, 사람이 할 수 있는 일도 없습니다.
        return

    _speak(outcome, utterance)


def _speak(outcome, utterance) -> None:
    """
    답변이든 유보든 회의에 전달합니다.

    **유보도 말합니다.** 대리인이 아무 말 없이 있으면 회의는 답을 기다리며
    멈추거나, 그 사람 몫을 빼고 결정해 버립니다. "본인 확인이 필요합니다" 는
    그 자리에서 나와야 쓸모가 있습니다.
    """
    from .services.skills import SkillContext
    from .services.skills.act import SpeakInMeetingSkill

    body = (outcome.text or "").strip()
    if not body:
        return

    ctx = SkillContext(
        principal_id=str(outcome.run.user_id),
        actor_id=str(utterance.participant_id or ""),
        meeting_id=str(utterance.meeting_id),
        project_id=str(utterance.meeting.project_id),
        run_id=str(outcome.run.id),
    )
    result = SpeakInMeetingSkill().run({"body": body}, ctx)
    if not result.ok:
        logger.error("회의 발언 실패 run=%s: %s", outcome.run.id, result.message)
