"""
질문 대상 판정.

회의에서 누군가 말했을 때 **누구의 대리인이 답할 일인가** 를 정합니다.

## 후보를 먼저 좁힙니다

LLM 에게 회의 참석자 전체를 주고 고르라고 하면, 자리에 있는 사람의 대리인까지
불러 세웁니다. 본인이 있는데 대리인이 대신 말하면 이상합니다.

    대리 참석이 켜져 있고(delegated)
    말한 사람 본인이 아니며
    자리에 없는 사람

이 셋을 만족하는 사람만 후보입니다.

## 아무도 아닐 수 있습니다

회의 발언 대부분은 질문이 아닙니다. "아무도 아님" 을 고를 수 있게 해 두지 않으면
모델은 억지로 한 명을 고릅니다.

**후보가 하나여도 물어봅니다.** 후보를 좁히는 것과 "이게 질문이긴 한가" 를 보는 것은
다른 판단인데, 이 호출 하나가 둘을 겸하고 있습니다. 후보가 하나라고 건너뛰면 질문
여부를 보는 자리가 통째로 사라져, `아 넵 감사합니다` · `ㅋㅋㅋ` 같은 발언마다
대리인이 깨어나 유보 문구를 회의에 뿌립니다.

건너뛰어도 비용이 줄지 않는다는 점도 있습니다. 잘못 깨운 대리인은 ReAct 루프를
끝까지 돌며 LLM 을 여러 번 부르므로, 여기서 아낀 한 번보다 훨씬 비쌉니다.
"""
from __future__ import annotations

import logging

from .llm import client as default_client

logger = logging.getLogger("bordo.agent")

NONE_CHOICE = "0"


def candidates(utterance):
    """
    이 발언에 답할 수 있는 대리인의 주인들.

    `attendance` 가 없는 참석자도 후보에 넣습니다 — 아직 입장 처리가 안 됐을 뿐
    실제로는 자리에 없는 경우가 있습니다. 대리 참석을 켜 뒀다는 사실을 더 믿습니다.
    """
    from apps.meetings.models import Attendance, MeetingParticipant

    qs = (MeetingParticipant.objects
          .filter(meeting_id=utterance.meeting_id, delegated=True)
          .exclude(user_id=utterance.participant_id)
          .exclude(attendance=Attendance.PRESENT)
          .select_related("user"))
    return list(qs)


def pick(utterance, client=None) -> object | None:
    """
    발언이 누구를 향한 것인지 고릅니다.

    반환은 `MeetingParticipant` 이거나 None 입니다. **후보가 하나여도 물어봅니다** —
    이 호출이 질문 여부를 거르는 유일한 자리입니다.
    """
    rows = candidates(utterance)
    if not rows:
        return None

    client = client or default_client

    listing = "\n".join(
        f"{i + 1}. {p.user_name}" for i, p in enumerate(rows))
    r = client.chat(
        [{"role": "user", "content": utterance.body}],
        system=(
            "회의 발언을 읽고, 아래 사람 중 누구에게 향한 질문인지 번호만 답하십시오.\n"
            f"{listing}\n"
            f"{NONE_CHOICE}. 아무에게도 향하지 않음 (질문이 아니거나 대상이 불분명)\n"
            "번호 하나만 출력하십시오."),
    )
    if not r.ok:
        # 판정이 안 되면 아무도 부르지 않습니다. 잘못 부르면 엉뚱한 사람의
        # 기록이 회의에 나갑니다 — 되돌릴 수 없습니다.
        logger.warning("대상 판정 실패: %.200s", r.error)
        return None

    token = (r.text or "").strip().split()[:1]
    choice = token[0].strip(".,`\"'") if token else NONE_CHOICE
    if choice == NONE_CHOICE or not choice.isdigit():
        return None

    idx = int(choice) - 1
    return rows[idx] if 0 <= idx < len(rows) else None
