"""AI 대리인 설정 · 프롬프트 · 대화."""
import logging

from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.pagination import cursor_page
from apps.common.views import listing
from config.errors import BordoError

from .models import (AgentConversation, AgentMessage, AgentPrompt, AgentSettings,
                     AgentTone,
                     AgentSettingsVersion, PendingQuestion)
from .serializers import (AgentConversationSerializer, AgentPromptSerializer,
                          AgentSettingsSerializer)
from .tasks import run_agent_for_conversation

logger = logging.getLogger("bordo.agent")

BOOL_FIELDS = ("mention_feasibility", "allow_schedule_change",
               "allow_midmeeting_question", "disclose_work_plan_thought")


def get_settings(user):
    obj, _ = AgentSettings.objects.get_or_create(user=user)
    return obj


@api_view(["GET", "PATCH"])
def settings_view(request):
    obj = get_settings(request.user)
    if request.method == "GET":
        return Response(AgentSettingsSerializer(obj).data)

    changed = {}
    for f in BOOL_FIELDS:
        if f in request.data:
            new = bool(request.data[f])
            old = getattr(obj, f)
            if new != old:
                changed[f] = {"from": old, "to": new}
                setattr(obj, f, new)

    if "agent_name" in request.data:
        # 길이만 봅니다. 빈 값은 "기본 호칭으로 되돌린다" 는 뜻이라 허용합니다 —
        # 지운 사람에게 "이름을 넣으십시오" 라고 하면 되돌릴 방법이 없습니다.
        new = str(request.data["agent_name"] or "").strip()
        if len(new) > 40:
            raise BordoError("VALIDATION_ERROR", "대리인 이름은 40자를 넘을 수 없습니다.")
        if new != obj.agent_name:
            changed["agent_name"] = {"from": obj.agent_name, "to": new}
            obj.agent_name = new

    if "tone" in request.data:
        # 없는 값을 넣으면 400 입니다. 조용히 기본값으로 되돌리면 사용자는
        # 골랐다고 생각하는데 대리인은 다른 투로 말합니다.
        new = str(request.data["tone"] or "").upper()
        if new not in AgentTone.values:
            raise BordoError("VALIDATION_ERROR", "고를 수 없는 말투입니다.",
                             details={"tone": sorted(AgentTone.values)})
        if new != obj.tone:
            changed["tone"] = {"from": obj.tone, "to": new}
            obj.tone = new
    if not changed:
        # 바뀐 게 없으면 버전을 올리지 않습니다. 판정 이력이 의미 없이 불어납니다.
        return Response({"settings": AgentSettingsSerializer(obj).data,
                         "previous_version": obj.active_version, "changed": {}})

    with transaction.atomic():
        previous = obj.active_version
        obj.active_version += 1
        obj.save()
        AgentSettingsVersion.objects.create(
            user=request.user, version=obj.active_version, snapshot=obj.as_snapshot())
    return Response({"settings": AgentSettingsSerializer(obj).data,
                     "previous_version": previous, "changed": changed})


@api_view(["GET"])
def settings_history(request):
    rows = AgentSettingsVersion.objects.filter(user=request.user)[:50]
    return Response(listing([{"version": r.version, "snapshot": r.snapshot,
                              "activated_at": r.activated_at} for r in rows]))


@api_view(["GET", "POST"])
def prompts(request):
    if request.method == "GET":
        rows = AgentPrompt.objects.filter(user=request.user)
        return Response(listing(AgentPromptSerializer(rows, many=True).data))
    s = AgentPromptSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    s.save(user=request.user)
    return Response(s.data, status=201)


@api_view(["PATCH", "DELETE"])
def prompt_detail(request, prompt_id):
    obj = AgentPrompt.objects.filter(pk=prompt_id, user=request.user).first()
    if not obj:
        raise BordoError("STATE_NOT_FOUND", "프롬프트를 찾을 수 없습니다.")
    if request.method == "DELETE":
        obj.delete()
        return Response(status=204)
    s = AgentPromptSerializer(obj, data=request.data, partial=True)
    s.is_valid(raise_exception=True)
    s.save()
    return Response(s.data)


