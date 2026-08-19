"""
쓰기 스킬.

대리인이 실제로 **말하고 만드는** 자리입니다. 읽기 스킬과 달리 되돌릴 수 없는
결과를 남기므로, 원칙 하나를 여기서 지킵니다.

## 발언과 산출물을 가릅니다

    발언   send_message · speak_in_meeting
           → 즉시 나갑니다. is_agent=True 로 구분해 사람 발언과 섞이지 않게 합니다.
           → 사람이 돌아와 "대리인이 뭐라고 했나" 를 확인합니다.

    산출물  propose_task · propose_schedule
           → PENDING_APPROVAL / DRAFT 로 만듭니다. 사람이 승인해야 실제가 됩니다.

회의 발언까지 승인 대기로 만들면 대리인이 무의미해집니다 — 사람이 자리에 없어서
대신 참석하는 것이기 때문입니다. 반대로 태스크·일정을 대리인이 확정해 버리면
1원칙(사람 최종 승인)이 무너집니다.

## Discord 로 직접 부르지 않습니다

`speak_in_meeting` 은 `OutboxEvent` 에 행 하나만 남깁니다. 트랜잭션이 롤백돼도
메시지는 이미 나가 있는 상황을 만들지 않기 위해서입니다.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .base import SkillBase, SkillContext, SkillKind, SkillResult

logger = logging.getLogger("bordo.agent")


def _principal(ctx: SkillContext):
    from apps.accounts.models import User
    return User.objects.filter(pk=ctx.principal_id).first()


def _flow_artifact(ctx: SkillContext, kind: str, title: str) -> None:
    """
    후보가 서버에 쌓인 것을 플로우에 남깁니다.

    "AI 가 뭘 만들어 뒀다" 가 화면에 안 보이면, 승인 대기 목록에 항목이 갑자기
    생긴 것처럼 보입니다. 어느 회의에서 나온 것인지가 승인 판단의 근거입니다.
    """
    if not ctx.meeting_id:
        return
    from apps.meetings.models import Meeting

    from ..flow import artifact_proposed

    meeting = Meeting.objects.filter(pk=ctx.meeting_id).first()
    me = _principal(ctx)
    if meeting and me:
        artifact_proposed(meeting, principal=me, kind=kind, title=title)


# ═══════════════════════════════════════════ 발언

class SendMessageSkill(SkillBase):
    """
    팀원에게 메시지를 남깁니다.

    `PEER_AGENT` 방을 씁니다 — 받는 사람 입장에서 "저 사람의 대리인" 과의 대화입니다.
    사람이 직접 보낸 메시지와 같은 방에 섞으면, 나중에 누가 한 말인지 되짚을 수
    없습니다.
    """
    name = "send_message"
    kind = SkillKind.WRITE
    description = (
        "팀원에게 메시지를 남깁니다. 회의에서 답한 내용을 특정 사람에게 따로 "
        "전해야 할 때만 씁니다. 회의 전체에 말하려면 speak_in_meeting 을 쓰십시오."
    )
    parameters = {
        "type": "object",
        "properties": {
            "to_user_id": {"type": "string", "description": "받는 사람 ID"},
            "body": {"type": "string", "description": "보낼 내용"},
        },
        "required": ["to_user_id", "body"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        from apps.accounts.models import User
        from apps.chat.models import ChatMessage, ChatRoom, RoomMember, RoomType
        from apps.chat.services import peer_agent_key, touch

        body = (args.get("body") or "").strip()
        to_id = (args.get("to_user_id") or "").strip()
        if not body:
            return SkillResult.fail("body 는 필수입니다.", "validation")

        target = User.objects.filter(pk=to_id).first() if to_id else None
        if target is None:
            return SkillResult.fail("받는 사람을 찾을 수 없습니다.", "not_found")

        me = _principal(ctx)
        if me is None:
            return SkillResult.fail("대리 대상을 찾을 수 없습니다.", "not_found")

        key = peer_agent_key(target.id, me.id)
        with transaction.atomic():
            room = ChatRoom.all_objects.filter(type=RoomType.PEER_AGENT,
                                               dedupe_key=key).first()
            if room is None:
                room = ChatRoom.objects.create(
                    type=RoomType.PEER_AGENT, dedupe_key=key,
                    agent_owner=me, created_by=me)
            # 방에 두 사람을 넣습니다. 받는 사람만 넣으면 본인 사이드바에서
            # 대리인이 무슨 말을 했는지 볼 수 없습니다.
            for u in (target, me):
                RoomMember.objects.get_or_create(room=room, user=u)

            msg = ChatMessage.objects.create(
                room=room, sender=me, sender_name=me.name,
                is_agent=True, body=body)
            touch(room, msg.sent_at)

        return SkillResult(ok=True, message=f"{target.name} 님에게 전달했습니다.",
                           data={"room_id": str(room.id), "message_id": str(msg.id)})


def _agent_name(owner) -> str:
    """`{이름}의 Bordo`. 주인이 없으면 서비스 이름으로 둡니다."""
    from .. import flow

    return flow.agent_display_name(owner) if owner else "Bordo"


def _record_utterance(meeting, owner, body: str):
    """
    대리인 발언을 회의록에 남깁니다.

    ## 왜 봇이 되돌려 보내지 않고 여기서 만드는가

    봇은 자기가 올린 메시지를 되받지 않습니다(`bot/cogs/general.py` 의 봇 필터).
    걸러 주지 않기로 하면 이번엔 대리인이 자기 말에 다시 답합니다. 그래서
    왕복을 늘리는 대신 만든 쪽에서 남깁니다 — 왕복이 하나 늘면 그 왕복이
    실패할 때 발언이 **조용히** 사라집니다.

    Discord 를 안 쓰는 회의에서도 같은 문제가 있는데, 봇 쪽 해결로는 그것을
    못 덮습니다.

    `participant` 는 대리인이 아니라 **주인**입니다. 회의록에서 "누구를 대신한
    말인가" 가 사라지면 돌아온 사람이 자기 몫을 찾을 수 없습니다.
    """
    from apps.meetings.models import Utterance

    return Utterance.objects.create(
        meeting=meeting,
        participant=owner,
        participant_name=_agent_name(owner)[:100],
        body=body,
        # 비워 두면 안 됩니다. `Utterance.Meta.ordering` 이 `spoken_at` 이라
        # NULL 인 줄이 회의록 맨 앞으로 올라가, 대리인이 회의가 시작하기도 전에
        # 말한 것처럼 보입니다.
        spoken_at=timezone.now(),
        is_agent=True,
    )


class SpeakInMeetingSkill(SkillBase):
    """
    회의에 발언합니다.

    Discord 를 직접 부르지 않고 `OutboxEvent` 에 남깁니다. 봇이 가져가 게시합니다.
    """
    name = "speak_in_meeting"
    kind = SkillKind.WRITE
    description = (
        "회의에 발언합니다. 질문에 대한 답을 회의 참석자 전체에게 전할 때 씁니다."
    )
    parameters = {
        "type": "object",
        "properties": {"body": {"type": "string", "description": "발언 내용"}},
        "required": ["body"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        from apps.agent.models import OutboxEvent
        from apps.meetings.models import Meeting

        body = (args.get("body") or "").strip()
        if not body:
            return SkillResult.fail("body 는 필수입니다.", "validation")
        if not ctx.meeting_id:
            return SkillResult.fail("회의 범위가 없습니다.", "validation")

        meeting = Meeting.objects.filter(pk=ctx.meeting_id).select_related(
            "project").first()
        if meeting is None:
            return SkillResult.fail("회의를 찾을 수 없습니다.", "not_found")

        me = _principal(ctx)

        # 멱등키에 실행 ID 를 씁니다. 같은 실행이 재시도돼도 발언은 한 번만
        # 나갑니다 — 회의에 같은 말이 두 번 뜨면 어지럽습니다.
        key = f"speak:{ctx.run_id}" if ctx.run_id else f"speak:{timezone.now().timestamp()}"

        # 발송함과 회의록을 **한 트랜잭션에** 넣습니다. 둘이 갈라지면 회의에는
        # 말이 나갔는데 요약에는 없거나(요약에서 대리인만 빠집니다), 요약에는
        # 있는데 아무도 못 들은 말이 회의록에 남습니다.
        with transaction.atomic():
            event, created = OutboxEvent.objects.get_or_create(
                team_id=meeting.project.team_id, idempotency_key=key,
                defaults=dict(
                    kind=OutboxEvent.Kind.MESSAGE,
                    channel_id=meeting.discord_channel_id or "",
                    # 봇이 `🤖 **{speaker}**: ` 로 붙입니다. 본인 이름을 넣으면
                    # 자리에 없는 사람이 직접 말한 것처럼 보이고, 불참자가 둘
                    # 이상이면 누구를 대신한 말인지도 구별되지 않습니다.
                    # 호칭은 `agent_display_name()` 한 군데서만 만듭니다 —
                    # 플로우 노드·채팅방 제목·회의록과 같은 표기입니다.
                    payload={"body": body,
                             "speaker": _agent_name(me),
                             "is_agent": True,
                             "meeting_id": str(meeting.id)},
                    run_id=ctx.run_id,
                ),
            )
            if created:
                _record_utterance(meeting, me, body)

        if not created:
            return SkillResult(ok=True, message="이미 발언했습니다.",
                               data={"outbox_id": str(event.id), "duplicate": True})

        return SkillResult(ok=True, message="회의에 전달했습니다.",
                           data={"outbox_id": str(event.id)})


# ═══════════════════════════════════════════ 산출물

class ProposeTaskSkill(SkillBase):
    """
    태스크 **후보** 를 만듭니다.

    `PENDING_APPROVAL` 로 시작합니다. 대리인이 바로 `TODO` 로 넣으면 사람이
    모르는 사이에 할 일이 늘어납니다.
    """
    name = "propose_task"
    kind = SkillKind.WRITE
    description = (
        "회의에서 나온 할 일을 후보로 등록합니다. 확정이 아니라 제안이며, "
        "본인이 승인해야 실제 태스크가 됩니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
        },
        "required": ["title"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        from apps.tasks.models import Task, TaskStatus

        title = (args.get("title") or "").strip()
        if not title:
            return SkillResult.fail("title 은 필수입니다.", "validation")
        if not ctx.project_id:
            return SkillResult.fail("프로젝트 범위가 없습니다.", "validation")

        task = Task.objects.create(
            project_id=ctx.project_id,
            title=title[:200],
            description=(args.get("description") or "").strip(),
            priority=args.get("priority") or "P2",
            # 상태를 인자로 받지 않습니다. 받으면 모델이 TODO 를 넣어 버립니다.
            status=TaskStatus.PENDING_APPROVAL,
            created_by_agent=True,
            created_by_id=ctx.principal_id,
            source_meeting_id=ctx.meeting_id,
        )
        _flow_artifact(ctx, "task", title)
        return SkillResult(ok=True,
                           message="승인 대기 상태로 등록했습니다. 확정은 본인이 합니다.",
                           data={"task_id": str(task.id), "status": task.status})


class ProposeScheduleSkill(SkillBase):
    """
    일정 **후보** 를 만듭니다.

    `DRAFT` 로 시작합니다. POLICY 가 일정 변경을 허용해도 확정은 사람이 합니다.
    """
    name = "propose_schedule"
    kind = SkillKind.WRITE
    description = (
        "회의에서 나온 일정을 초안으로 등록합니다. 확정이 아니라 제안이며, "
        "본인이 확정해야 실제 일정이 됩니다."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start_at": {"type": "string", "description": "ISO 8601"},
            "end_at": {"type": "string", "description": "ISO 8601"},
        },
        "required": ["title", "start_at"],
    }

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        from apps.calendars.models import CalendarEvent, EventStatus
        from apps.common.parsing import parse_dt

        title = (args.get("title") or "").strip()
        if not title:
            return SkillResult.fail("title 은 필수입니다.", "validation")
        if not ctx.project_id:
            return SkillResult.fail("프로젝트 범위가 없습니다.", "validation")

        try:
            start_at = parse_dt(args.get("start_at"), "start_at", required=True)
            end_at = parse_dt(args.get("end_at"), "end_at")
        except Exception as exc:                              # noqa: BLE001
            # 모델이 만든 날짜 문자열은 형식이 어긋나기 쉽습니다. 여기서 터지면
            # 루프가 죽으므로 정상 실패로 되돌려 모델이 고쳐 부르게 합니다.
            return SkillResult.fail(f"날짜 형식이 올바르지 않습니다: {exc}", "validation")

        event = CalendarEvent.objects.create(
            project_id=ctx.project_id, title=title[:200],
            status=EventStatus.DRAFT,
            start_at=start_at, end_at=end_at,
            created_by_id=ctx.principal_id,
        )
        _flow_artifact(ctx, "schedule", title)
        return SkillResult(ok=True,
                           message="초안으로 등록했습니다. 확정은 본인이 합니다.",
                           data={"event_id": str(event.id), "status": event.status})
