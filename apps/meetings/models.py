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
    """
    화면 좌측 `필터링 > 내용` 체크박스와 1:1 입니다.

    회의 모드가 6종인 이유 — 와이어프레임 필터에 칸이 6개이고 화살표 뱃지에
    `일정` 이 실제로 붙어 있습니다. 3종으로 두면 회의에서 오간 일정 조정과
    결론이 플로우에 표현될 자리가 없습니다.

    `REVISION`(수정사항) → `CHANGE`(변동사항) 로 바꾼 건 화면 라벨을 따른 것입니다.
    중앙 요약표 헤더도 `변동 사항` 입니다.

    **작업 모드는 종류가 아예 다릅니다.** 같은 필터 자리에 다른 항목이 뜹니다.

        회의   의견 · 요청사항 · 변동사항 · 일정 · 결론 · 기타
        작업   작업 · 수정 · 피드백 · 공유 · AI 조회

    `DOCUMENT` · `PLAN` 은 Figma 어디에도 없는 초기 설계의 잔재입니다. 시드가
    쓰고 있어 남겨 두지만 새 코드에서는 쓰지 마십시오.
    """
    # 초기 설계 잔재. 시드 전용
    DOCUMENT = "DOCUMENT", "문서"
    PLAN = "PLAN", "계획"

    # 회의 모드 — 화면 필터 6칸과 1:1
    OPINION = "OPINION", "의견"
    REQUEST = "REQUEST", "요청사항"
    CHANGE = "CHANGE", "변동사항"
    SCHEDULE = "SCHEDULE", "일정"
    CONCLUSION = "CONCLUSION", "결론"
    ETC = "ETC", "기타"

    # 작업 모드 — 화면 필터 5칸과 1:1
    #
    # 여기 `REVISION` 은 작업물을 고친 것(`수정`)이고, 위 `CHANGE`(`변동사항`)는
    # 회의에서 무엇이 달라졌는가입니다. 이름이 비슷하지만 다른 축입니다.
    WORK = "WORK", "작업"
    REVISION = "REVISION", "수정"
    FEEDBACK = "FEEDBACK", "피드백"
    SHARE = "SHARE", "공유"
    AI_LOOKUP = "AI_LOOKUP", "AI 조회"


class Surface(models.TextChoices):
    """그 일이 **어디서** 일어났는가."""
    SERVICE = "SERVICE", "서비스"
    DISCORD = "DISCORD", "Discord"