@api_view(["GET", "POST"])
def conversations(request):
    if request.method == "GET":
        rows = AgentConversation.objects.filter(user=request.user)[:50]
        return Response(listing(AgentConversationSerializer(rows, many=True).data))
    conv = AgentConversation.objects.create(
        user=request.user, title=request.data.get("title") or "새 대화")
    return Response(AgentConversationSerializer(conv).data, status=201)


@api_view(["PATCH", "DELETE"])
def conversation_detail(request, conversation_id):
    conv = AgentConversation.objects.filter(pk=conversation_id, user=request.user).first()
    if not conv:
        raise BordoError("STATE_NOT_FOUND", "대화를 찾을 수 없습니다.")
    if request.method == "DELETE":
        conv.delete()
        return Response(status=204)
    if "title" in request.data:
        conv.title = request.data["title"]
        conv.title_pinned = True      # 직접 고치면 자동 제목 갱신을 멈춥니다.
        conv.save(update_fields=["title", "title_pinned", "updated_at"])
    return Response(AgentConversationSerializer(conv).data)


@api_view(["GET", "POST"])
def conversation_messages(request, conversation_id):
    conv = AgentConversation.objects.filter(pk=conversation_id, user=request.user).first()
    if not conv:
        raise BordoError("STATE_NOT_FOUND", "대화를 찾을 수 없습니다.")

    if request.method == "GET":
        rows, next_before = cursor_page(
            AgentMessage.objects.filter(conversation=conv),
            before=request.query_params.get("before"),
            limit=request.query_params.get("limit"),
            order_field="-sent_at")
        rows = list(reversed(rows))
        return Response({"results": [{"id": str(m.id), "role": m.role, "body": m.body,
                                      "run_id": str(m.run_id) if m.run_id else None,
                                      "sent_at": m.sent_at} for m in rows],
                         "next_before": next_before})

    body = (request.data.get("body") or "").strip()
    if not body:
        raise BordoError("VALIDATION_ERROR", "body 는 비울 수 없습니다.")
    msg = AgentMessage.objects.create(conversation=conv,
                                      role=AgentMessage.Role.USER, body=body)
    conv.last_message_preview = body[:200]
    if not conv.title_pinned and conv.title == "새 대화":
        # 첫 메시지로 임시 제목을 잡아둡니다. 실제 요약 제목은 워커가 갱신합니다.
        conv.title = body[:40]
    conv.save(update_fields=["last_message_preview", "title", "updated_at"])

    # 대리인을 실제로 부릅니다.
    #
    # 여기가 비어 있었습니다. 메시지는 적히고 202 는 나가는데 **그것을 읽는 쪽이
    # 없어서**, 홈 대화창과 채팅 AI 방이 영영 `답을 준비하고 있습니다` 에
    # 머물렀습니다. 화면은 처음부터 답을 기다리게 만들어져 있었습니다.
    #
    # 저장에 실패하지 않았는데 호출에서 터지면 사용자 메시지만 남고 202 대신
    # 500 이 나갑니다. 화면은 "안 보내졌다" 로 읽고 같은 말을 다시 보내는데,
    # 앞의 것은 이미 저장돼 있어 대화에 같은 말이 두 번 남습니다.
    try:
        run_agent_for_conversation.delay(str(msg.id))
    except Exception:                                          # noqa: BLE001
        logger.exception("대리인 호출 실패 message=%s", msg.id)

    return Response({"id": str(msg.id), "role": msg.role, "body": msg.body,
                     "sent_at": msg.sent_at,
                     "run": {"status": "RECEIVED", "run_id": None}}, status=202)


@api_view(["GET"])
def my_pending_questions(request):
    rows = PendingQuestion.objects.filter(target_user=request.user,
                                          answered_at__isnull=True)[:100]
    return Response(listing([{
        "question_id": str(q.id), "meeting_id": str(q.meeting_id),
        "asker_name": q.asker_name, "title": q.title, "body": q.body,
        "asked_at": q.created_at} for q in rows]))


