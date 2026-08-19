"""
채팅 직렬화.

`is_mine` · `unread_count` · `important_confirmed_at` 은 전부 **보는 사람 기준**
값이라 context 로 받습니다. serializer 안에서 조회하면 목록 하나에 N+1 이 납니다.
"""
from rest_framework import serializers

from apps.common.display import avatar_of

from .models import ChatAttachment, ChatMessage, ChatRoom, DailyChatSummary


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatAttachment
        fields = ("id", "kind", "name", "size_bytes", "mime_type", "url",
                  "status", "expires_at")


class MessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)
    is_mine = serializers.SerializerMethodField()
    important_confirmed_at = serializers.SerializerMethodField()
    read_count = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ("id", "room_id", "sender", "body", "attachments", "sent_at",
                  "is_mine", "is_important", "important_confirmed_at",
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
        return obj.sender_id == self.context.get("me_id")

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

    class Meta:
        model = ChatRoom
        fields = ("id", "type", "title", "team_id", "team_name", "project_id",
                  "project_name", "path_label", "avatar_urls", "last_message",
                  "unread_count", "has_important")

    def get_title(self, obj):
        """
        대리인 방은 **주인의 지금 호칭**으로 부릅니다 (`{이름}의 Bordo`).

        `AI 대리인` 은 화면 어디에도 없는 낱말이라 저장된 제목을 그대로 쓰면
        목록에만 그 낱말이 남습니다. 이름은 개인 설정에서 바뀌므로 조회 시점에
        맞춥니다 — 뷰가 `agent_name_map` 으로 한 번에 모아 넘깁니다.
        """
        named = (self.context.get("agent_name_map") or {}).get(obj.agent_owner_id)
        return named or obj.title

    def get_avatar_urls(self, obj):
        # 단체방 4분할 썸네일. 4개를 넘기면 화면에서 못 씁니다.
        return (self.context.get("avatar_map") or {}).get(obj.id, [])[:4]

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
