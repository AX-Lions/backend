"""
AI 대리인.

설정은 팀이 아니라 **사람**에게 붙습니다. 사람 하나에 대리인 하나입니다.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

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
        constraints = [
            # 한 실행은 유보 질문을 하나만 남깁니다.
            #
            # `deferral.record()` 가 "있는지 보고 없으면 넣는" 방식이라, 같은 run 에
            # 대해 동시에 두 번 불리면 둘 다 "없다" 를 보고 둘 다 넣습니다.
            # 그러면 사용자는 같은 질문에 두 번 답해야 합니다.
            #
            # 코드에서 막는 것과 DB 에서 막는 것은 다릅니다 — 앞의 것은 순서가
            # 어긋나면 뚫리고, 뒤의 것은 안 뚫립니다.
            #
            # run 이 없는 행도 있습니다(시드, 삭제된 실행은 SET_NULL). 그쪽까지
            # 하나로 묶으면 안 되므로 조건부 제약입니다.
            models.UniqueConstraint(fields=["run"], condition=~models.Q(run=None),
                                    name="uq_pending_question_run"),
        ]
        indexes = [models.Index(fields=["target_user", "answered_at"]),
                   models.Index(fields=["meeting"])]


class OutboxEvent(UUIDModel, TimeStamped):
    """
    Discord 로 나갈 것들의 대기열.

    ## 왜 큐를 거치는가

    **외부 호출을 요청 트랜잭션 안에서 하면 안 됩니다.** 트랜잭션이 롤백돼도
    Discord 메시지는 이미 나가 있습니다. 되돌릴 방법이 없습니다.

    여기에는 행 하나만 남기고, 봇이 폴링해서 가져가 게시한 뒤 결과를 돌려줍니다.
    DB 커밋과 발송이 분리되므로 롤백되면 행도 함께 사라집니다.

    ## 멱등키

    `(team, idempotency_key)` 가 유니크입니다. 같은 발언을 두 번 큐에 넣어도
    한 번만 나갑니다. 네트워크가 끊겨 재시도할 때 중복 게시를 막는 유일한 장치입니다.

    봇 쪽에서 들어오는 이벤트의 멱등키는 `guild_id + channel_id + message_id` 이고,
    나가는 쪽은 `run_id` 나 발언 식별자를 씁니다 — 방향이 다르므로 규칙도 다릅니다.

    ## 상태

        PENDING ──▶ SENT       봇이 게시 성공
            │
            └────▶ FAILED ──▶ PENDING   재시도 대기
                       │
                       └────▶ DEAD      상한 초과. 사람이 봐야 함

    `DEAD` 를 따로 둔 이유는 조용히 사라지는 것을 막기 위해서입니다. 무한 재시도는
    같은 오류를 계속 반복하고, 그냥 버리면 아무도 모릅니다.
    """

    class Kind(models.TextChoices):
        MESSAGE = "MESSAGE", "회의 발언"
        ANNOUNCEMENT = "ANNOUNCEMENT", "일정 공지"
        DM = "DM", "개인 알림"

    class Status(models.TextChoices):
        PENDING = "PENDING", "대기"
        SENT = "SENT", "발송됨"
        FAILED = "FAILED", "실패 (재시도 대기)"
        DEAD = "DEAD", "포기 (사람 확인 필요)"

    team = models.ForeignKey("orgs.Team", on_delete=models.CASCADE,
                             related_name="outbox_events")
    #: 같은 팀 안에서 유일. 재시도 시 중복 게시를 막습니다.
    idempotency_key = models.CharField(max_length=120)

    kind = models.CharField(max_length=16, choices=Kind.choices)
    #: 봇이 그대로 쓰는 값. 서버는 채널을 해석하지 않습니다.
    channel_id = models.CharField(max_length=40, blank=True, default="")
    payload = models.JSONField(default=dict)

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    #: 이 시각 전에는 봇이 가져가지 않습니다. 실패할수록 뒤로 밉니다.
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)

    #: 어떤 실행이 만든 발언인가. 삭제돼도 큐는 남아야 하므로 SET_NULL 입니다.
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL,
                            null=True, blank=True, related_name="outbox_events")

    class Meta:
        db_table = "outbox_event"
        constraints = [
            models.UniqueConstraint(fields=["team", "idempotency_key"],
                                    name="uniq_outbox_team_idem"),
        ]
        # 봇의 폴링 쿼리 그대로입니다: PENDING 중 available_at 지난 것을 오래된 순으로.
        indexes = [models.Index(fields=["status", "available_at"])]

    def __str__(self):
        return f"{self.kind} {self.status} {self.idempotency_key}"

    # ── 봇이 결과를 돌려줄 때 ──────────────────────────────
    def mark_sent(self):
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.last_error = ""
        self.save(update_fields=["status", "sent_at", "last_error", "updated_at"])

    def mark_failed(self, error: str = ""):
        """
        재시도 간격을 지수로 늘립니다.

        같은 간격으로 반복하면 Discord 가 잠시 불안정할 때 재시도가 몰려
        상황을 더 나쁘게 만듭니다.
        """
        self.attempts += 1
        self.last_error = (error or "")[:2000]
        if self.attempts >= self.max_attempts:
            self.status = self.Status.DEAD
        else:
            self.status = self.Status.PENDING
            self.available_at = timezone.now() + timedelta(seconds=30 * (2 ** (self.attempts - 1)))
        self.save(update_fields=["status", "attempts", "last_error",
                                 "available_at", "updated_at"])
