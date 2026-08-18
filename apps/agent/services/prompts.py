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

- 한국어로, 회의에서 사람이 말하듯.
- 근거가 된 작업·문서 이름을 함께 밝히십시오.
- 진행률·상태·막힌 이유가 있으면 그대로 전하십시오.
"""

#: 말투. **어떻게 말할지만** 바꿉니다.
#
# 무엇을 말할지는 건드리지 않습니다. "간결하게" 를 골랐다는 이유로 유보 사유나
# 근거 출처가 잘려 나가면, 말투 설정이 판정을 바꾸는 셈이 됩니다. 그래서 각
# 항목에 **무엇은 줄이지 말라**를 함께 적습니다.
_BY_TONE = {
    "FORMAL":
        "정중한 존댓말로 말하십시오(…합니다 / …드립니다). 격식을 지키되 "
        "장황해지지 않게 하십시오.",
    "FRIENDLY":
        "부드러운 구어체로 말하십시오(…해요 / …할게요). 친근하게 말하되 "
        "사실을 흐리거나 확답처럼 들리게 하지 마십시오.",
    "CONCISE":
        "군더더기 없이 핵심만 말하십시오. 다만 **근거와 유보 사유는 줄이지 "
        "마십시오** — 짧게 만들려고 그것을 빼면 왜 그렇게 답했는지가 사라집니다.",
}

_CONTEXT = """\
## 지금 상황

{lines}
"""

_INSTRUCTION = """\
## {principal} 님이 미리 남긴 지시

{prompt}

이 지시는 회의 전에 본인이 직접 적은 것입니다. 위 규칙과 어긋나지 않는 범위에서 따르십시오.
"""

_STANDING = """\
## {principal} 님이 평소 정해 둔 것

{prompts}

설정 화면에 저장해 둔 지시입니다. 회의별 지시와 어긋나면 **회의별 지시가 우선**입니다 —
그쪽이 더 최근이고 이 회의를 보고 적은 것입니다.
"""

#: 평소 지시를 몇 개까지 싣는가.
#
# 저장은 무제한이지만 전부 실으면 규칙보다 길어져 모델이 앞쪽 규칙을 흘립니다.
# 최근 것부터 다섯 개면 사람이 실제로 관리하는 범위입니다.
_STANDING_MAX = 5

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
                 delegate_prompt: str = "", constraints: list[str] | None = None,
                 tone: str = "", standing_prompts: list[str] | None = None) -> str:
    parts = [_BASE.format(principal=principal_name)]

    # 말투는 규칙 바로 뒤, 상황·의도보다 앞에 둡니다. 뒤에 붙이면 의도별 주의와
    # 섞여 "일정은 확정하지 마십시오" 같은 문장이 말투 지시로 읽힙니다.
    style = _BY_TONE.get(tone)
    if style:
        parts.append(f"## 말투\n\n{style}\n")

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

    # 평소 지시를 회의별 지시보다 **먼저** 놓습니다. 뒤에 오는 것이 더 최근이고
    # 이 회의를 보고 적은 것이라, 어긋날 때 뒤가 이기는 순서가 자연스럽습니다.
    if standing_prompts:
        body = "\n\n".join(f"- {p.strip()}" for p in standing_prompts if p and p.strip())
        if body:
            parts.append(_STANDING.format(principal=principal_name, prompts=body))

    if delegate_prompt:
        parts.append(_INSTRUCTION.format(principal=principal_name,
                                         prompt=delegate_prompt.strip()))

    return "\n".join(parts)


def standing_prompts_for(user) -> list[str]:
    """
    설정 화면에 저장해 둔 지시.

    **한동안 아무도 안 읽었습니다.** `AgentPrompt` 는 CRUD 가 다 도는데 대리인이
    한 번도 보지 않아서, 사용자는 "말하면 안 되는 것" 을 적어 두고 대리인이
    그것을 지킨다고 믿고 있었습니다. 저장되는데 안 지켜지는 설정은 없는 것보다
    나쁩니다.
    """
    from ..models import AgentPrompt

    return list(AgentPrompt.objects
                .filter(user=user)
                .values_list("body", flat=True)[:_STANDING_MAX])


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