@api_view(["POST"])
def answer_question(request, question_id):
    from django.utils import timezone
    q = PendingQuestion.objects.filter(pk=question_id, target_user=request.user).first()
    if not q:
        raise BordoError("STATE_NOT_FOUND", "질문을 찾을 수 없습니다.")
    if q.answered_at:
        raise BordoError("DUPLICATE_EVENT", "이미 답변한 질문입니다.")
    q.answer_body = request.data.get("body", "") or ""
    q.answered_at = timezone.now()
    q.save(update_fields=["answer_body", "answered_at", "updated_at"])
    return Response({"question_id": str(q.id), "answered_at": q.answered_at})


@api_view(["GET"])
def agent_lookup_detail(request, lookup_id):
    """
    `AI 조회` 화살표를 눌렀을 때 뜨는 4단 상세.

    조회 이유 → 질문 → 확인된 내용 → 출처·시각.

    프로젝트 참여자만 봅니다. 조회한 쪽·받은 쪽만으로 좁히지 않는 이유는,
    작업 플로우가 **팀 관점 화면**이라 남의 조회 화살표도 눌러 볼 수 있어야
    하기 때문입니다.
    """
    from apps.common.permissions import project_membership

    from .models import AgentLookup

    row = (AgentLookup.objects.filter(pk=lookup_id)
           .select_related("asker", "target").first())
    if row is None:
        raise BordoError("STATE_NOT_FOUND", "조회 기록을 찾을 수 없습니다.")
    project_membership(request.user, row.project_id)

    return Response({
        "id": str(row.id),
        "topic": row.topic,
        "asker": {"user_id": str(row.asker_id), "name": f"{row.asker.name}의 Bordo"},
        "target": {"user_id": str(row.target_id), "name": f"{row.target.name}의 Bordo"},
        "reason": row.reason,
        "question": row.question,
        # 유보하면 빕니다. 화면은 "확인된 내용" 자리를 비워 두고 안내를 띄웁니다.
        "answer": row.answer,
        "answered": bool(row.answer),
        "source": row.source or None,
        "edge_id": str(row.edge_id) if row.edge_id else None,
        "occurred_at": row.occurred_at,
    })


# ═══════════════════════════════════════════ 실행 단계 · 근거

#: `AgentRun.steps` 의 `kind` → 계약의 `StepType`.
#:
#: 우리 단계가 계약보다 잘게 나뉩니다. 화면은 다섯 종류만 알면 되므로 여기서 묶습니다.
_STEP_TYPE = {
    "intent": "ANALYZING",
    "policy": "CHECKING_POLICY",
    "skill": "SEARCHING",
    "skill_blocked": "CHECKING_POLICY",
    "answer_draft": "GENERATING",
    "verdict": "GENERATING",
    "max_steps": "GENERATING",
    "llm_error": "GENERATING",
}

_INTENT_LABEL = {
    "FEASIBILITY": "구현 가능성", "SCHEDULE": "일정", "STATUS": "진행 상황",
    "CLARIFY": "되묻기", "OTHER": "기타",
}


