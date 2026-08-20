"""
채팅 직렬화.

`is_mine` · `unread_count` · `important_confirmed_at` 은 전부 **보는 사람 기준**
값이라 context 로 받습니다. serializer 안에서 조회하면 목록 하나에 N+1 이 납니다.
"""
from rest_framework import serializers

from apps.common.display import avatar_of

from .models import (ChatAttachment, ChatMessage, ChatRoom, DailyChatSummary,
                     RoomType)


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatAttachment
        fields = ("id", "kind", "name", "size_bytes", "mime_type", "url",
                  "status", "expires_at", "client_upload_id")


class MessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)
    is_mine = serializers.SerializerMethodField()
    important_confirmed_at = serializers.SerializerMethodField()
    read_count = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()
    is_from_my_agent = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ("id", "room_id", "sender", "body", "attachments", "sent_at",
                  "is_mine", "is_from_my_agent", "answered_while_away",
                  "is_important", "important_confirmed_at",
                  "read_count", "edited_at", "deleted_at", "pending_question_id")

    def get_sender(self, obj):
        return {
            "id": str(obj.sender_id) if obj.sender_id else None,
            "name": obj.sender_name,
            "avatar_url": avatar_of((self.context.get("avatars") or {})
                                    .get(obj.sender_id)),
            "is_agent": obj.is_agent,
        }

    def get_body(self, obj):
        # 삭제된 메시지는 자리만 남깁니다. 화면에서 `삭제된 메시지입니다` 로 그립니다.
        return "" if obj.deleted_at else obj.body

    def get_is_mine(self, obj):
        """
        **내가 쓴 문장인가.** 대리인이 내 이름으로 한 말은 여기서 빠집니다.

        화면이 이 값으로 `수정`·`삭제` 를 붙이는데, 대리인 발언은 눌러 봐야
        서버가 거절합니다 — 내가 쓴 문장이 아니기 때문입니다. 말풍선을 어느
        쪽에 앉힐지는 `is_from_my_agent` 가 따로 답합니다.
        """
        return obj.sender_id == self.context.get("me_id") and not obj.is_agent

    def get_is_from_my_agent(self, obj):
        """내 대리인이 나 대신 한 말. 자리는 내 쪽이고 편집 권한은 없습니다."""
        return bool(obj.is_agent) and obj.sender_id == self.context.get("me_id")

    def get_important_confirmed_at(self, obj):
        """**내가** 확인했는지. 남이 확인한 건 내 화면에 반영되지 않습니다."""
        return (self.context.get("confirmed_map") or {}).get(obj.id)

    def get_read_count(self, obj):
        return (self.context.get("read_map") or {}).get(obj.id, 0)


class RoomSummarySerializer(serializers.ModelSerializer):
    path_label = serializers.CharField(read_only=True)
    title = serializers.SerializerMethodField()
    avatar_urls = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    has_important = serializers.SerializerMethodField()
    members = serializers.SerializerMethodField()
    muted = serializers.SerializerMethodField()

    class Meta:
        model = ChatRoom
        fields = ("id", "type", "title", "team_id", "team_name", "project_id",
                  "project_name", "path_label", "avatar_urls", "members",
                  "last_message", "unread_count", "has_important", "muted")

    def get_title(self, obj):
        """
        대리인 방은 **주인의 지금 호칭**으로 부릅니다 (`{이름}의 Bordo`).

        `AI 대리인` 은 화면 어디에도 없는 낱말이라 저장된 제목을 그대로 쓰면
        목록에만 그 낱말이 남습니다. 이름은 개인 설정에서 바뀌므로 조회 시점에
        맞춥니다 — 뷰가 `agent_name_map` 으로 한 번에 모아 넘깁니다.
        """
        named = (self.context.get("agent_name_map") or {}).get(obj.agent_owner_id)
        if named:
            return named
        if obj.title:
            return obj.title

        # 제목이 빈 1:1 방은 **상대 이름**으로 부릅니다.
        #
        # 1:1 방에는 이름을 안 붙이고 만드는 경로가 있습니다(시드 · 유보 답변
        # 방). 그대로 두면 사이드바 「개인 대화」에 **글자 없는 줄**이 서서,
        # 누구와의 대화인지 열어 봐야 압니다. 저장값으로는 못 고칩니다 —
        # 같은 방을 두 사람이 서로 다른 이름으로 봐야 하기 때문입니다.
        if obj.type == RoomType.DIRECT:
            members = (self.context.get("member_map") or {}).get(obj.id, [])
            other = next((m for m in members if not m.get("is_me")), None)
            if other:
                return other.get("name") or ""
        return obj.title

    def get_avatar_urls(self, obj):
        # 단체방 4분할 썸네일. 4개를 넘기면 화면에서 못 씁니다.
        return (self.context.get("avatar_map") or {}).get(obj.id, [])[:4]

    def get_members(self, obj):
        """
        방 머리의 참여자별 현지 시각·자리 상태에 쓸 **재료**입니다.

        시각 자체는 안 내려줍니다 — 그 값이 답하는 질문이 "저 사람 지금 몇 시지"
        라 *보는 사람의 지금*이 기준이고, 1분마다 서버를 부를 값이 아닙니다.
        대신 시간대와 나라 이름처럼 **클라이언트가 만들 수 없는 것**을 줍니다.

        팀 구성원 목록으로 대신할 수 없습니다 — 1:1 방은 팀에 안 매달려 있어
        (`team_id`가 비어 있습니다) 뒤질 목록 자체가 없습니다.
        """
        return (self.context.get("member_map") or {}).get(obj.id, [])

    def get_muted(self, obj):
        """**보는 사람 기준**입니다. 한 사람이 껐다고 남의 목록에서도 꺼지면 안 됩니다."""
        return (self.context.get("muted_map") or {}).get(obj.id, False)

    def get_last_message(self, obj):
        return (self.context.get("last_map") or {}).get(obj.id)

    def get_unread_count(self, obj):
        return (self.context.get("unread_map") or {}).get(obj.id, 0)

    def get_has_important(self, obj):
        return bool((self.context.get("important_map") or {}).get(obj.id))


class DailySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyChatSummary
        fields = ("date", "one_line", "my_todos", "schedules", "generated_at")
