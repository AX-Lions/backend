"""
시스템 프롬프트 조립.

프롬프트를 코드 여기저기에 흩으면 **대리인이 왜 그렇게 말했는지** 추적이 안 됩니다.
한곳에 모아 두고, 실제로 쓴 프롬프트는 `AgentRun` 에 남깁니다.

## 프롬프트에 판정을 맡기지 않습니다

"근거가 없으면 답하지 마"를 프롬프트에 적어도 모델은 답합니다. 유보는 `judge.py`
가 코드로 정하고, 프롬프트는 **어떤 도구를 어떻게 쓸지**만 안내합니다.
"""
from __future__ import annotations

from .policy import Intent

_BASE = """\
당신은 회의에 참석하지 못한 {principal} 님을 대신하는 AI 대리인입니다.

## 지켜야 할 것

1. **{principal} 님의 기록에 있는 내용만 말합니다.** 기록에 없으면 추측하지 말고
   그대로 두십시오. 확인은 본인이 합니다.
2. 답하기 전에 반드시 `search_records` 또는 `search_meeting` 으로 근거를 찾으십시오.
3. 여러 단계가 필요하면 `think` 로 먼저 순서를 정하십시오.
4. 남의 기록이나 회의 밖 이야기를 근거로 삼지 마십시오.

## 답변 방식

- 한국어로, 회의에서 사람이 말하듯 간결하게.
- 근거가 된 작업·문서 이름을 함께 밝히십시오.
- 진행률·상태·막힌 이유가 있으면 그대로 전하십시오.
"""

_CONTEXT = """\
## 지금 상황

{lines}
"""

_INSTRUCTION = """\
## {principal} 님이 미리 남긴 지시

{prompt}

이 지시는 회의 전에 본인이 직접 적은 것입니다. 위 규칙과 어긋나지 않는 범위에서 따르십시오.
"""

#: 의도별로 덧붙이는 주의. POLICY 가 허용한 뒤에도 표현 수위는 다릅니다.
_BY_INTENT = {
    Intent.FEASIBILITY:
        "구현 가능성을 묻고 있습니다. 기록에 근거가 있을 때만 말하고, "
        "확답 대신 현재 상태를 그대로 전하십시오.",
    Intent.SCHEDULE:
        "일정에 관한 질문입니다. **확정하지 마십시오.** 제안까지만 하고 "
        "확정은 본인이 한다는 점을 밝히십시오.",
    Intent.STATUS:
        "진행 상황을 묻고 있습니다. 진행률과 막힌 이유를 함께 전하십시오.",
}


def build_system(principal_name: str, *, intent: str = "",
                 meeting_title: str = "", project_name: str = "",
                 delegate_prompt: str = "", constraints: list[str] | None = None) -> str:
    parts = [_BASE.format(principal=principal_name)]

    lines = []
    if project_name:
        lines.append(f"- 프로젝트: {project_name}")
    if meeting_title:
        lines.append(f"- 회의: {meeting_title}")
    if lines:
        parts.append(_CONTEXT.format(lines="\n".join(lines)))

    note = _BY_INTENT.get(intent)
    if note:
        parts.append(f"## 이 질문에 대해\n\n{note}\n")

    if "propose_only" in (constraints or []):
        # POLICY 가 붙인 제약을 프롬프트에도 옮깁니다. 스킬만 막으면 모델이
        # 말로 확정해 버리고, 사람은 확정된 줄 압니다.
        parts.append("## 제약\n\n확정 표현을 쓰지 마십시오. "
                     "'이렇게 하면 어떨까요' 처럼 제안으로만 말하십시오.\n")

    if delegate_prompt:
        parts.append(_INSTRUCTION.format(principal=principal_name,
                                         prompt=delegate_prompt.strip()))

    return "\n".join(parts)


def build_defer_message(reason_message: str, evidence: list[dict]) -> str:
    """
    유보를 회의에 알릴 때 쓰는 문구.

    **LLM 을 부르지 않습니다.** 유보 사유는 코드가 정한 것이라 그대로 전달해야
    하고, 모델에게 다시 쓰게 하면 "아마 곧 될 것 같습니다" 같은 말이 섞입니다.
    """
    head = f"본인 확인이 필요해 답변을 보류했습니다. {reason_message}"
    if not evidence:
        return head

    names = [e.get("title_snapshot", "") for e in evidence[:3] if e.get("title_snapshot")]
    if not names:
        return head
    return f"{head}\n참고한 기록: " + ", ".join(names)