def _step_summary(step: dict) -> str:
    """
    화면에 그대로 찍히는 한 줄.

    클라이언트가 `kind` 로 분기해 문장을 만들면 단계를 하나 추가할 때마다 프론트도
    같이 고쳐야 하고, 안 고치면 **모르는 단계가 화면에서 조용히 사라집니다.**
    서버가 완성해 내려주는 다른 문자열들과 같은 이유입니다.
    """
    kind = step.get("kind")
    name = step.get("name")
    if kind == "intent":
        got = step.get("intent", "")
        return f"질문 의도를 {_INTENT_LABEL.get(got, got)} 로 봤습니다."
    if kind == "policy":
        if step.get("allowed"):
            return "설정을 확인했습니다. 답해도 되는 질문입니다."
        why = step.get("message") or step.get("reason") or ""
        return f"설정이 막았습니다 — {why}".strip(" —")
    if kind == "skill":
        if step.get("ok") is False:
            return f"{name} 을 실행하지 못했습니다."
        found = step.get("found", 0)
        return (f"{name} — {found}건을 찾았습니다." if found
                else f"{name} — 관련 기록이 없었습니다.")
    if kind == "skill_blocked":
        return f"{name} 을 하지 않았습니다 — {step.get('reason', '')}"
    if kind == "answer_draft":
        return "모은 근거로 답을 만들었습니다."
    if kind == "verdict":
        return ("답할 근거가 충분했습니다." if step.get("answer")
                else f"유보했습니다 — {step.get('reason', '')}".strip(" —"))
    if kind == "max_steps":
        return (f"확인 단계 상한({step.get('limit')})에 닿아 멈췄습니다. "
                "모은 근거로 판단합니다.")
    if kind == "llm_error":
        # 모델 오류 문구를 그대로 보여 주지 않습니다. 사용자가 할 수 있는 일이 없고,
        # 영문 오류가 화면에 뜨면 서비스가 통째로 고장 난 것처럼 보입니다.
        return "생성 중 오류가 나 그 단계를 건너뛰었습니다."
    return kind or ""


def _durations(steps):
    """
    단계마다 걸린 시간.

    `at` 은 그 단계가 **끝난** 시각입니다. 앞 단계 끝에서 이번 단계 끝까지가
    이번 단계에 쓴 시간입니다. 첫 단계는 기준이 없어 비웁니다 — 0 으로 채우면
    "아주 빨랐다" 로 읽힙니다.
    """
    from django.utils.dateparse import parse_datetime

    out, prev = [], None
    for s in steps:
        raw = s.get("at")
        at = parse_datetime(raw) if raw else None
        out.append(int((at - prev).total_seconds() * 1000) if at and prev else None)
        prev = at or prev
    return out


def _run_or_404(request, run_id):
    """
    본인 실행만 봅니다.

    `evidence` 에는 본인의 work·plan·thought 원문이 들어 있습니다. 남이 열면
    `작업/계획/생각 공개` 설정을 우회해 읽는 셈이 됩니다.

    남의 것은 **403 이 아니라 404** 입니다. 403 을 주면 "그 실행이 있긴 하다" 가
    새어 나갑니다.
    """
    from .models import AgentRun

    run = AgentRun.objects.filter(pk=run_id, user=request.user).first()
    if run is None:
        raise BordoError("STATE_NOT_FOUND", "실행 기록을 찾을 수 없습니다.")
    return run


def _find(steps, kind):
    for s in steps:
        if s.get("kind") == kind:
            return s
    return None


@api_view(["GET"])
def agent_run_detail(request, run_id):
    """
    실행 하나의 요약.

    **추적성 원칙의 구현체**입니다 — 대리인이 한 말은 질문·근거·설정·단계와
    이어져 있어야 합니다. 이어져 있지 않으면 사용자는 그 말을 믿을 근거가 없습니다.

    ## 계약에 있는데 못 채우는 것

    `confidence` · `policy_version` · `model` · `agent_id` 는 우리가 기록하지
    않습니다. **0 이나 빈 문자열로 채우지 않고 뺍니다** — 없는 값을 그럴듯하게
    채우면 화면이 그것을 사실로 보여 줍니다. 지어내지 않는다는 규칙은 대리인의
    답변만이 아니라 API 에도 적용됩니다.
    """
    run = _run_or_404(request, run_id)
    steps = run.steps or []
    verdict = _find(steps, "verdict")
    policy = _find(steps, "policy")
    done = run.status in ("COMPLETED", "DEFERRED", "FAILED")

    return Response({
        "id": str(run.id),
        "owner_user_id": str(run.user_id),
        "status": run.status,
        "trigger_ref": {"meeting_id": str(run.meeting_id) if run.meeting_id else None},
        "trace_id": str(run.trace_id) if run.trace_id else None,
        "hop_count": run.hop_count,
        "max_hops": run.max_hops,
        "step_count": len(steps),
        "evidence_count": len(run.evidence or []),
        "final_action": ("answer" if verdict and verdict.get("answer")
                         else "defer" if verdict else None),
        "policy_decision": (None if policy is None
                            else "ALLOW" if policy.get("allowed") else "DENY"),
        # 유보로 끝났으면 본인이 답해야 합니다. 화면이 유보 질문 카드를 띄우는
        # 근거가 이 값입니다.
        "requires_owner_follow_up": bool(verdict and not verdict.get("answer")),
        "answer": {"text": run.result},
        # 어떤 설정으로 답했는지. 설정을 바꾼 뒤 옛 답을 다시 볼 때, 지금 설정으로
        # 보여 주면 "왜 이렇게 답했지" 가 설명되지 않습니다.
        "settings_snapshot": run.settings_snapshot,
        "started_at": run.created_at,
        "completed_at": run.updated_at if done else None,
        "duration_ms": (int((run.updated_at - run.created_at).total_seconds() * 1000)
                        if done else None),
    })


