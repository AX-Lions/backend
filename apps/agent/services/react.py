"""
ReAct 루프.

지금까지 만든 조각을 잇습니다.

    질문 → 의도 분류 → POLICY 게이트 → 도구 반복 호출 → 근거 수집 → 유보 판정 → 답변/유보

## 루프가 하지 않는 것

**답할지 말지 결정하지 않습니다.** 그건 `judge.py` 가 합니다.
루프는 근거를 모으고 상태를 기록할 뿐입니다. 이 경계가 무너지면 "모델이 답하고
싶어 해서 답했다"가 되고, 왜 그렇게 판단했는지 설명할 수 없게 됩니다.

## 출력을 직접 내보내지 않습니다

`AgentRun` 에 쓰고 결과 객체를 돌려줄 뿐, Discord 발송이나 SSE 스트리밍은
호출부가 정합니다. 회의 대리(비동기)와 웹 대화(동기)가 같은 루프를 쓰되
출력만 갈리기 때문입니다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings as dj_settings
from django.utils import timezone

from ..models import AgentRun
from . import deferral, judge, policy, prompts
from .llm import LLMClient, LLMResponse
from .llm import client as default_client
from .skills import SkillContext, SkillKind
from .skills import registry as default_registry

logger = logging.getLogger("bordo.agent")

#: 도구 호출 반복 상한. 넘으면 근거가 모인 만큼으로 판정합니다.
#: 무한히 돌면 비용이 새고, 강제로 답을 만들게 하면 지어내기가 됩니다.
MAX_STEPS = 6


@dataclass
class RunOutcome:
    run: AgentRun
    answered: bool
    text: str = ""
    reason: str = ""
    evidence: list[dict] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    error: str = ""
    #: 유보로 끝났다면 생성된 유보 질문. 회의가 없는 실행에서는 None 입니다.
    pending_question: object = None

    def __bool__(self) -> bool:
        return self.answered


def _classify(client: LLMClient, question: str) -> str:
    """
    질문 의도 분류.

    분류만 시키고 판정은 시키지 않습니다. 애매하면 좁은 쪽으로 가도록 유도합니다 —
    **과하게 막히는 것이 과하게 새는 것보다 낫습니다.**
    """
    r = client.chat(
        [{"role": "user", "content": question}],
        system=("다음 질문의 의도를 한 단어로만 답하십시오. "
                "FEASIBILITY(구현 가능성) SCHEDULE(일정) STATUS(진행상황) "
                "CLARIFY(되묻기) OTHER 중 하나. "
                "애매하면 OTHER 대신 더 좁은 쪽을 고르십시오."),
    )
    if not r.ok:
        return Intent_OTHER
    word = (r.text or "").strip().upper().split()[:1]
    guess = word[0].strip(".,`\"'") if word else ""
    return guess if guess in policy.Intent.ALL else Intent_OTHER


Intent_OTHER = policy.Intent.OTHER


def run(*, principal, question: str, meeting=None, project_id=None,
        actor_id=None, asker=None, delegate_prompt: str = "", allow_private: bool = False,
        trace_id=None, hop_count: int = 0,
        client: LLMClient | None = None, registry=None) -> RunOutcome:
    """
    대리인 실행 한 번.

    `principal` 은 대리 대상(User), `actor_id` 는 질문한 사람입니다.
    """
    client = client or default_client
    registry = registry or default_registry

    snapshot = _snapshot_of(principal)
    max_hops = dj_settings.BORDO.get("MAX_HOPS", 3)

    run_obj = AgentRun.objects.create(
        user=principal, meeting=meeting,
        status=AgentRun.Status.RECEIVED,
        trace_id=trace_id, hop_count=hop_count, max_hops=max_hops,
        settings_snapshot=snapshot,
    )

    # ── AI↔AI 무한 대화 차단 ──────────────────────────────
    # 대리인이 다른 대리인에게 되묻고 그쪽이 또 되물으면 끝나지 않습니다.
    if hop_count >= max_hops:
        return _finish(run_obj, answered=False, reason="MAX_HOPS",
                       text="대리인끼리 주고받은 횟수가 상한에 닿아 본인 확인이 필요합니다.")

    steps: list[dict] = []
    evidence: list[dict] = []
    seen: set = set()

    def _step(step_kind: str, **payload):
        # 인자 이름을 kind 로 두면 payload 의 kind 키와 부딪혀 TypeError 로 죽습니다.
        # 루프 한가운데서 터지는 자리라 실제로 실행하기 전에는 안 드러났습니다.
        steps.append({"kind": step_kind, "at": timezone.now().isoformat(), **payload})

    try:
        # ── 1. 의도 분류 ──────────────────────────────────
        _set(run_obj, AgentRun.Status.ANALYZING)
        intent = _classify(client, question)
        _step("intent", intent=intent)

        # ── 2. POLICY 게이트 ──────────────────────────────
        _set(run_obj, AgentRun.Status.CHECKING_POLICY)
        gate = policy.check(intent, snapshot)
        _step("policy", intent=intent, allowed=gate.allowed,
              reason=gate.reason, constraints=gate.constraints)
        if not gate.allowed:
            # 여기서 막히면 LLM 생성 자체를 하지 않습니다. 토큰도 아끼고,
            # 무엇보다 만들어 두면 새어 나갈 자리가 생깁니다.
            run_obj.steps = steps
            return _finish(run_obj, answered=False, reason=gate.reason,
                           text=prompts.build_defer_message(gate.message, []),
                           deferred=(question, gate.message, [], asker))

        # ── 3. 도구 반복 호출 ─────────────────────────────
        _set(run_obj, AgentRun.Status.SEARCHING)
        system = prompts.build_system(
            principal.name,
            intent=intent,
            meeting_title=getattr(meeting, "title", ""),
            project_name=getattr(meeting, "project_name", ""),
            delegate_prompt=delegate_prompt,
            constraints=gate.constraints,
        )
        ctx = SkillContext(
            principal_id=str(principal.id), actor_id=str(actor_id or principal.id),
            meeting_id=str(meeting.id) if meeting else None,
            project_id=str(project_id or getattr(meeting, "project_id", "") or "") or None,
            run_id=str(run_obj.id), settings_snapshot=snapshot,
            allow_private=allow_private,
        )
        # 회의 대리는 읽기만 합니다. 쓰기 스킬은 별도 단계에서 붙습니다 —
        # 카탈로그에 없으면 모델이 부를 수도 없습니다.
        catalog = registry.build_catalog(kind=SkillKind.READ)

        messages = [{"role": "user", "content": question}]
        answer_text = ""

        for i in range(MAX_STEPS):
            resp: LLMResponse = client.chat(messages, catalog, system)
            if not resp.ok:
                _step("llm_error", error=resp.error[:300],
                      error_kind=resp.error_kind)
                run_obj.steps = steps
                run_obj.evidence = evidence
                return _fail(run_obj, resp.error)

            if not resp.tool_calls:
                answer_text = resp.text
                _step("answer_draft", tokens=resp.total_tokens)
                break

            messages.append(LLMClient.assistant_message(resp))
            for call in resp.tool_calls:
                result = registry.dispatch(call.name, call.arguments, ctx)
                _step("skill", name=call.name, args=call.arguments,
                      ok=result.ok, message=result.message,
                      found=len(result.evidence))
                # 같은 기록을 두 번 담지 않습니다. 모델은 검색어를 바꿔 가며
                # 여러 번 부르는데, 그때마다 쌓으면 같은 근거가 여러 건으로
                # 세어져 유보 판정이 "근거가 많다" 고 착각합니다.
                for item in result.evidence:
                    key = (item.get("source_type"), item.get("source_id"))
                    if key not in seen:
                        seen.add(key)
                        evidence.append(item)
                messages.append(LLMClient.tool_message(call, result.data))
        else:
            # 상한 소진. 강제로 답을 만들게 하지 않습니다 — 그 순간 지어내기가
            # 됩니다. 모인 근거로 판정하고, 부족하면 유보로 끝납니다.
            _step("max_steps", limit=MAX_STEPS)

        # ── 4. 유보 판정 ──────────────────────────────────
        run_obj.steps = steps
        run_obj.evidence = evidence
        verdict = judge.judge(intent, evidence, snapshot)
        _step("verdict", answer=verdict.answer, reason=verdict.reason)
        run_obj.steps = steps

        if not verdict.answer:
            return _finish(run_obj, answered=False, reason=verdict.reason,
                           text=prompts.build_defer_message(verdict.message,
                                                            verdict.evidence),
                           evidence=verdict.evidence,
                           deferred=(question, verdict.message,
                                     verdict.evidence, asker))

        # 근거는 충분한데 모델이 문장을 못 만든 경우. 억지로 채우지 않습니다.
        if not answer_text:
            msg = judge.MESSAGES[judge.Reason.NO_EVIDENCE]
            return _finish(run_obj, answered=False, reason=judge.Reason.NO_EVIDENCE,
                           text=prompts.build_defer_message(msg, evidence),
                           evidence=evidence,
                           deferred=(question, msg, evidence, asker))

        _set(run_obj, AgentRun.Status.GENERATING)
        return _finish(run_obj, answered=True, text=answer_text,
                       evidence=verdict.evidence, constraints=verdict.constraints)

    except Exception as exc:                                  # noqa: BLE001
        logger.exception("대리인 실행 실패 run=%s", run_obj.id)
        run_obj.steps = steps
        run_obj.evidence = evidence
        return _fail(run_obj, str(exc))


# ── 상태 기록 ──────────────────────────────────────────────

def _snapshot_of(principal) -> dict:
    """
    실행 시작 시점의 POLICY 를 DB 에서 새로 읽습니다.

    `principal.agent_settings` 로 가면 인스턴스에 캐시된 값이 잡힙니다. 사용자가
    회의 직전에 설정을 바꿨는데 그 전에 불러온 객체를 들고 있으면 **낡은 정책으로
    판정**하게 되고, 스냅샷에도 낡은 값이 박혀 나중에 재현할 때 사실과 어긋납니다.
    """
    from ..models import AgentSettings
    s = AgentSettings.objects.filter(user=principal).first()
    return s.as_snapshot() if s else {}


def _set(run_obj: AgentRun, status: str):
    run_obj.status = status
    run_obj.save(update_fields=["status", "updated_at"])


def _finish(run_obj: AgentRun, *, answered: bool, text: str = "", reason: str = "",
            evidence: list[dict] | None = None,
            constraints: list[str] | None = None,
            deferred: tuple | None = None) -> RunOutcome:
    """
    정상 종료.

    **유보도 COMPLETED 입니다.** FAILED 로 두면 화면에 오류로 뜨고, 사용자는
    대리인이 고장난 줄 압니다. 유보는 제품이 의도한 결과입니다.
    """
    run_obj.status = AgentRun.Status.COMPLETED
    run_obj.result = text
    if evidence is not None:
        run_obj.evidence = evidence
    run_obj.save()

    # 유보는 남겨야 사용자가 돌아왔을 때 볼 수 있습니다. 남기지 않으면 대리인이
    # 침묵한 사실 자체가 사라집니다.
    question_obj = None
    if deferred is not None:
        q, message, ev, asker = deferred
        question_obj = deferral.record(run=run_obj, question=q,
                                       reason_message=message, evidence=ev,
                                       asker=asker)

    return RunOutcome(run=run_obj, answered=answered, text=text, reason=reason,
                      evidence=evidence or [], constraints=constraints or [],
                      pending_question=question_obj)


def _fail(run_obj: AgentRun, error: str) -> RunOutcome:
    """기술적 실패. LLM 오류·타임아웃·예상 못 한 예외."""
    run_obj.status = AgentRun.Status.FAILED
    run_obj.result = ""
    run_obj.save()
    return RunOutcome(run=run_obj, answered=False, error=error[:500])
