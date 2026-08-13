"""
회의와 플로우 그래프.

플로우는 이 서비스의 차별점이라 JSONB 한 덩어리로 넣지 않았습니다.
참여자·내용 종류·시간으로 필터링해야 하는데, JSON 안에 넣으면
그 필터가 전부 애플리케이션 메모리 연산이 됩니다.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel, Versioned


class MeetingStatus(models.TextChoices):
    DRAFT = "DRAFT", "초안"
    SCHEDULED = "SCHEDULED", "예정"
    CONFIRMED = "CONFIRMED", "확정"
    ACTIVE = "ACTIVE", "진행 중"
    PROCESSING_REPORT = "PROCESSING_REPORT", "보고서 생성 중"
    ENDED = "ENDED", "종료"


class FlowCategory(models.TextChoices):
    WORK = "WORK", "작업"
    MEETING = "MEETING", "회의"


class FlowContentType(models.TextChoices):
    DOCUMENT = "DOCUMENT", "문서"
    PLAN = "PLAN", "계획"
    OPINION = "OPINION", "의견"
    REQUEST = "REQUEST", "요청사항"
    REVISION = "REVISION", "수정사항"


class Surface(models.TextChoices):
    SERVICE = "SERVICE", "서비스"
    DISCORD = "DISCORD", "Discord"


class Meeting(UUIDModel, TimeStamped, SoftDeletable, Versioned):
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="meetings")
    project_name = models.CharField(max_length=120)        # 비정규화 (홈 카드)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=MeetingStatus.choices,
                              default=MeetingStatus.SCHEDULED)
    scheduled_at = models.DateTimeField()
    duration_min = models.PositiveSmallIntegerField(default=60)

    discord_channel_id = models.CharField(max_length=40, blank=True, default="",
                                          help_text="회의가 실제로 열리는 곳")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True,
                                    help_text="화살표 opacity 계산의 기준 시각")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="created_meetings")

    class Meta:
        db_table = "meeting"
        indexes = [models.Index(fields=["project", "-scheduled_at"]),
                   models.Index(fields=["-scheduled_at"])]
        ordering = ["-scheduled_at"]

    def __str__(self):
        return f"{self.title} ({self.scheduled_at:%m/%d})"

    @property
    def is_locked(self):
        """진행 중이거나 끝난 회의는 제목·시각을 못 바꿉니다."""
        return self.status in (MeetingStatus.ACTIVE,
                               MeetingStatus.PROCESSING_REPORT,
                               MeetingStatus.ENDED)

    def save(self, *args, **kwargs):
        if self.project_id and not self.project_name:
            self.project_name = self.project.name
        super().save(*args, **kwargs)


class Attendance(models.TextChoices):
    PENDING = "PENDING", "미정"
    PRESENT = "PRESENT", "참석"
    ABSENT = "ABSENT", "불참"
    DELEGATED = "DELEGATED", "대리 참석"


class MeetingParticipant(TimeStamped):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE,
                                related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="meeting_participations")
    user_name = models.CharField(max_length=100)           # 비정규화
    attendance = models.CharField(max_length=10, choices=Attendance.choices,
                                  default=Attendance.PENDING)
    delegated = models.BooleanField(default=False, help_text="대리 참석 활성화 여부")
    delegate_prompt = models.TextField(
        blank=True, default="", help_text="이 회의에 한정된 지시. 비면 기본 프롬프트를 씁니다.")

    class Meta:
        db_table = "meeting_participant"
        constraints = [
            models.UniqueConstraint(fields=["meeting", "user"], name="uq_meeting_participant"),
        ]
        indexes = [models.Index(fields=["user", "attendance"])]

    @property
    def missed(self):
        """홈 카드의 `불참한 회의` 뱃지 판정."""
        return self.attendance in (Attendance.ABSENT, Attendance.DELEGATED)


class Agenda(UUIDModel, TimeStamped):
    class Status(models.TextChoices):
        PENDING = "PENDING", "대기"
        DISCUSSED = "DISCUSSED", "논의됨"
        DEFERRED = "DEFERRED", "보류"

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="agendas")
    title = models.CharField(max_length=200)
    sort_order = models.PositiveSmallIntegerField(default=0)
    category = models.CharField(max_length=40, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    content = models.TextField(blank=True, default="")
    direction_label = models.CharField(
        max_length=200, blank=True, default="",
        help_text="`AI 대리인 → A, B` 처럼 서버가 조립해 둡니다.")
    created_by_agent = models.BooleanField(default=False)

    class Meta:
        db_table = "agenda"
        ordering = ["sort_order", "created_at"]
        indexes = [models.Index(fields=["meeting", "sort_order"])]


class Utterance(UUIDModel):
    """`회의 맥락` / `전달 맥락` 의 한 줄."""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="utterances")
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True)
    participant_name = models.CharField(max_length=100)    # 비정규화
    body = models.TextField()
    spoken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "utterance"
        ordering = ["spoken_at", "id"]
        indexes = [models.Index(fields=["meeting", "spoken_at"])]


class FlowEdge(UUIDModel, TimeStamped):
    """
    플로우의 화살표 하나.

    노드 이름과 방향 표기를 행 안에 넣어둡니다 — 그래프를 그릴 때
    사용자 테이블을 매번 조인하지 않기 위해서입니다.
    서비스 대리인과 Discord 대리인은 **같은 노드**로 취급하되(`kind=AGENT`),
    엣지에는 `surface` 로 출처를 남깁니다.
    """
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="flow_edges")
    category = models.CharField(max_length=10, choices=FlowCategory.choices)
    content_type = models.CharField(max_length=10, choices=FlowContentType.choices)
    surface = models.CharField(max_length=10, choices=Surface.choices,
                               default=Surface.SERVICE)

    from_node = models.JSONField(help_text='{"id","kind","user_id","name"}')
    to_nodes = models.JSONField(default=list, help_text="같은 모양의 배열. 1:N 가능.")
    # 필터가 배열 안을 뒤지지 않도록 참여자 id 를 따로 뽑아 둡니다.
    participant_ids = models.JSONField(default=list, blank=True)

    label = models.CharField(max_length=60)
    direction_label = models.CharField(max_length=200, blank=True, default="")
    extra_participant_count = models.PositiveSmallIntegerField(default=0)

    document = models.ForeignKey("meetings.MeetingDocumentRef", on_delete=models.SET_NULL,
                                 null=True, blank=True)
    agenda = models.ForeignKey(Agenda, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="flow_edges")
    occurred_at = models.DateTimeField()
    opacity = models.FloatField(default=1.0)

    class Meta:
        db_table = "flow_edge"
        ordering = ["occurred_at"]
        indexes = [
            models.Index(fields=["meeting", "occurred_at"]),
            models.Index(fields=["meeting", "category", "content_type"]),
        ]

    def compute_opacity(self, oldest=None, newest=None):
        """
        최근일수록 진하게.

        기준을 조회 시점이 아니라 회의 구간으로 잡아야 같은 회의를 언제 열어도
        그림이 같습니다.
        """
        newest = newest or self.meeting.ended_at or timezone.now()
        oldest = oldest or self.meeting.started_at or self.meeting.scheduled_at
        span = (newest - oldest).total_seconds()
        if span <= 0:
            return 1.0
        ratio = (self.occurred_at - oldest).total_seconds() / span
        return round(0.25 + 0.75 * max(0.0, min(1.0, ratio)), 3)


class MeetingDocumentRef(UUIDModel, TimeStamped):
    """
    플로우가 나른 문서.

    1차 범위에서는 문서 도메인 전체를 만들지 않고, 플로우에 필요한 만큼만
    가볍게 들고 있습니다. 문서 기능을 붙일 때 `orgs.Document` 로 승격하십시오.
    """
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="document_refs")
    title = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    visibility = models.CharField(max_length=10, default="team",
                                  choices=[("team", "팀 공개"), ("private", "비공개")])
    sections = models.JSONField(default=list, blank=True)
    delivery_context = models.JSONField(default=list, blank=True)
    direction_label = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "meeting_document_ref"


class MeetingSummary(models.Model):
    """플로우 캔버스 가운데의 3열 표."""
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE,
                                   primary_key=True, related_name="summary")
    discovered_issues = models.JSONField(default=list, blank=True)   # 발견한 문제
    changes = models.JSONField(default=list, blank=True)             # 변동 사항
    next_plans = models.JSONField(default=list, blank=True)          # 이후 계획
    one_line = models.CharField(max_length=300, blank=True, default="")
    main_opinions = models.JSONField(default=list, blank=True)       # 홈 카드용
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "meeting_summary"


class AiBriefing(TimeStamped):
    """
    회의 × 사람.

    불참자와 참석자에게 보내는 내용이 다르므로 사람 단위로 만듭니다.
    """
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="briefings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="briefings")
    narrative = models.TextField(blank=True, default="")
    used_answers = models.JSONField(default=list, blank=True)
    deferred_answers = models.JSONField(default=list, blank=True)
    settings_version = models.PositiveIntegerField(default=1)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_briefing"
        constraints = [
            models.UniqueConstraint(fields=["meeting", "user"], name="uq_ai_briefing"),
        ]
        indexes = [models.Index(fields=["user", "read_at"])]


class FlowFilterPreset(UUIDModel, TimeStamped):
    """자주 쓰는 필터 조합. 회의·팀이 바뀌어도 같은 걸 씁니다."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="flow_filter_presets")
    name = models.CharField(max_length=60)
    participant_ids = models.JSONField(default=list, blank=True)
    content_types = models.JSONField(default=list, blank=True)
    surfaces = models.JSONField(default=list, blank=True)
    since_minutes = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "flow_filter_preset"
        ordering = ["-created_at"]
