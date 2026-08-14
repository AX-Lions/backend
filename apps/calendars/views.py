"""
프로젝트 일정.

계약보다 늘어난 곳이 하나 있습니다. **일정 확정이 연결 회의 상태를 함께 올립니다** —
계약에서는 둘이 따로 놀아 `일정은 확정인데 회의는 예정` 같은, 사용자가 해석할 수
없는 상태가 만들어집니다.

**Discord 발송함은 여기 없습니다.** 발송 큐·ACK·재시도는 A 담당이라,
이 앱은 공지 요청을 `publish()` 로 흘리고 `discord_notified` 표시만 듭니다.
"""
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.common.events import publish
from apps.common.parsing import parse_dt
from apps.common.permissions import project_membership
from apps.common.views import listing
from apps.orgs.models import ProjectMember, TeamRole
from config.errors import BordoError

from .models import (CalendarEvent, EventKind, EventParticipant, EventStatus,
                     Reminder)
from .serializers import EventSerializer

ADMINS = (TeamRole.OWNER, TeamRole.ADMIN)
REMINDER_OFFSETS = {Reminder.Type.T_MINUS_1D: timedelta(days=1),
                    Reminder.Type.T_MINUS_15M: timedelta(minutes=15)}


def _ctx(events):
    """참여자와 발송 상태를 한 번에 모아 N+1 을 막습니다."""
    ids = [e.id for e in events]
    pmap = {}
    for p in EventParticipant.objects.filter(event_id__in=ids).select_related("user"):
        pmap.setdefault(p.event_id, []).append(p)

    return {"participant_map": pmap}


def _load(user, event_id):
    event = (CalendarEvent.objects.filter(pk=event_id)
             .select_related("project", "project__team", "related_meeting").first())
    if not event:
        raise BordoError("STATE_NOT_FOUND", "일정을 찾을 수 없습니다.")
    _, member = project_membership(user, event.project_id)
    return event, member


def _may_manage(event, member, user):
    return event.created_by_id == user.id or member.team_role in ADMINS


# ─────────────────────────────────────────── 목록 · 생성
@api_view(["GET", "POST"])
def events(request, project_id):
    project, member = project_membership(request.user, project_id)

    if request.method == "GET":
        qs = CalendarEvent.objects.filter(project=project)
        tz = timezone.get_current_timezone()
        frm, to = request.query_params.get("from"), request.query_params.get("to")
        try:
            if frm:
                qs = qs.filter(start_at__gte=timezone.make_aware(
                    datetime.combine(datetime.strptime(frm, "%Y-%m-%d").date(),
                                     time.min), tz))
            if to:
                qs = qs.filter(start_at__lt=timezone.make_aware(
                    datetime.combine(datetime.strptime(to, "%Y-%m-%d").date()
                                     + timedelta(days=1), time.min), tz))
        except ValueError:
            raise BordoError("VALIDATION_ERROR", "from · to 는 YYYY-MM-DD 입니다.")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])

        rows = list(qs[:300])
        return Response(listing(
            EventSerializer(rows, many=True, context=_ctx(rows)).data,
            extra={"range": {"from": frm, "to": to}}))

    title = (request.data.get("title") or "").strip()
    if not title:
        raise BordoError("VALIDATION_ERROR", "title 은 필수입니다.")
    start_at = parse_dt(request.data.get("start_at"), "start_at", required=True)
    end_at = parse_dt(request.data.get("end_at"), "end_at")
    if end_at and end_at < start_at:
        raise BordoError("VALIDATION_ERROR", "end_at 이 start_at 보다 앞섭니다.",
                         details={"start_at": start_at, "end_at": end_at})

    participant_ids = request.data.get("participant_ids") or []
    if participant_ids:
        valid = set(map(str, ProjectMember.objects
                        .filter(project=project, user_id__in=participant_ids)
                        .values_list("user_id", flat=True)))
        outsiders = [str(u) for u in participant_ids if str(u) not in valid]
        if outsiders:
            raise BordoError("PROJECT_ACCESS_DENIED",
                             "프로젝트 참여자만 일정에 넣을 수 있습니다.",
                             details={"not_in_project": outsiders})
    else:
        valid = set(map(str, ProjectMember.objects.filter(project=project)
                        .values_list("user_id", flat=True)))

    with transaction.atomic():
        event = CalendarEvent.objects.create(
            project=project, title=title, start_at=start_at, end_at=end_at,
            kind=request.data.get("kind") or EventKind.MEETING,
            related_meeting_id=request.data.get("related_meeting") or None,
            created_by=request.user)
        EventParticipant.objects.bulk_create(
            [EventParticipant(event=event, user_id=u) for u in valid],
            ignore_conflicts=True)

    if request.data.get("notify_discord"):
        _request_announcement(event)

    body = EventSerializer(event, context=_ctx([event])).data
    body["conflicts"] = _conflicts(event, valid)
    publish(project.id, "calendar.event.created", {"event_id": str(event.id)})
    return Response(body, status=201)


