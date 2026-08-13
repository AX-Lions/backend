"""
프로젝트 일정.

**팀 일정만 다룹니다.** 개인 일정은 조정 대상이 아니며 AI 가 건드리지 않습니다.

Outbox 를 여기 두는 이유 — Discord 발송은 A 담당이지만, 발송 **상태를 사용자가
보는 것**은 캘린더 화면의 일입니다. 공지가 실패했는데 화면에 아무 표시가 없으면
사용자는 공지가 나간 줄 알고 회의에 안 옵니다. A 가 워커를 붙이면 같은 테이블을
씁니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel


class EventKind(models.TextChoices):
    MEETING = "MEETING", "회의"
    DEADLINE = "DEADLINE", "마감"
    BLOCK = "BLOCK", "일정 블록"


class EventStatus(models.TextChoices):
    DRAFT = "DRAFT", "초안"
    SCHEDULED = "SCHEDULED", "예정"
    CONFIRMED = "CONFIRMED", "확정"
    CANCELLED = "CANCELLED", "취소됨"


class OutboxStatus(models.TextChoices):
    PENDING = "PENDING", "대기"
    CLAIMED = "CLAIMED", "가져감"
    SENT = "SENT", "전송됨"
    ACKED = "ACKED", "확인됨"
    RETRY_WAIT = "RETRY_WAIT", "재시도 대기"
    DEAD = "DEAD", "실패 확정"


class CalendarEvent(UUIDModel, TimeStamped, SoftDeletable):
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="calendar_events")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=EventKind.choices,
                            default=EventKind.MEETING)
    status = models.CharField(max_length=10, choices=EventStatus.choices,
                              default=EventStatus.SCHEDULED)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)

    related_meeting = models.OneToOneField(
        "meetings.Meeting", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="calendar_event")
    discord_notified = models.BooleanField(default=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, related_name="created_events")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="confirmed_events")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "calendar_event"
        ordering = ["start_at"]
        indexes = [models.Index(fields=["project", "start_at"]),
                   models.Index(fields=["project", "status"])]

    def __str__(self):
        return f"{self.title} ({self.start_at:%m/%d %H:%M})"

    @property
    def effective_end(self):
        return self.end_at or self.start_at


class EventParticipant(TimeStamped):
    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE,
                              related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="calendar_participations")

    class Meta:
        db_table = "calendar_event_participant"
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="uq_event_participant"),
        ]


class Reminder(UUIDModel):
    """
    확정 시 예약되는 알림.

    `event_id + notification_type` 이 멱등 키라 확정을 두 번 눌러도
    리마인더가 두 벌 안 생깁니다.
    """
    class Type(models.TextChoices):
        T_MINUS_1D = "T_MINUS_1D", "하루 전"
        T_MINUS_15M = "T_MINUS_15M", "15분 전"

    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE,
                              related_name="reminders")
    notification_type = models.CharField(max_length=20, choices=Type.choices)
    scheduled_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "calendar_reminder"
        constraints = [
            models.UniqueConstraint(fields=["event", "notification_type"],
                                    name="uq_reminder_idempotency"),
        ]
        indexes = [models.Index(fields=["scheduled_at", "cancelled_at"])]

    @property
    def idempotency_key(self):
        return f"{self.event_id}:{self.notification_type}"


class OutboxEvent(UUIDModel, TimeStamped):
    """
    Discord 발송함.

    서버는 요청 트랜잭션 안에서 Discord 를 직접 부르지 않습니다. 여기에 행 하나만
    남기고, 실제 게시는 봇이 가져가 합니다. 외부 호출이 트랜잭션 안에 들어가면
    롤백돼도 메시지는 이미 나가 있습니다.

    A 담당이 워커를 붙이기 전까지는 `PENDING` 으로 쌓이기만 하며,
    사용자는 여기 상태를 보고 실패를 알아챕니다.
    """
    class Type(models.TextChoices):
        AGENT_UTTERANCE = "agent_utterance", "대리인 발언"
        MEETING_ANNOUNCEMENT = "meeting_announcement", "회의 공지"
        MEETING_REMINDER = "meeting_reminder", "회의 리마인더"
        PENDING_QUESTION = "pending_question", "정보 질문"
        MEETING_REPORT = "meeting_report", "회의 보고서"
        SCHEDULE_CHANGED = "schedule_changed", "일정 변경"

    team = models.ForeignKey("orgs.Team", on_delete=models.CASCADE,
                             related_name="outbox_events")
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                null=True, blank=True, related_name="outbox_events")
    type = models.CharField(max_length=24, choices=Type.choices)
    status = models.CharField(max_length=12, choices=OutboxStatus.choices,
                              default=OutboxStatus.PENDING)
    target = models.JSONField(default=dict, blank=True,
                              help_text='{"kind":"channel","channel_id":"..."}')
    payload = models.JSONField(default=dict, blank=True)

    idempotency_key = models.CharField(max_length=200)
    retry_count = models.PositiveSmallIntegerField(default=0)
    max_retries = models.PositiveSmallIntegerField(default=5)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=60, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    claimed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    discord_message_id = models.CharField(max_length=40, blank=True, default="")

    #: 이 발송이 어느 화면 항목에 매달렸는지. 상태를 되돌려 보여줄 때 씁니다.
    source_type = models.CharField(max_length=30, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "outbox_event"
        ordering = ["created_at"]
        constraints = [
            # 같은 키가 다시 오면 기존 결과를 재사용합니다 — 중복 공지 방지.
            models.UniqueConstraint(fields=["team", "idempotency_key"],
                                    name="uq_outbox_idempotency"),
        ]
        indexes = [models.Index(fields=["status", "next_retry_at"]),
                   models.Index(fields=["source_type", "source_id"])]

    def __str__(self):
        return f"[{self.status}] {self.type}"

    @property
    def is_failed(self):
        return self.status in (OutboxStatus.RETRY_WAIT, OutboxStatus.DEAD)
