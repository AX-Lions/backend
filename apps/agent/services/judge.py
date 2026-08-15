"""
유보 판정.

> 근거가 부족하면 답을 지어내지 말고 유보한다 — **유보는 실패가 아니라 제품 가치다.**

대부분의 AI 협업 도구는 모르는 것도 그럴듯하게 답합니다. Bordo 의 차별점은
"본인 확인 필요"로 남기고 **그 판단 이유를 보여주는 것**입니다.

## 왜 LLM 에게 맡기지 않는가

"모르면 유보해"라고 시키면 모델은 답할 수 있다고 스스로를 설득합니다.
참고했던 구현은 스텝을 소진하면 "작업을 완료했습니다."로 강제 요약합니다 —
그 자리가 여기서는 유보여야 합니다.

| 하는 일 | 주체 |
|---|---|
| 의도 분류 · 검색어 생성 · 근거 관련도 라벨링 | LLM |
| **유보 여부 판정** | **이 모듈 (결정적 규칙)** |
| 답변·유보 문구 작성 | LLM |

판정을 코드가 하면 같은 입력에 같은 결과가 나오고, 왜 유보했는지 사용자에게
규칙으로 설명할 수 있습니다.

## 규칙은 순서가 있고 먼저 걸리는 것이 이깁니다

사용자에게 사유를 **하나만** 보여주기 위해서입니다. "근거도 없고 오래됐고
모순됩니다"는 무엇을 하라는 말인지 알 수 없습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings as dj_settings

from . import policy


class Reason:
    NO_EVIDENCE = "NO_EVIDENCE"
    NOT_MY_RECORD = "NOT_MY_RECORD"
    INFERRED_ONLY = "INFERRED_ONLY"
    NEEDS_DISCUSSION = "NEEDS_DISCUSSION"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"


#: 사유 → 사용자에게 보여줄 문구.
#: 대리인이 침묵한 이유를 본인이 읽고 **무엇을 하면 되는지** 알 수 있어야 합니다.
MESSAGES = {
    Reason.NO_EVIDENCE:
        "관련 기록을 찾지 못해 답변을 보류했습니다.",
    Reason.NOT_MY_RECORD:
        "본인 기록이 아닌 자료만 있어 대신 답변하지 않았습니다.",
    Reason.INFERRED_ONLY:
        "직접적인 근거 없이 추측만 가능해 보류했습니다.",
    Reason.NEEDS_DISCUSSION:
        "본인이 논의가 필요하다고 표시한 내용이라 보류했습니다.",
    Reason.LOW_CONFIDENCE:
        "본인도 확신하지 않은 기록이라 보류했습니다.",
    Reason.STALE:
        "진행 중인 작업인데 기록이 오래되어 현재 상태를 확신할 수 없습니다.",
    Reason.CONFLICTING:
        "기록끼리 내용이 어긋나 어느 쪽이 맞는지 확인이 필요합니다.",
}

MATCH_DIRECT = "direct"
MATCH_PARTIAL = "partial"
MATCH_INFERRED = "inferred"

_STATE_TYPES = ("work", "plan", "thought")


def _conf(key: str, default):
    return dj_settings.BORDO.get(key, default)


@dataclass
class Verdict:
    """
    판정 결과.

    `answer=False` 라도 **실패가 아닙니다.** 호출부는 `AgentRun` 을 `COMPLETED` 로
    닫아야 합니다. `FAILED` 는 LLM 오류·타임아웃 같은 기술적 실패에만 씁니다.
    """
    answer: bool
    reason: str = ""
    message: str = ""
    #: 판정에 실제로 쓰인 근거. 유보 사유와 함께 사용자에게 보여줍니다.
    evidence: list[dict] = field(default_factory=list)
    #: POLICY 가 붙인 제약(예: propose_only)을 그대로 넘깁니다.
    constraints: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.answer


def _defer(reason: str, evidence: list[dict]) -> Verdict:
    return Verdict(answer=False, reason=reason, message=MESSAGES[reason],
                   evidence=evidence)


def _has_conflict(items: list[dict]) -> bool:
    """
    근거끼리 어긋나는가.

    지금은 한 가지만 봅니다 — **막혀 있다고 적어 둔 작업과 곧 끝난다는 계획이
    함께 있는 경우**입니다. 이 조합이 실제로 사람을 헷갈리게 하고, 대리인이
    둘 중 하나만 골라 답하면 반대쪽 사실이 묻힙니다.

    규칙을 늘리기 전에 실제 사례를 먼저 모으는 편이 낫습니다. 근거 없이 늘리면
    멀쩡한 답변까지 유보로 막힙니다.
    """
    blocked = any(i.get("source_type") == "work" and i.get("status") == "BLOCKED"
                  for i in items)
    will_finish = any(i.get("source_type") == "plan"
                      and i.get("status") in ("DONE", "IN_PROGRESS")
                      for i in items)
    return blocked and will_finish


def judge(intent: str, evidence: list[dict], snapshot: dict | None) -> Verdict:
    """
    답할 것인가 유보할 것인가.

    `evidence` 는 검색 스킬이 모은 근거이고, `match` 라벨은 LLM 이 붙입니다.
    이 함수는 **라벨을 믿되 판정은 직접** 합니다.
    """
    # ── 1단계. POLICY 게이트 (하드) ───────────────────────
    # 여기서 막히면 LLM 생성 자체를 하지 않습니다.
    decision = policy.check(intent, snapshot)
    if not decision.allowed:
        return Verdict(answer=False, reason=decision.reason,
                       message=decision.message, evidence=[])

    # 공개할 수 없는 근거는 아예 없는 것으로 봅니다. 남겨 두면 아래 규칙이
    # "근거가 있다"고 판단해 답변으로 가고, 정작 인용할 수 없어 빈 답이 됩니다.
    usable = [e for e in (evidence or []) if policy.can_disclose(e, snapshot)]

    # ── 2단계. 근거 충분성 ────────────────────────────────
    # R1
    if not usable:
        return _defer(Reason.NO_EVIDENCE, [])

    # R2 — 대리인은 본인을 대리합니다. 남의 기록으로 답하면 대리가 아닙니다.
    mine = [e for e in usable if e.get("owner_is_principal")]
    if not mine:
        return _defer(Reason.NOT_MY_RECORD, usable)

    # R3 — 추론만으로 답하면 지어내기가 됩니다.
    if not any(e.get("match") == MATCH_DIRECT for e in mine):
        return _defer(Reason.INFERRED_ONLY, mine)

    # R4 — 본인이 직접 "논의 필요"로 표시한 것. 가장 강한 신호입니다.
    if any(e.get("requires_discussion") for e in mine):
        return _defer(Reason.NEEDS_DISCUSSION, mine)

    # R5 — 본인도 확신하지 않은 생각.
    floor = _conf("AGENT_CONFIDENCE_MIN", 0.7)
    weak = [e for e in mine
            if e.get("source_type") == "thought" and e.get("confidence") is not None
            and e["confidence"] < floor]
    if weak and len(weak) == len([e for e in mine if e.get("source_type") == "thought"]):
        # 확신 없는 생각'만' 있을 때 유보합니다. 확실한 작업 기록이 함께 있으면
        # 그쪽을 근거로 답할 수 있습니다.
        if not [e for e in mine if e.get("source_type") != "thought"]:
            return _defer(Reason.LOW_CONFIDENCE, mine)

    # R6 — 진행 중인데 기록이 오래됐습니다.
    stale_days = _conf("AGENT_STALE_DAYS", 14)
    fresh = [e for e in mine if (e.get("staleness_days") or 0) <= stale_days]
    if not fresh:
        in_progress = any(e.get("status") == "IN_PROGRESS" for e in mine)
        if in_progress:
            return _defer(Reason.STALE, mine)

    # R7
    if _has_conflict(mine):
        return _defer(Reason.CONFLICTING, mine)

    # R8
    return Verdict(answer=True, evidence=mine, constraints=decision.constraints)
