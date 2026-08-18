"""
현재 상태 — `work`(지금) · `plan`(앞으로) · `thought`(미확정).

대리인이 "저 사람 지금 뭐 하고 있나"에 답할 때 읽는 재료입니다.
셋 다 휘발성 데이터라 이력을 쌓지 않고 하드 삭제합니다. 다만 이걸 근거로
이미 답한 Agent Run 의 Evidence 스냅샷은 남습니다 — 무엇을 보고 답했는지가
사라지면 추적이 끊깁니다.

`visibility` 를 셋 모두에 둡니다. 계약에는 문서에만 있지만, 대리인 설정의
`작업/계획/생각 공개` 스위치가 이 값과 맞물려야 "공개는 껐는데 검색에는
잡히는" 구멍이 안 생깁니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStamped, UUIDModel


class WorkStatus(models.TextChoices):
    TODO = "TODO", "예정"
    IN_PROGRESS = "IN_PROGRESS", "진행 중"
    BLOCKED = "BLOCKED", "막힘"
    DONE = "DONE", "완료"


class Priority(models.TextChoices):
    P0 = "P0", "P0"
    P1 = "P1", "P1"
    P2 = "P2", "P2"
    P3 = "P3", "P3"


class Visibility(models.TextChoices):
    TEAM = "team", "팀 공개"
    PRIVATE = "private", "비공개"


class Source(models.TextChoices):
    """
    누가 적었나.

    화면이 「개인 AI가 기록함」 뱃지를 달려면 **항목 자체에** 출처가 있어야 합니다.
    `ActivityEvent.detail` 에만 넣으면 활동 로그에서만 보이고 플로우·현재 상태
    카드에서는 안 보입니다.
    """
    WEB = "web", "웹"
    MCP = "mcp", "개인 AI"
    AGENT = "agent", "대리인"


class StateBase(UUIDModel, TimeStamped):
    """세 모델의 공통 뼈대."""
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="%(class)s_items")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="%(class)s_items")
    category = models.CharField(max_length=40, blank=True, default="")
    visibility = models.CharField(max_length=10, choices=Visibility.choices,
                                  default=Visibility.TEAM)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.WEB)

    class Meta:
        abstract = True


class WorkItem(StateBase):
    """`지금 하고 있는 것`. 진행률과 막힌 이유가 대리인 답변의 근거가 됩니다."""
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=WorkStatus.choices,
                              default=WorkStatus.IN_PROGRESS)
    progress = models.PositiveSmallIntegerField(default=0)
    blockers = models.JSONField(default=list, blank=True,
                                help_text="막힌 이유. `왜 안 되고 있나` 의 답입니다.")
    expected_end_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "work_item"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["project", "owner"]),
                   models.Index(fields=["project", "status"])]

    def __str__(self):
        return self.title


class PlanItem(StateBase):
    """`앞으로 할 것`. `dependencies` 로 선후 관계를 표현합니다."""
    title = models.CharField(max_length=200)
    priority = models.CharField(max_length=2, choices=Priority.choices,
                                default=Priority.P2)
    planned_start_at = models.DateTimeField(null=True, blank=True)
    planned_end_at = models.DateTimeField(null=True, blank=True)
    dependencies = models.JSONField(default=list, blank=True,
                                    help_text="선행 plan_item id 배열. 순환은 막습니다.")
    status = models.CharField(max_length=12, choices=WorkStatus.choices,
                              default=WorkStatus.TODO)

    class Meta:
        db_table = "plan_item"
        ordering = ["priority", "planned_end_at"]
        indexes = [models.Index(fields=["project", "owner"]),
                   models.Index(fields=["project", "priority"])]

    def __str__(self):
        return self.title


class ThoughtItem(StateBase):
    """
    `미확정` 판단 · 우려.

    **대리인은 thought 를 확정 사실로 답하지 않습니다.** `confidence` 가 낮으면
    인용하지 않거나 유보합니다. `requires_discussion` 이면 회의 안건 후보입니다.
    """
    class Status(models.TextChoices):
        OPEN = "OPEN", "미해결"
        RESOLVED = "RESOLVED", "해결됨"
        PROMOTED = "PROMOTED", "안건으로 승격"

    topic = models.CharField(max_length=200)
    content = models.TextField()
    confidence = models.FloatField(default=0.5)
    requires_discussion = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    promoted_from_question = models.ForeignKey(
        "agent.PendingQuestion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="promoted_thoughts",
        help_text="정보 질문에 답하며 `promote_to_thought` 로 승격된 경우")

    class Meta:
        db_table = "thought_item"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "owner"]),
                   models.Index(fields=["project", "requires_discussion"])]

    def __str__(self):
        return self.topic


class ActivityEvent(UUIDModel):
    """
    상태가 바뀐 흔적.

    진행률을 언제 얼마나 올렸는지가 남아야 `이 사람 이번 주에 뭐 했나` 를
    되짚을 수 있습니다. 상태 자체는 덮어써도 이 로그는 쌓입니다.
    """
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="activity_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, related_name="activity_events")
    kind = models.CharField(max_length=40, help_text="work.updated 등")
    target_id = models.UUIDField()
    detail = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "activity_event"
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["project", "-occurred_at"]),
                   models.Index(fields=["target_id"])]
