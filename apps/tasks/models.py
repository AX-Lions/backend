"""
태스크 — 설계 1원칙(사람 최종 승인)이 코드로 나타나는 자리.

AI 가 만든 것은 예외 없이 `PENDING_APPROVAL` 로 시작합니다.
클라이언트가 `status` 를 보내도 서버가 무시합니다. 자유롭게 쓸 수 있으면
승인 단계를 건너뛰고 바로 `TODO` 로 넣을 수 있고, 그러면 원칙이 무력해집니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel


class TaskStatus(models.TextChoices):
    PENDING_APPROVAL = "PENDING_APPROVAL", "승인 대기"
    TODO = "TODO", "예정"
    IN_PROGRESS = "IN_PROGRESS", "진행 중"
    BLOCKED = "BLOCKED", "막힘"
    COMPLETED = "COMPLETED", "완료"
    REJECTED = "REJECTED", "반려됨"


#: 상태 전이표. 전용 엔드포인트만 이 표를 따라 움직입니다.
#: PATCH 로는 어떤 경로로도 status 를 못 바꿉니다.
TRANSITIONS = {
    "approve":  ({TaskStatus.PENDING_APPROVAL}, TaskStatus.TODO),
    "reject":   ({TaskStatus.PENDING_APPROVAL}, TaskStatus.REJECTED),
    "start":    ({TaskStatus.TODO, TaskStatus.BLOCKED}, TaskStatus.IN_PROGRESS),
    "block":    ({TaskStatus.TODO, TaskStatus.IN_PROGRESS}, TaskStatus.BLOCKED),
    "complete": ({TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED},
                 TaskStatus.COMPLETED),
}


class Task(UUIDModel, TimeStamped, SoftDeletable):
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=TaskStatus.choices,
                              default=TaskStatus.TODO)
    priority = models.CharField(max_length=2, default="P2",
                                choices=[("P0", "P0"), ("P1", "P1"),
                                         ("P2", "P2"), ("P3", "P3")])
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="assigned_tasks")
    due_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, related_name="created_tasks")
    created_by_agent = models.BooleanField(
        default=False, help_text="true 면 서버가 PENDING_APPROVAL 을 강제합니다.")
    source_meeting = models.ForeignKey("meetings.Meeting", on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name="tasks")

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="approved_tasks")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(
        blank=True, default="",
        help_text="다음 회의 보고서를 만들 때 컨텍스트로 참조됩니다.")
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "task"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "status"]),
                   models.Index(fields=["assignee", "status"]),
                   models.Index(fields=["source_meeting"])]

    def __str__(self):
        return f"[{self.status}] {self.title}"

    def can(self, action):
        allowed, _ = TRANSITIONS[action]
        return self.status in allowed


class TaskEvent(UUIDModel):
    """
    상태가 바뀐 이력.

    **승인된 태스크는 회의를 지워도 남습니다.** 이미 사람이 하고 있는 일이라
    누가 언제 승인했는지가 사라지면 안 됩니다.
    """
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, related_name="task_events")
    action = models.CharField(max_length=20)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_event"
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["task", "-occurred_at"])]
