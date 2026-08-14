"""
프로젝트 일정.

**팀 일정만 다룹니다.** 개인 일정은 조정 대상이 아니며 AI 가 건드리지 않습니다.

Discord 공지는 **여기서 보내지 않습니다.** 발송함(Outbox)과 봇 연동은 A 담당이라
이 앱은 `discord_notified` 표시만 들고, 실제 발송 요청은 `publish()` 로 흘립니다.
A 가 발송함을 붙이면 그 이벤트를 받아 큐에 넣습니다.
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