class FlowSource(models.TextChoices):
    """
    그 산출물이 **어디에 있는가**. 작업 모드 `필터링 > 출처`.

    `Surface` 와 헷갈리기 쉬운데 다른 축입니다.

        surface   일이 일어난 곳      서비스 / Discord
        source    산출물이 있는 곳    Github / Figma / Notion

    회의 모드에서는 비어 있습니다.
    """
    GITHUB = "GITHUB", "Github"
    FIGMA = "FIGMA", "Figma"
    NOTION = "NOTION", "Notion"


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
    allowed_sources = models.JSONField(
        null=True, blank=True, default=None,
        help_text="이 회의에서 대리인이 근거로 쓸 자료 종류. "
                  "null 이면 고른 적 없음(제한 없음), [] 면 아무것도 쓰지 않음.")

    # ── 이번 회의에만 다르게 (「Bordo 활동 설정」) ─────────────
    #
    # 평소 설정(`AgentSettings`)을 **덮어쓰지 않습니다.** 회의 하나 때문에 평소
    # 설정을 바꿔 두면 그 회의가 끝난 뒤 되돌리는 것은 사람 몫이 되고, 대개
    # 잊습니다. 다음 회의에서 켜 둔 줄 알았던 것이 꺼져 있습니다.
    settings_override = models.JSONField(
        default=dict, blank=True,
        help_text="이 회의에만 적용할 설정. 바꾼 키만 담습니다. "
                  "{} 면 평소 설정 그대로.")
    prompt_override = models.JSONField(
        null=True, blank=True, default=None,
        help_text="이 회의에만 쓸 시스템 프롬프트 목록. "
                  "null 이면 평소 것 그대로, [] 면 아무 프롬프트도 쓰지 않음.")

    #: 이 회의에서 대리인이 볼 수 있는 자료 종류.
    #
    # `search_records` 의 `kinds` 와 **같은 값을 씁니다.** 화면에서 고른 것이
    # 검색 종류와 다른 낱말이면, 무엇을 끄면 무엇이 안 나오는지 사용자가
    # 짚을 수 없습니다.
    SOURCE_CHOICES = ("work", "plan", "thought", "document")

    @property
    def source_scope(self) -> list[str]:
        """
        실제로 검색에 쓸 종류.

        **`null` 과 `[]` 는 다릅니다.**

            null   고른 적 없음  → 전부
            []     전부 껐음     → 아무것도 안 봄

        둘을 같게 다루면 전부 끈 사람의 대리인이 **모든 자료를 보게 됩니다.**
        정반대로 동작하는 셈입니다.
        """
        if self.allowed_sources is None:
            return list(self.SOURCE_CHOICES)
        return [s for s in self.allowed_sources if s in self.SOURCE_CHOICES]

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

    @property
    def uses_standing_settings(self) -> bool:
        """
        화면의 `현재 설정 사용` / `이번에만 다르게 사용` 중 어느 쪽인가.

        컬럼을 따로 두지 않습니다. 모드 컬럼과 실제 덮어쓴 값이 어긋나면
        (모드는 `현재 설정 사용` 인데 override 에 값이 남아 있는 식) 화면과
        대리인이 서로 다른 것을 보게 됩니다.
        """
        return not self.settings_override and self.prompt_override is None

    #: 낱개로 덮어쓰면 파생값도 다시 계산해야 하는 키들.
    DISCLOSURE_KEYS = ("disclose_work", "disclose_plan", "disclose_thought")

    def effective_settings(self, base: dict) -> dict:
        """
        평소 설정 위에 이 회의 것만 얹습니다.

        **파생 키(`disclose_work_plan_thought`)를 다시 계산합니다.** 그냥 덮으면
        낱개 셋을 모두 끈 회의에서도 옛 키가 켜진 채로 남아, 화면은 `공개 안 함`
        인데 판정은 통과하는 정반대 상태가 됩니다. 읽는 쪽(준비 화면)과 쓰는
        쪽(대리인 실행)이 각자 계산하면 언젠가 한쪽만 고쳐집니다.
        """
        merged = {**(base or {}), **(self.settings_override or {})}
        if any(k in (self.settings_override or {}) for k in self.DISCLOSURE_KEYS):
            merged["disclose_work_plan_thought"] = any(
                merged.get(k, True) for k in self.DISCLOSURE_KEYS)
        return merged


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
    """
    `회의 맥락` / `전달 맥락` 의 한 줄.

    **대리인이 한 발언도 여기 남습니다.** 회의 요약(`briefing.build_summary`)과
    다음 회의의 논쟁점 예측(`contention`)이 이 표만 읽기 때문에, 대리인 발언이
    빠지면 자리를 비운 사람을 대신해 한 말이 요약에서 통째로 사라집니다.
    """
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="utterances")
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True)
    participant_name = models.CharField(max_length=100)    # 비정규화
    body = models.TextField()
    spoken_at = models.DateTimeField(null=True, blank=True)
    #: 대리인이 주인을 대신해 한 발언입니다. `participant` 는 **주인**이고
    #: `participant_name` 은 `{이름}의 Bordo` 입니다 — 누구를 대신했는지와
    #: 누가 말했는지가 둘 다 필요합니다.
    #:
    #: 이 칸이 켜진 발언으로는 **대리인을 깨우지 않습니다**(`agent/tasks.py`).
    #: 깨우면 A 의 대리인이 답한 것을 B 의 대리인이 다시 받아 서로 끝없이
    #: 말하게 됩니다 — 대상 판정은 발언자 본인만 후보에서 빼기 때문입니다.
    is_agent = models.BooleanField(default=False)

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
    # 회의 플로우만 채웁니다.
    #
    # 작업 플로우는 회의가 아니라 **기간**이 스코프입니다(화면 헤더가
    # `8.10 - 8.16 작업 흐름`). 붙일 회의가 없으므로 비워 둡니다.
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE,
                                null=True, blank=True, related_name="flow_edges")
    # 두 모드 공통. 작업 플로우 조회의 기준이 됩니다.
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="flow_edges")
    category = models.CharField(max_length=10, choices=FlowCategory.choices)
    content_type = models.CharField(max_length=10, choices=FlowContentType.choices)
    surface = models.CharField(max_length=10, choices=Surface.choices,
                               default=Surface.SERVICE)
    # 작업 모드 `필터링 > 출처`. 회의 모드에서는 빕니다.
    source = models.CharField(max_length=10, choices=FlowSource.choices,
                              blank=True, default="")
    # 화면에서 눌러 원본으로 이동합니다.
    source_url = models.URLField(blank=True, default="")

    from_node = models.JSONField(help_text='{"id","kind","user_id","name"}')
    to_nodes = models.JSONField(default=list, help_text="같은 모양의 배열. 1:N 가능.")
    # 필터가 배열 안을 뒤지지 않도록 참여자 id 를 따로 뽑아 둡니다.
    participant_ids = models.JSONField(default=list, blank=True)

    label = models.CharField(max_length=60)
    direction_label = models.CharField(max_length=200, blank=True, default="")
    extra_participant_count = models.PositiveSmallIntegerField(default=0)

    document = models.ForeignKey("meetings.MeetingDocumentRef", on_delete=models.SET_NULL,
                                 null=True, blank=True)
    #: 작업 모드 좌측 인덱스가 묶는 문서.
    #:
    #: 위 `document` 와 **다른 것**입니다. 저쪽은 회의에서 오간 문서의 스냅샷
    #: (`MeetingDocumentRef`)이고, 이쪽은 프로젝트에 실제로 있는 문서입니다.
    #: 작업 엣지에는 회의가 없어 스냅샷을 만들 자리가 없습니다 — 이 칸이 없으면
    #: 작업 모드 인덱스는 무엇으로도 묶이지 않아 언제나 빈 목록입니다.
    work_document = models.ForeignKey("documents.Document", on_delete=models.SET_NULL,
                                      null=True, blank=True,
                                      related_name="flow_edges")
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
            # 작업 플로우 조회 그대로입니다: 프로젝트의 WORK 엣지를 기간으로 자름.
            models.Index(fields=["project", "category", "occurred_at"]),
        ]

    def compute_opacity(self, oldest=None, newest=None):
        """
        최근일수록 진하게.

        기준을 조회 시점이 아니라 회의 구간으로 잡아야 같은 회의를 언제 열어도
        그림이 같습니다.
        """
        # 작업 플로우 엣지에는 회의가 없습니다. 그때는 호출부가 조회 기간을
        # oldest/newest 로 넘겨야 합니다 — 넘기지 않으면 갈릴 기준이 없어
        # 전부 같은 값이 됩니다.
        if self.meeting_id is None:
            if oldest is None or newest is None:
                return 1.0
        else:
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
    #: 요약 아래 `정보 위치 칩`. 눌러도 상태가 남지 않아 스냅샷으로 충분합니다.
    location_chips = models.JSONField(default=list, blank=True)
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


