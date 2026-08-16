"""AI 대리인 설정 · 프롬프트 · 대화."""
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.pagination import cursor_page
from apps.common.views import listing
from config.errors import BordoError

from .models import (AgentConversation, AgentMessage, AgentPrompt, AgentSettings,
                     AgentSettingsVersion, PendingQuestion)
from .serializers import (AgentConversationSerializer, AgentPromptSerializer,
                          AgentSettingsSerializer)

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
