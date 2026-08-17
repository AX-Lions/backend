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

    from .services import flow, react, targeting
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

    # 화면에 남기는 것은 여기서 시작합니다. 대리인이 실패하더라도 "이 질문이 저
    # 사람의 대리인에게 갔다" 는 사실은 남아야 합니다 — 답이 없는 것과 질문이
    # 닿지도 않은 것은 사용자에게 전혀 다른 이야기입니다.
    flow.delegate_prompt_given(utterance.meeting, target.user,
                               target.delegate_prompt or "")
    # 안건은 여기서 한 번만 구해 질문·답변 화살표에 함께 넘깁니다. 각자 구하면
    # 같은 조회와 같은 토큰 계산이 발언마다 두 번 돕니다.
    agenda = flow.agenda_for(utterance.meeting, utterance.body)
    flow.question_routed(utterance.meeting, asker=utterance.participant,
                         target=target.user, agenda=agenda)

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

    _record_flow(outcome, utterance, target.user, agenda)
    _speak(outcome, utterance)


def _record_flow(outcome, utterance, principal, agenda=None) -> None:
    """
    답변과 유보를 화면에 남깁니다.

    둘을 **다른 종류로** 기록합니다. 화면에서 "답했다" 와 "확인이 필요하다" 가
    같은 색으로 보이면, 유보를 보여 주는 의미가 사라집니다.

    함수 전체를 감쌉니다. `flow.record()` 안쪽만 보호하면 여기서 도는 참석자
    조회가 그 밖이라, DB 오류 하나에 예외가 위로 올라가 **`_speak()` 가 아예
    안 불립니다.** 화살표를 못 그리는 것과 대리인이 회의에서 입을 다무는 것은
    전혀 다른 이야기입니다.
    """
    from apps.meetings.models import Attendance, MeetingParticipant

    from .services import flow

    try:
        if not outcome.answered:
            flow.deferred(utterance.meeting, principal=principal,
                          asker=utterance.participant)
            return

        # 받는 쪽은 그 자리에 있던 사람들입니다. 질문자 한 명에게만 그리면
        # 회의에서 모두가 들은 사실이 화면에 남지 않습니다.
        #
        # 정렬을 반드시 겁니다. 순서가 흔들리면 to_nodes 배열의 순서도 흔들리고,
        # 조회 API 가 (보내는 쪽, 받는 쪽들) 로 화살표를 묶기 때문에 같은 두 사람
        # 사이 화살표가 어떤 회의에서는 하나로, 어떤 회의에서는 둘로 쪼개집니다.
        audience = [p.user for p in
                    MeetingParticipant.objects
                    .filter(meeting_id=utterance.meeting_id,
                            attendance=Attendance.PRESENT)
                    .exclude(user_id=principal.id)
                    .select_related("user")
                    .order_by("user_id")]
        if not audience and utterance.participant:
            # 참석 상태가 아직 안 들어온 회의도 있습니다. 최소한 질문자에게는 그립니다.
            audience = [utterance.participant]
        flow.answered(utterance.meeting, principal=principal, audience=audience,
                      agenda=agenda)
    except Exception:                                          # noqa: BLE001
        logger.exception("플로우 기록 실패 run=%s", getattr(outcome.run, "id", None))


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