class BriefingCard(TimeStamped):
    """
    브리핑 카드의 공통 뼈대.

    ## 왜 JSON 이 아니라 테이블인가

    `AiBriefing` 은 `update_or_create` 로 **통째로 덮어씁니다.** 회의가 다시 종료
    처리되는 경우가 실제로 있어서인데(봇 재시도·중복 호출), 카드를 그 JSON 안에 두면
    **사용자가 이미 눌러 둔 확인 표시가 재생성 때마다 리셋됩니다.** 확인해서 없앤
    카드가 되살아납니다.

    행으로 두면 `source_key` 로 upsert 하면서 사람이 남긴 흔적만 보존할 수 있습니다.

    ## 왜 사람마다 행을 복제하는가

    같은 변경을 세 사람이 봐야 하면 세 행이 생깁니다. 정규화하면 한 행으로 줄지만,
    브리핑은 **그 시점의 스냅샷**입니다. 원문이 나중에 고쳐졌다고 내가 어제 받은
    브리핑 문구까지 같이 바뀌면 "내가 그때 뭘 보고 확인했는지"가 사라집니다.
    복제는 비용이 아니라 여기서는 성질입니다.
    """
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE,
                                related_name="%(class)ss")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="%(class)ss")
    #: 재생성 때 같은 카드를 알아보는 열쇠. `edge:<id>` 처럼 출처를 그대로 씁니다.
    #: 화살표에서 온 카드도 안건에서 온 카드도 같은 방식으로 다루려고 문자열입니다.
    source_key = models.CharField(max_length=80)
    title = models.CharField(max_length=200)
    edge = models.ForeignKey("meetings.FlowEdge", on_delete=models.SET_NULL,
                             null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        abstract = True


class BriefingConfirmation(UUIDModel, BriefingCard):
    """
    브리핑 `확인이 필요해요` 카드.

    **`used_answers` / `deferred_answers` 와 다릅니다.** 그 둘은 *내 대리인이 무엇을
    답했나* 이고, 이것은 *내가 없는 사이 무엇이 바뀌었나* 입니다. 자리를 비운 사람이
    돌아와서 가장 먼저 봐야 하는 것이라 섹션을 따로 뺐습니다.

    확인은 **사람마다**입니다. 한 사람이 눌렀다고 다른 사람 목록에서 사라지면,
    아직 못 본 사람이 변경을 영영 모르고 지나갑니다.
    """
    body = models.TextField(blank=True, default="")
    agenda = models.ForeignKey(Agenda, on_delete=models.SET_NULL, null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "briefing_confirmation"
        ordering = ["occurred_at"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "user", "source_key"],
                                    name="uq_briefing_confirmation"),
        ]
        indexes = [models.Index(fields=["user", "confirmed_at"])]


