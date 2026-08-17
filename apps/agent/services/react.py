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
            # 설정 스냅샷에서 꺼냅니다. 설정 행을 다시 읽지 않는 이유는 실행
            # 도중에 사용자가 바꿔도 **이 실행은 시작할 때의 설정으로 끝나야**
            # 하기 때문입니다 — `active_version` 이 가리키는 것도 그것입니다.
            tone=(snapshot or {}).get("tone", ""),
        )
        ctx = SkillContext(
            principal_id=str(principal.id), actor_id=str(actor_id or principal.id),
            meeting_id=str(meeting.id) if meeting else None,
            project_id=str(project_id or getattr(meeting, "project_id", "") or "") or None,
            run_id=str(run_obj.id), settings_snapshot=snapshot,
            allow_private=allow_private,
        )
        # 읽기와 쓰기를 모두 넘깁니다.
        #
        # 한동안 읽기만 넘겼습니다. 그러면 `propose_task` · `propose_schedule` ·
        # `send_message` · `ask_peer_agent` 를 **모델이 부를 방법이 아예 없습니다** —
        # 카탈로그가 그대로 OpenAI `tools` 로 나가고, 목록에 없는 이름은 부를 수
        # 없기 때문입니다. 회의에서 나온 할 일이 태스크 후보로 한 번도 안 만들어졌고,
        # 시연 시나리오의 "후속 태스크 승인" 은 승인할 것이 없었습니다.
        #
        # 무엇을 제안할지는 **맥락을 아는 모델이 판단할 일**입니다. 안전장치는
        # 카탈로그에서 빼는 것이 아니라 아래 두 가지입니다.
        #
        #   1. 쓰기 실행 전에 POLICY 를 다시 본다 (`_may_write`)
        #   2. AI 산출물은 전부 PENDING_APPROVAL · DRAFT 로 시작한다 (1원칙)
        #
        # `speak_in_meeting` 만 카탈로그에서 뺍니다. 그건 모델이 고를 일이 아니라
        # 루프가 끝난 뒤 코드가 부르는 것입니다(`tasks.py::_speak`). 목록에 두면
        # 모델이 중간에 회의에 끼어들 수 있습니다.
        # 도구 스펙은 프로바이더 중립 형식입니다(`name` 이 최상위). OpenAI
        # 모양으로 바꾸는 것은 `llm.py` 가 합니다.
        catalog = [t for t in registry.build_catalog()
                   if t["name"] != "speak_in_meeting"]

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
                blocked = _may_write(call.name, snapshot)
                if blocked:
                    # 정책이 막은 것은 실패가 아니라 **하지 않기로 한 것**입니다.
                    # 모델에게 사유를 돌려주면 다른 방법을 찾습니다.
                    _step("skill_blocked", name=call.name, reason=blocked)
                    # `tool_message(call, data)` 입니다 — 호출 객체와 dict 를
                    # 받습니다. `call.id` 를 넘기면 안쪽에서 문자열의 `.id` 를
                    # 읽어 터지고, 그 예외가 바깥 핸들러까지 올라가 **실행 전체가
                    # FAILED** 로 끝납니다. 그러면 회의에서 대리인이 아무 말도
                    # 못 하는데, 정책이 막았다는 사실을 알려 주려고 만든 자리가
                    # 오히려 침묵을 만듭니다.
                    messages.append(LLMClient.tool_message(call, {"error": blocked}))
                    continue

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
                # 실패는 사유를 붙여 돌려줍니다.
                #
                # `SkillResult.fail()` 은 `data` 를 채우지 않아, 그냥 넘기면
                # 모델은 **빈 dict 를 받습니다.** 그러면 "찾았는데 없었다" 와
                # "부르다 터졌다" 가 구별되지 않습니다.
                #
                #     propose_schedule → {}      지금. 일정이 잡힌 줄 압니다
                #     propose_schedule → {"error": "start_at 형식이 잘못됐습니다."}
                #
                # 앞의 경우 대리인은 "9월 7일로 잡았습니다" 라고 답합니다.
                # **없는 일을 했다고 말하게 됩니다.** 정책이 막을 때는 사유를
                # 돌려주면서 정작 실패는 안 돌려주고 있었습니다.
                payload = result.data if result.ok else {
                    "error": result.message, "code": result.error_code}
                messages.append(LLMClient.tool_message(call, payload))
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


def _may_write(skill_name: str, snapshot: dict | None) -> str:
    """
    쓰기 스킬을 실행해도 되는지.

    막을 사유가 있으면 **사용자에게 보여줄 한국어**를 돌려주고, 괜찮으면 빈
    문자열을 돌려줍니다. 그 문구는 모델에게도 그대로 전달돼 다른 방법을 찾게
    합니다.

    ## 카탈로그에서 빼지 않고 여기서 막는 이유

    빼 버리면 모델은 그런 도구가 있다는 것조차 모릅니다. 그러면 "일정을 바꾸는
    건 제 권한이 아닙니다" 같은 말도 못 하고 그냥 침묵합니다. 목록에는 두되
    실행 직전에 막는 편이, 사용자에게 **왜 안 했는지**를 남깁니다.

    ## 무엇을 보는가

    `SkillKind` docstring 이 "쓰기 스킬은 실행 전에 POLICY 를 다시 본다" 고
    적어 뒀는데 그 자리가 비어 있었습니다. 여기가 그 자리입니다.

    `propose_task` 는 여기서 안 막습니다. 그건 **후보**를 만들 뿐이고 확정은
    사람이 하므로(1원칙) 승인 화면에서 한 번 더 걸러집니다.

    ## 나가는 것과 쌓이는 것을 가릅니다

        propose_task       PENDING_APPROVAL 로 쌓임 — 사람이 봅니다
        propose_schedule   DRAFT 로 쌓임 — 다만 설정에 스위치가 있습니다
        send_message       **즉시 나갑니다** — 되돌릴 자리가 없습니다
        ask_peer_agent     남에게 이쪽 맥락을 건넵니다

    뒤의 둘은 승인 단계가 없어 여기가 마지막 관문입니다.
    """
    from . import policy

    s = {**policy.DEFAULTS, **(snapshot or {})}

    if skill_name == "propose_schedule" and not s.get("allow_schedule_change", True):
        return ("본인이 일정 수정을 대리인에게 맡기지 않도록 설정해 두었습니다. "
                "일정은 제안하지 않고 본인에게 남깁니다.")

    if not s.get("disclose_work_plan_thought", True):
        if skill_name == "ask_peer_agent":
            # 남에게 물으려면 이쪽 맥락을 얼마간 건네야 합니다. 본인이 자기
            # 기록을 안 알리기로 했다면 그 맥락도 나가면 안 됩니다.
            return ("본인이 작업·계획·생각을 공개하지 않도록 설정해 두었습니다. "
                    "다른 대리인에게 묻지 않습니다.")

        if skill_name == "send_message":
            # `judge.can_disclose()` 는 **최종 답변**만 거릅니다. 검색 스킬이
            # 돌려준 원문은 그 전에 이미 `messages` 에 들어가 있어 모델이 보고
            # 있고, 그대로 메시지 본문에 옮겨 담으면 판정을 거치지 않고 나갑니다.
            # 승인 큐도 안 거치므로 되돌릴 자리가 없습니다.
            #
            # 답을 아예 못 하게 되는 것은 아닙니다 — 회의 발언은 판정을 거쳐
            # 나가고, 개인 메시지 통로만 닫습니다.
            return ("본인이 작업·계획·생각을 공개하지 않도록 설정해 두었습니다. "
                    "개인 메시지로 따로 전하지 않습니다.")

    return ""


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