def _conflicts(event, user_ids):
    """
    겹치는 일정을 알려줍니다. 막지는 않습니다 — 겹칠 줄 알면서 잡는 경우가 있습니다.
    """
    end = event.effective_end
    rows = (CalendarEvent.objects
            .filter(participants__user_id__in=list(user_ids),
                    status__in=(EventStatus.SCHEDULED, EventStatus.CONFIRMED))
            .exclude(pk=event.pk)
            .filter(Q(start_at__lt=end) &
                    (Q(end_at__gt=event.start_at) |
                     Q(end_at__isnull=True, start_at__gte=event.start_at)))
            .distinct()[:20])
    return [{"event_id": str(r.id), "title": r.title, "start_at": r.start_at}
            for r in rows]


@api_view(["GET", "PATCH", "DELETE"])
def event_detail(request, event_id):
    event, member = _load(request.user, event_id)

    if request.method == "GET":
        return Response(EventSerializer(event, context=_ctx([event])).data)

    if not _may_manage(event, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "만든 사람 또는 OWNER · ADMIN 만 가능합니다.")

    if request.method == "PATCH":
        if event.status == EventStatus.CANCELLED:
            raise BordoError("APPROVAL_REQUIRED", "취소된 일정은 고칠 수 없습니다.",
                             status=409)
        moved = False
        if "title" in request.data:
            event.title = request.data["title"]
        for f in ("start_at", "end_at"):
            if f in request.data:
                new = parse_dt(request.data[f], f)
                if new != getattr(event, f):
                    moved = True
                setattr(event, f, new)
        if event.end_at and event.end_at < event.start_at:
            raise BordoError("VALIDATION_ERROR", "end_at 이 start_at 보다 앞섭니다.")
        event.save()

        if moved:
            # 시각이 바뀌면 예약된 리마인더가 옛 시각을 가리킵니다. 다시 겁니다.
            _reschedule_reminders(event)
            publish(event.project_id, "calendar.event.moved",
                    {"event_id": str(event.id), "start_at": event.start_at.isoformat()})
        return Response(EventSerializer(event, context=_ctx([event])).data)

    # ── DELETE — 취소를 먼저 거치게 합니다
    if event.discord_notified and event.status != EventStatus.CANCELLED:
        raise BordoError(
            "APPROVAL_REQUIRED",
            "이미 공지가 나간 일정입니다. 먼저 취소해 참석자에게 알리십시오. "
            "그냥 지우면 공지만 보고 참석하러 오는 사람이 생깁니다.",
            details={"cancel": f"/api/v1/calendar/events/{event.id}/cancel"}, status=409)
    if event.related_meeting_id:
        from apps.meetings.models import MeetingStatus
        if event.related_meeting.status == MeetingStatus.ACTIVE:
            raise BordoError("MEETING_NOT_ACTIVE",
                             "진행 중인 회의가 연결돼 있어 지울 수 없습니다.", status=409)
    deleted_at = event.soft_delete()
    Reminder.objects.filter(event=event, cancelled_at__isnull=True).update(
        cancelled_at=timezone.now())
    publish(event.project_id, "calendar.event.deleted", {"event_id": str(event.id)})
    return Response({"id": str(event.id), "deleted_at": deleted_at,
                     "restorable_until": None})


# ─────────────────────────────────────────── 확정 · 취소
@api_view(["POST"])
def confirm(request, event_id):
    """
    후보 일정을 사람이 확정합니다.

    **연결된 회의 상태도 같이 올립니다.** 따로 두면 `일정은 확정인데 회의는 예정`
    이라는, 사용자가 해석할 수 없는 상태가 만들어집니다.
    """
    event, member = _load(request.user, event_id)
    if event.status == EventStatus.CANCELLED:
        raise BordoError("APPROVAL_REQUIRED", "취소된 일정은 확정할 수 없습니다.", status=409)

    previous = event.status
    with transaction.atomic():
        event.status = EventStatus.CONFIRMED
        event.confirmed_by, event.confirmed_at = request.user, timezone.now()
        event.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])
        reminders = _reschedule_reminders(event)

        meeting_status = None
        if event.related_meeting_id:
            from apps.meetings.models import Meeting, MeetingStatus
            meeting = event.related_meeting
            if meeting.status in (MeetingStatus.DRAFT, MeetingStatus.SCHEDULED):
                Meeting.objects.filter(pk=meeting.pk).update(
                    status=MeetingStatus.CONFIRMED, scheduled_at=event.start_at)
                meeting_status = MeetingStatus.CONFIRMED
            else:
                meeting_status = meeting.status

    publish(event.project_id, "calendar.event.confirmed", {"event_id": str(event.id)})
    return Response({
        "id": str(event.id), "status": event.status, "previous_status": previous,
        "confirmed_by": str(request.user.id), "confirmed_at": event.confirmed_at,
        "meeting": ({"id": str(event.related_meeting_id), "status": meeting_status}
                    if event.related_meeting_id else None),
        "reminders": [{"notification_type": r.notification_type,
                       "scheduled_at": r.scheduled_at,
                       "idempotency_key": r.idempotency_key} for r in reminders],
    })