class BriefingRequest(UUIDModel, BriefingCard):
    """
    브리핑 `나에게 요청한 내용` 카드.

    태스크와 닮았지만 **아직 태스크가 아닙니다.** 회의에서 나에게 넘어온 요청을
    그대로 보여주는 자리이고, 사람이 받아들이면 그때 `task` 가 붙습니다.

    곧바로 `PENDING_APPROVAL` 태스크로 찍지 않는 이유 — 승인 큐가 남의 회의 요청으로
    가득 차면 승인이라는 행위 자체가 뜻을 잃습니다. 받아들일지는 사람이 정합니다.
    """
    requester_name = models.CharField(max_length=100)
    #: 기한이 없을 때 대신 붙는 문구. 예: `다음 회의 전까지 확인이 필요해요.`
    note = models.CharField(max_length=200, blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    task = models.ForeignKey("tasks.Task", on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="briefing_requests")
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "briefing_request"
        ordering = ["occurred_at"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "user", "source_key"],
                                    name="uq_briefing_request"),
        ]
        indexes = [models.Index(fields=["user", "accepted_at"])]


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


# ═══════════════════════════════════════════ 회의 대리 참석 준비
#
# 자리를 비우기로 한 사람이 **회의 전에** 채워 두는 것들입니다.
# 회의가 끝난 뒤 만들어지는 `AiBriefing` 과 앞뒤로 짝을 이룹니다.
#
#     회의 전   DebatePoint  → DebateStance   "이런 게 쟁점일 텐데, 내 입장은 이렇다"
#     회의 중   대리인이 그 입장을 근거로 말함
#     회의 후   AiBriefing                     "없는 사이 이렇게 정해졌다"


