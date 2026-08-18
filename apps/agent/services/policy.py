"""
POLICY 검사.

`AgentSettings` 의 O/X 4종을 읽어 **대리인이 이 질문에 답해도 되는가**를 판정합니다.
지금까지 이 설정은 저장만 되고 아무도 읽지 않았습니다 — 이 모듈이 첫 독자입니다.

## 왜 LLM 이 아니라 코드가 판정하는가

모델에게 "설정을 보고 알아서 판단해"라고 시키면 답하고 싶은 쪽으로 스스로를
설득합니다. 판정을 코드로 두면 **같은 입력에 같은 결과**가 나오고, 왜 막혔는지
사용자에게 규칙으로 설명할 수 있습니다.

LLM 은 의도를 분류하고 문구를 쓸 뿐, 허용 여부는 여기서 정합니다.

## 스냅샷을 받는 이유

모델 인스턴스가 아니라 dict 를 받습니다. 실행 중에 사용자가 설정을 바꿔도
**한 번의 실행은 시작 시점의 정책으로 끝나야** 하기 때문입니다.
`AgentRun.settings_snapshot` 에 그대로 남아 "그때 어떤 정책으로 답했나"를 재현합니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Intent:
    """
    질문 의도. LLM 이 분류하고, 여기 매핑은 코드가 합니다.

    분류를 틀리면 엉뚱한 스위치를 보게 되므로, 애매하면 `OTHER` 가 아니라
    **더 좁은 쪽**으로 분류하도록 프롬프트에서 유도합니다. 과하게 막히는 것이
    과하게 새는 것보다 낫습니다.
    """
    FEASIBILITY = "FEASIBILITY"   # 이거 구현 가능한가요
    SCHEDULE = "SCHEDULE"         # 일정을 당길 수 있나요
    STATUS = "STATUS"             # 지금 어디까지 됐나요
    CLARIFY = "CLARIFY"           # 대리인이 되묻는 경우
    OTHER = "OTHER"

    ALL = (FEASIBILITY, SCHEDULE, STATUS, CLARIFY, OTHER)


class Reason:
    """거부 사유. 사용자에게 그대로 보여줄 문구와 짝지어 둡니다."""
    FEASIBILITY = "POLICY_FEASIBILITY"
    SCHEDULE = "POLICY_SCHEDULE"
    DISCLOSURE = "POLICY_DISCLOSURE"
    CLARIFY = "POLICY_CLARIFY"


#: 사유 코드 → 사용자에게 보여줄 한국어.
#: "권한 없음" 이 아니라 **본인이 무엇을 켜면 되는지**까지 적습니다.
MESSAGES = {
    Reason.FEASIBILITY: "구현 가능성에 대한 답변을 사용하지 않도록 설정하셨습니다.",
    Reason.SCHEDULE: "일정 변경에 대한 답변을 사용하지 않도록 설정하셨습니다.",
    Reason.DISCLOSURE: "작업·계획·생각을 공개하지 않도록 설정하셨습니다.",
    Reason.CLARIFY: "회의 중 되묻지 않도록 설정하셨습니다.",
}

#: 의도 → 검사할 스위치.
_SWITCH = {
    Intent.FEASIBILITY: ("mention_feasibility", Reason.FEASIBILITY),
    Intent.SCHEDULE: ("allow_schedule_change", Reason.SCHEDULE),
    Intent.STATUS: ("disclose_work_plan_thought", Reason.DISCLOSURE),
    Intent.CLARIFY: ("allow_midmeeting_question", Reason.CLARIFY),
}

#: 기본값. 설정 행이 없는 사용자도 대리인이 돌아야 하므로 모델 기본값과 맞춥니다.
DEFAULTS = {
    "mention_feasibility": True,
    "allow_schedule_change": True,
    "allow_midmeeting_question": False,
    "disclose_work_plan_thought": True,
}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    message: str = ""
    #: 허용이되 제약이 붙는 경우. 예) 일정은 제안만, 확정은 사람이.
    constraints: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Decision(allowed=True)


def _deny(reason: str) -> Decision:
    return Decision(allowed=False, reason=reason, message=MESSAGES[reason])


def check(intent: str, snapshot: dict | None) -> Decision:
    """
    이 의도로 답해도 되는가.

    `snapshot` 은 `AgentSettings.as_snapshot()` 결과입니다. None 이면 기본값을 씁니다 —
    설정 화면에 한 번도 안 들어간 사용자의 대리인이 아예 안 도는 것보다,
    모델 기본값으로 도는 편이 낫습니다.
    """
    s = {**DEFAULTS, **(snapshot or {})}

    pair = _SWITCH.get(intent)
    if pair is None:
        # OTHER 및 미분류. POLICY 는 통과시키고 근거 충분성(judge)에 맡깁니다.
        return ALLOW

    key, reason = pair
    if not s.get(key, DEFAULTS[key]):
        return _deny(reason)

    if intent == Intent.SCHEDULE:
        # 허용해도 확정은 못 합니다. 1원칙(사람 최종 승인)상 대리인은 후보만 만듭니다.
        # 이 제약을 여기서 붙여야 프롬프트와 스킬이 같은 규칙을 봅니다.
        return Decision(allowed=True, constraints=["propose_only"])

    return ALLOW


def can_disclose(evidence_item: dict, snapshot: dict | None) -> bool:
    """
    이 근거 하나를 발언에 써도 되는가.

    `check()` 가 질문 단위라면 이것은 **근거 단위**입니다. 의도가 STATUS 가 아니어도
    답변에 작업 기록을 인용하는 순간 공개가 일어나므로, 인용 시점에 한 번 더 봅니다.

    `private` 문서는 설정과 무관하게 막습니다 — 본인이 비공개로 표시한 것을
    대리인이 대신 공개하면 설정 화면의 어떤 스위치로도 되돌릴 수 없습니다.

    ## 개인 설정과 회의별 범위는 **둘 다** 통과해야 합니다

    개인 설정(`disclose_work_plan_thought`)은 평소 기준이고, 불참 팝업에서 고른
    `delegate_sources` 는 이 회의에서만 쓰는 범위입니다. 회의에서 켠다고 개인
    설정을 뚫지 못하고, 개인 설정이 켜져 있어도 이 회의에서 끈 것은 안 나갑니다.

    `None` 은 고른 적이 없다(제한 없음)이고 `[]` 는 아무것도 쓰지 말라는 뜻입니다.
    둘을 같게 다루면 "대리인은 보내되 내 기록은 쓰지 마라" 가 성립하지 않습니다.
    """
    if evidence_item.get("visibility") == "private":
        return False

    source_type = evidence_item.get("source_type")
    s = {**DEFAULTS, **(snapshot or {})}

    if source_type in ("work", "plan", "thought"):
        if not s.get("disclose_work_plan_thought"):
            return False

    allowed = s.get("delegate_sources")
    # 회의 발언(`meeting`)은 범위 밖입니다. 그 자리에 있던 사람이 이미 다 들은
    # 말이라 대리인이 인용해도 새로 공개되는 것이 없습니다.
    if allowed is not None and source_type not in (None, "meeting"):
        return source_type in allowed

    return True
