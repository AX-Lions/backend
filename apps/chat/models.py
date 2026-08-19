"""
앱 내 채팅.

방 5종(AI · DIRECT · TEAM · PROJECT · PEER_AGENT), 중요 표시/확인,
날짜별 요약, 달력.

설계에서 두 가지가 계약과 다르게 갈렸습니다. 둘 다 이유가 있습니다.

1. **중요 확인은 사용자별입니다.** OpenAPI 의 `ChatMessage.important_confirmed_at`
   은 메시지 하나에 붙은 스칼라라, 단체방에서 한 사람이 확인하면 다른 사람의
   `중요 채팅` 목록에서도 사라집니다. `MessageImportance` 를 따로 두어
   (message, user) 단위로 기록합니다. 응답 필드 이름은 계약 그대로 두되
   **값은 요청한 사람 기준**으로 채웁니다.

2. **읽음은 워터마크입니다.** 메시지마다 읽음 행을 쌓으면 단체방 10명 × 1만 건에
   10만 행이 됩니다. `RoomMember.last_read_at` 하나로 미읽음을 셉니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel


class RoomType(models.TextChoices):
    AI = "AI", "나의 AI 대리인"
    DIRECT = "DIRECT", "개인 채팅"
    TEAM = "TEAM", "팀 채팅"
    PROJECT = "PROJECT", "프로젝트 채팅"
    PEER_AGENT = "PEER_AGENT", "동료의 AI 대리인"


#: 이름을 사용자가 못 바꾸는 방. 상대 이름이나 팀 이름으로 표시됩니다.
UNRENAMABLE = (RoomType.DIRECT, RoomType.AI, RoomType.PEER_AGENT)
#: 사람을 초대할 수 있는 방.
GROUP_TYPES = (RoomType.TEAM, RoomType.PROJECT)
#: 나가면 목록에서 숨기기만 하는 방(기록은 상대 쪽에 남습니다).
HIDE_ON_LEAVE = (RoomType.DIRECT, RoomType.PEER_AGENT)


class ChatRoom(UUIDModel, TimeStamped, SoftDeletable):
    type = models.CharField(max_length=12, choices=RoomType.choices)
    title = models.CharField(max_length=120, blank=True, default="")

    team = models.ForeignKey("orgs.Team", on_delete=models.CASCADE, null=True, blank=True,
                             related_name="chat_rooms")
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                null=True, blank=True, related_name="chat_rooms")
    # 비정규화 — 사이드바 트리와 `팀 A - 프로젝트 b` 경로 표기에 매번 씁니다.
    team_name = models.CharField(max_length=120, blank=True, default="")
    project_name = models.CharField(max_length=120, blank=True, default="")

    #: PEER_AGENT 방에서 상대가 누구의 대리인인지. AI 방은 본인입니다.
    agent_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                    null=True, blank=True, related_name="peer_agent_rooms")

    #: DIRECT 중복 생성 방지용 지문. `min(uid),max(uid)` 를 정렬해 넣습니다.
    dedupe_key = models.CharField(max_length=120, blank=True, default="", db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, related_name="created_chat_rooms")
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "chat_room"
        constraints = [
            models.UniqueConstraint(
                fields=["type", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="uq_chat_room_dedupe"),
            # 팀·프로젝트 단체방은 하나뿐입니다.
            models.UniqueConstraint(fields=["team"], condition=models.Q(type="TEAM"),
                                    name="uq_chat_room_team"),
            models.UniqueConstraint(fields=["project"], condition=models.Q(type="PROJECT"),
                                    name="uq_chat_room_project"),
        ]
        indexes = [models.Index(fields=["team", "project"]),
                   models.Index(fields=["-last_message_at"])]

    def __str__(self):
        return f"[{self.type}] {self.title or self.id}"

    @property
    def path_label(self):
        """`팀 A - 프로젝트 b`. 둘 다 없으면 빈 문자열입니다."""
        return " - ".join(x for x in (self.team_name, self.project_name) if x)


class RoomMember(TimeStamped):
    """
    방 참여자.

    `last_read_at` 하나로 미읽음을 셉니다. 메시지별 읽음 행을 쌓으면
    단체방에서 행이 사람 수만큼 곱해집니다.
    `hidden_at` 은 DIRECT 를 나갔을 때 — 기록은 두고 목록에서만 감춥니다.
    """
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="chat_memberships")
    last_read_at = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    #: 나중에 초대된 사람은 이 시각 이후 메시지만 봅니다.
    visible_from = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    #: 이 방 알림을 껐는가.
    #:
    #: **방을 나가는 것과 다릅니다.** 나가면 목록에서 사라지고 새 메시지도
    #: 안 보이는데, 알림만 끄는 것은 대화는 계속 보되 소리로 부르지 말라는
    #: 뜻입니다. 미읽음 수는 그대로 셉니다 — 안 세면 껐다는 것과 다 읽었다는
    #: 것이 화면에서 구별되지 않습니다.
    muted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_room_member"
        constraints = [
            models.UniqueConstraint(fields=["room", "user"], name="uq_chat_room_member"),
        ]
        indexes = [models.Index(fields=["user", "left_at"]),
                   models.Index(fields=["room", "left_at"])]


class ChatAttachment(UUIDModel, TimeStamped):
    """
    첨부.

    메시지에 붙기 전까지는 `UPLOADED` 상태로 떠 있습니다. 이 상태로 방치된 파일은
    `expires_at` 이후 청소 대상입니다 — 보내다 만 파일이 영원히 쌓이면 안 됩니다.
    """
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "업로드됨"
        ATTACHED = "ATTACHED", "메시지에 붙음"
        EXPIRED = "EXPIRED", "만료"

    class Kind(models.TextChoices):
        IMAGE = "IMAGE", "이미지"
        FILE = "FILE", "파일"

    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="attachments")
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="chat_attachments")
    message = models.ForeignKey("chat.ChatMessage", on_delete=models.CASCADE,
                                null=True, blank=True, related_name="attachments")
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.UPLOADED)
    kind = models.CharField(max_length=6, choices=Kind.choices, default=Kind.FILE)
    name = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=120, blank=True, default="")
    url = models.CharField(max_length=500, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_attachment"
        indexes = [models.Index(fields=["room", "status"]),
                   models.Index(fields=["status", "expires_at"])]


class ChatMessage(UUIDModel):
    """
    메시지.

    삭제는 내용만 비웁니다(`deleted_at` + 빈 `body`). 자리를 통째로 없애면
    앞뒤 대화의 맥락이 끊겨 남은 사람이 무슨 얘기였는지 못 따라갑니다.
    """
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, related_name="chat_messages")
    sender_name = models.CharField(max_length=100)         # 비정규화
    is_agent = models.BooleanField(default=False, help_text="대리인이 보낸 메시지")
    body = models.TextField(blank=True, default="")

    is_important = models.BooleanField(default=False)
    #: 답변 대기 질문에 대한 답으로 보낸 메시지. 브리핑 카드를 닫는 근거가 됩니다.
    pending_question = models.ForeignKey(
        "agent.PendingQuestion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="chat_answers")

    client_message_id = models.CharField(
        max_length=80, blank=True, default="",
        help_text="`room_id + client_message_id` 로 중복 전송을 막습니다.")

    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_message"
        ordering = ["-sent_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "client_message_id"],
                condition=~models.Q(client_message_id=""),
                name="uq_chat_message_client_id"),
        ]
        indexes = [models.Index(fields=["room", "-sent_at"]),
                   models.Index(fields=["room", "is_important"])]

    def __str__(self):
        return f"{self.sender_name}: {self.body[:20]}"

    @property
    def is_deleted(self):
        return self.deleted_at is not None


class MessageImportance(models.Model):
    """
    중요 확인 — **사용자별**.

    단체방에서 A 가 확인해도 B 의 `중요 채팅` 목록에는 그대로 남아야 합니다.
    메시지 한 행에 `important_confirmed_at` 을 두면 그게 안 됩니다.
    """
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE,
                                related_name="importance_receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="importance_receipts")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_message_importance"
        constraints = [
            models.UniqueConstraint(fields=["message", "user"],
                                    name="uq_message_importance"),
        ]
        indexes = [models.Index(fields=["user", "confirmed_at"])]


class DailyChatSummary(models.Model):
    """
    `2026년 8월 9일 채팅 요약` 패널.

    AI 가 채우는 자리라 1차에서는 빈 껍데기로 만들어 두고, B 가 생성기를 붙이면
    같은 행을 갱신합니다. 조회 쪽 계약은 지금 확정해 둡니다.
    """
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE,
                             related_name="daily_summaries")
    date = models.DateField()
    one_line = models.CharField(max_length=300, blank=True, default="")
    my_todos = models.JSONField(default=list, blank=True)
    schedules = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_daily_summary"
        constraints = [
            models.UniqueConstraint(fields=["room", "date"], name="uq_chat_daily_summary"),
        ]
        indexes = [models.Index(fields=["room", "-date"])]
