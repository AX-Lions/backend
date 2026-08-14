"""
캘린더 직렬화.

`local_times` 는 서버가 계산해 내려줍니다. 시간대가 다른 팀이 전제라
클라이언트마다 환산하면 서머타임 경계에서 값이 갈립니다.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework import serializers

from .models import CalendarEvent


def local_times(event, participants):
    """참여자별 현지 시각. 잘못된 timezone 문자열은 조용히 UTC 로 떨굽니다."""
    out = {}
    for p in participants:
        try:
            tz = ZoneInfo(p.user.timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo("UTC")
        out[str(p.user_id)] = event.start_at.astimezone(tz).isoformat()
    return out


class EventSerializer(serializers.ModelSerializer):
    participant_ids = serializers.SerializerMethodField()
    local_times = serializers.SerializerMethodField()
    related_meeting = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = ("id", "project_id", "title", "kind", "status", "start_at", "end_at",
                  "participant_ids", "related_meeting", "discord_notified",
                  "local_times", "confirmed_at", "cancelled_reason",
                  "created_at", "updated_at")

    def get_participant_ids(self, obj):
        rows = (self.context.get("participant_map") or {}).get(obj.id)
        if rows is None:
            rows = list(obj.participants.all())
        return [str(p.user_id) for p in rows]

    def get_local_times(self, obj):
        rows = (self.context.get("participant_map") or {}).get(obj.id)
        if rows is None:
            rows = list(obj.participants.select_related("user"))
        return local_times(obj, rows)

    def get_related_meeting(self, obj):
        return str(obj.related_meeting_id) if obj.related_meeting_id else None