def _reschedule_reminders(event):
    """
    리마인더 재예약.

    `event_id + notification_type` 이 유니크라 두 번 확정해도 두 벌이 안 생깁니다.
    이미 지난 시각은 걸지 않습니다 — 즉시 발사되는 알림은 소음입니다.
    """
    now = timezone.now()
    out = []
    for kind, offset in REMINDER_OFFSETS.items():
        at = event.start_at - offset
        if at <= now:
            Reminder.objects.filter(event=event, notification_type=kind).update(
                cancelled_at=now)
            continue
        r, created = Reminder.objects.get_or_create(
            event=event, notification_type=kind,
            defaults={"scheduled_at": at})
        if not created:
            r.scheduled_at, r.cancelled_at = at, None
            r.save(update_fields=["scheduled_at", "cancelled_at"])
        out.append(r)
    return out


@api_view(["POST"])
def cancel(request, event_id):
    """
    취소는 삭제와 다릅니다.

    `CANCELLED` 로 남겨 참석자에게 `이 일정은 없어졌다` 를 알립니다.
    목록에서 통째로 사라지면 이미 공지를 본 사람이 헛걸음합니다.
    """
    event, member = _load(request.user, event_id)
    if not _may_manage(event, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "만든 사람 또는 OWNER · ADMIN 만 가능합니다.")
    reason = (request.data.get("reason") or "").strip()
    if not reason:
        raise BordoError("VALIDATION_ERROR", "reason 은 필수입니다.")
    if event.status == EventStatus.CANCELLED:
        raise BordoError("DUPLICATE_EVENT", "이미 취소된 일정입니다.")

    with transaction.atomic():
        event.status = EventStatus.CANCELLED
        event.cancelled_reason, event.cancelled_at = reason, timezone.now()
        event.save(update_fields=["status", "cancelled_reason", "cancelled_at",
                                  "updated_at"])
        cancelled = Reminder.objects.filter(
            event=event, cancelled_at__isnull=True).update(cancelled_at=timezone.now())

        notify = bool(request.data.get("notify_participants", True)) and event.discord_notified

    people = list(EventParticipant.objects.filter(event=event)
                  .values_list("user_id", flat=True))
    from apps.tasks.models import Task, TaskStatus
    affected = list(Task.objects
                    .filter(source_meeting_id=event.related_meeting_id)
                    .exclude(status__in=(TaskStatus.COMPLETED, TaskStatus.REJECTED))
                    .values_list("id", flat=True)) if event.related_meeting_id else []

    publish(event.project_id, "calendar.event.cancelled",
            {"event_id": str(event.id), "reason": reason, "announce": notify})
    return Response({
        "id": str(event.id), "status": event.status, "reason": reason,
        "impact": {"participants_notified": [str(u) for u in people],
                   "linked_meeting": (str(event.related_meeting_id)
                                      if event.related_meeting_id else None),
                   "cancelled_reminders": cancelled,
                   "affected_tasks": [str(t) for t in affected]},
        "discord": ({"action": "CANCEL_ANNOUNCEMENT"} if notify else None),
        "cancelled_at": event.cancelled_at,
    })


# ─────────────────────────────────────────── Discord 공지 요청
def _request_announcement(event, channel="announcement"):
    """
    공지를 **요청**만 합니다. 실제 발송은 A(Discord) 담당입니다.

    여기서 Discord 를 직접 부르지 않는 건 설계 2원칙입니다 — 요청 트랜잭션 안에서
    외부를 부르면 롤백돼도 메시지는 이미 나가 있습니다.
    """
    if not event.discord_notified:
        event.discord_notified = True
        event.save(update_fields=["discord_notified", "updated_at"])
    publish(event.project_id, "calendar.discord.announcement_requested", {
        "event_id": str(event.id), "title": event.title,
        "start_at": event.start_at.isoformat(), "channel": channel,
        # A 가 발송함에 넣을 때 쓸 멱등 키. 같은 일정을 두 번 공지하면
        # 참석자는 회의가 두 개인 줄 압니다.
        "idempotency_key": f"{event.project.team_id}:{event.id}:announcement",
    })


@api_view(["POST"])
def notify_discord(request, event_id):
    """
    공지 요청.

    `202` 는 **요청이 접수됐다**는 뜻이지 게시됐다는 뜻이 아닙니다.
    발송 상태 조회·재시도는 A 가 발송함을 붙이면서 함께 냅니다.
    """
    event, member = _load(request.user, event_id)
    if not _may_manage(event, member, request.user):
        raise BordoError("TEAM_ACCESS_DENIED", "만든 사람 또는 OWNER · ADMIN 만 가능합니다.")
    if event.status == EventStatus.CANCELLED:
        raise BordoError("APPROVAL_REQUIRED", "취소된 일정은 공지할 수 없습니다.", status=409)

    _request_announcement(event, request.data.get("channel") or "announcement")
    return Response({"event_id": str(event.id), "requested": True,
                     "channel": request.data.get("channel") or "announcement"},
                    status=202)