class DebatePoint(UUIDModel, TimeStamped):
    """
    예상 논쟁점.

    ## 사람이 아니라 회의에 답니다

    "이번 회의에서 의견이 갈릴 곳" 은 회의의 성질이지 개인의 것이 아닙니다.
    두 사람이 불참하면 같은 쟁점을 봅니다 — 사람마다 만들면 같은 예측을 두 번
    돌리고, 둘이 서로 다른 쟁점을 보게 되어 회의 후에 말이 안 맞습니다.
    **갈리는 것은 입장(`DebateStance`)뿐입니다.**

    ## `source_key` 로 다시 알아봅니다

    예측은 회의 직전에 다시 돌 수 있습니다(자료가 늘었을 때). 통째로 지우고 새로
    만들면 **사람이 이미 적어 둔 입장이 함께 사라집니다.** 브리핑 카드와 같은
    방식으로 열쇠를 두고 갱신합니다.
    """
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE,
                                related_name="debate_points")
    #: 재예측 때 같은 쟁점을 알아보는 열쇠. 제목에서 만듭니다.
    source_key = models.CharField(max_length=80)
    #: 화면의 `논쟁점 01`. 1부터.
    order = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=200, help_text="질문형. `A 할 것인가, B 할 것인가?`")
    #: [{"key": "A", "title": "핵심 기능만 구현", "description": "남은 기간을 고려해…"}]
    #: 둘로 갈리는 게 보통이지만 셋 이상도 담을 수 있게 배열로 둡니다.
    options = models.JSONField(default=list, blank=True)
    rationale = models.TextField(blank=True, default="",
                                 help_text="`Bordo가 이렇게 예측했어요` 문단")
    #: 근거 카드. [{"kind": "meeting"|"work", "title", "body", "at", "who", "link"}]
    #: 스냅샷입니다 — 원본이 나중에 바뀌어도 그때 무엇을 보고 예측했는지가 남아야 합니다.
    evidence = models.JSONField(default=list, blank=True)
    created_by_agent = models.BooleanField(default=True)

    class Meta:
        db_table = "meeting_debate_point"
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "source_key"],
                                    name="uq_debate_point"),
        ]
        indexes = [models.Index(fields=["meeting", "order"])]

    def __str__(self):
        return f"{self.order:02d} {self.title}"


class DebateStance(UUIDModel, TimeStamped):
    """
    논쟁점에 대한 나의 입장.

    **대리인이 회의에서 이걸 근거로 말합니다.** 그래서 사람마다 하나입니다 —
    같은 쟁점에 내 입장이 둘이면 대리인이 어느 쪽으로 말할지 정할 수 없습니다.
    고쳐 쓰는 것은 덮어쓰기입니다.

    `option_key` 는 비어 있을 수 있습니다. 화면의 선택지 A/B 를 고르지 않고
    글만 적는 경우가 정상이라, 고르지 않았다고 저장을 막지 않습니다.
    """
    point = models.ForeignKey(DebatePoint, on_delete=models.CASCADE,
                              related_name="stances")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="debate_stances")
    option_key = models.CharField(max_length=8, blank=True, default="",
                                  help_text="고른 선택지. 비어 있을 수 있습니다.")
    body = models.TextField()

    class Meta:
        db_table = "meeting_debate_stance"
        ordering = ["point__order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["point", "user"], name="uq_debate_stance"),
        ]
        indexes = [models.Index(fields=["user"])]

    def __str__(self):
        return f"{self.user_id}: {self.body[:30]}"