@api_view(["GET"])
def agent_run_steps(request, run_id):
    """
    ReAct 실행 단계.

    화면의 `AI 실행 단계 및 근거 표시` 가 이걸로 그려집니다 — 어떤 기록을
    찾아봤고 거기서 무엇을 가져왔는지 사용자에게 보여 주는 자리입니다.

    완료 후 `steps` 는 불변이라 캐시해도 됩니다. 실행 중에는 늘어납니다.
    """
    from apps.agent.services.react import MAX_STEPS

    run = _run_or_404(request, run_id)
    steps = run.steps or []
    spent = _durations(steps)

    return Response({
        "run_id": str(run.id),
        "count": len(steps),
        "max_steps": MAX_STEPS,
        "results": [{
            "step_number": i + 1,
            "type": _STEP_TYPE.get(s.get("kind"), "GENERATING"),
            "status": ("failed" if s.get("kind") == "llm_error" or s.get("ok") is False
                       else "completed"),
            "summary": _step_summary(s),
            "tool": s.get("name"),
            # 원본도 함께 실어 둡니다. 화면은 `summary` 만 쓰면 되지만, 왜 그렇게
            # 답했는지 되짚을 때는 무엇을 어떤 인자로 불렀는지가 필요합니다.
            "output_summary": {k: v for k, v in s.items()
                               if k not in ("kind", "at", "name")},
            "duration_ms": spent[i],
            "at": s.get("at"),
        } for i, s in enumerate(steps)],
    })


@api_view(["GET"])
def agent_run_evidence(request, run_id):
    """
    무엇을 보고 답했는지.

    Discord 발언에는 **근거 개수와 링크만** 나갑니다. 본문은 여기서 봅니다 —
    회의 채널에 작업 기록 원문이 통째로 실리면 안 됩니다.

    비공개 기록은 애초에 여기 없습니다. 검색 단계에서 걸러져 `evidence` 에
    들어오지 않습니다(`search_records._visible`).
    """
    run = _run_or_404(request, run_id)
    rows = run.evidence or []

    return Response({
        "run_id": str(run.id),
        "count": len(rows),
        "results": [{
            "source_type": e.get("source_type"),
            "source_id": e.get("source_id"),
            # 스냅샷입니다. 원본이 지워지거나 제목이 바뀌어도 **그때 본 것**을
            # 보여 줘야 추적이 끊기지 않습니다.
            "title": e.get("title_snapshot"),
            "excerpt": e.get("excerpt"),
            "visibility": e.get("visibility"),
            "status": e.get("status") or None,
            # 점수 대신 라벨입니다. `0.88` 을 보여 주면 사용자는 그게 무엇을 뜻하는지
            # 알 수 없고, 애초에 우리는 그 숫자를 만들지 않습니다.
            "match": e.get("match"),
            "staleness_days": e.get("staleness_days"),
            "is_mine": e.get("owner_is_principal"),
            "updated_at": e.get("updated_at"),
        } for e in rows],
    })
