"""
AI 대리인.

설정은 팀이 아니라 **사람**에게 붙습니다. 사람 하나에 대리인 하나입니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStamped, UUIDModel


class AgentSettings(TimeStamped):
    """
    세부 설정 4종.

    와이어프레임의 O / X 두 개짜리 선택을 그대로 boolean 으로 옮겼습니다.
    `active_version` 은 저장할 때마다 오르며, 대리인의 모든 발언이
    '그때 그 버전으로 판정했다'를 가리킵니다.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                primary_key=True, related_name="agent_settings")
    mention_feasibility = models.BooleanField(default=True)          # 구현 가능성 언급
    allow_schedule_change = models.BooleanField(default=True)        # 일정 수정 여부
    allow_midmeeting_question = models.BooleanField(default=False)   # 회의 중간 질문
    disclose_work_plan_thought = models.BooleanField(default=True)   # 작업/계획/생각 공개
    active_version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "agent_settings"

    def as_snapshot(self):
        return {
            "mention_feasibility": self.mention_feasibility,
            "allow_schedule_change": self.allow_schedule_change,
            "allow_midmeeting_question": self.allow_midmeeting_question,
            "disclose_work_plan_thought": self.disclose_work_plan_thought,
            "active_version": self.active_version,
        }


class AgentSettingsVersion(UUIDModel, TimeStamped):
    """수정 이력. 덮어쓰지 않고 새 행을 쌓습니다."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="agent_settings_versions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    activated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_settings_version"
        constraints = [
            models.UniqueConstraint(fields=["user", "version"], name="uq_agent_settings_version"),
        ]
        ordering = ["-version"]


class AgentPrompt(UUIDModel, TimeStamped):
    """시스템 프롬프트 카드. 저장할 때마다 하나씩 쌓입니다."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="agent_prompts")
    body = models.TextField()

    class Meta:
        db_table = "agent_prompt"
        ordering = ["-created_at"]


class AgentConversation(UUIDModel, TimeStamped):
    """`나의 AI 대리인` 대화. 제목은 서버가 자동 생성합니다."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="agent_conversations")
    title = models.CharField(max_length=200, default="새 대화")
    title_pinned = models.BooleanField(
        default=False, help_text="사용자가 직접 고치면 자동 갱신을 멈춥니다.")
    last_message_preview = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "agent_conversation"
        ordering = ["-updated_at"]


class AgentMessage(UUIDModel):
    class Role(models.TextChoices):
        USER = "USER", "사용자"
        AGENT = "AGENT", "대리인"

    conversation = models.ForeignKey(AgentConversation, on_delete=models.CASCADE,
                                     related_name="messages")
    role = models.CharField(max_length=6, choices=Role.choices)
    body = models.TextField()
    run = models.ForeignKey("agent.AgentRun", on_delete=models.SET_NULL,
                            null=True, blank=True, related_name="messages")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_message"
        indexes = [models.Index(fields=["conversation", "-sent_at"])]


class AgentRun(UUIDModel, TimeStamped):
    """
    대리인 실행 하나.

    `settings_snapshot` 은 완료 후 불변입니다 — '무엇을 보고 어떤 설정으로 답했는가' 를
    나중에 재현할 수 있어야 하기 때문입니다.
    """
    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "접수"
        ANALYZING = "ANALYZING", "분석"
        SEARCHING = "SEARCHING", "검색"
        CHECKING_POLICY = "CHECKING_POLICY", "정책 확인"
        GENERATING = "GENERATING", "생성"
        QUEUED_FOR_DELIVERY = "QUEUED_FOR_DELIVERY", "발송 대기"
        COMPLETED = "COMPLETED", "완료"
        FAILED = "FAILED", "실패"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="agent_runs")
    meeting = models.ForeignKey("meetings.Meeting", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="agent_runs")
    status = models.CharField(max_length=24, choices=Status.choices,
                              default=Status.RECEIVED)
    trace_id = models.UUIDField(null=True, blank=True, db_index=True)
    hop_count = models.PositiveSmallIntegerField(default=0)
    max_hops = models.PositiveSmallIntegerField(default=3)

    settings_snapshot = models.JSONField(default=dict, blank=True)
    steps = models.JSONField(default=list, blank=True,
                             help_text="ReAct 단계. 실행 중에만 늘고 완료 후 불변입니다.")
    evidence = models.JSONField(default=list, blank=True,
                                help_text="검색 근거. 문서 제목은 스냅샷으로 남깁니다.")
    result = models.TextField(blank=True, default="")

    class Meta:
        db_table = "agent_run"
        indexes = [models.Index(fields=["meeting", "-created_at"])]


class PendingQuestion(UUIDModel, TimeStamped):
    """
    대리인이 답을 유보한 질문.

    화면에서 클릭하면 답변용 채팅창이 열리므로 `chat_room_id` 를 함께 들고 있습니다.
    """
    meeting = models.ForeignKey("meetings.Meeting", on_delete=models.CASCADE,
                                related_name="pending_questions")
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL, null=True, blank=True)
    asker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, related_name="asked_questions")
    asker_name = models.CharField(max_length=100)          # 비정규화
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                    related_name="pending_questions")
    title = models.CharField(max_length=200)
    body = models.TextField()
    chat_room_id = models.UUIDField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    answer_body = models.TextField(blank=True, default="")

    class Meta:
        db_table = "pending_question"
        indexes = [models.Index(fields=["target_user", "answered_at"]),
                   models.Index(fields=["meeting"])]
