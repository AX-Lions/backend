from rest_framework import serializers

from .models import (Agenda, AiBriefing, FlowEdge, FlowFilterPreset, Meeting,
                     MeetingDocumentRef, MeetingParticipant, MeetingSummary, Utterance)


class MeetingSerializer(serializers.ModelSerializer):
    participants = serializers.SerializerMethodField()

    class Meta:
        model = Meeting
        fields = ("id", "project_id", "project_name", "title", "status",
                  "scheduled_at", "duration_min", "discord_channel_id",
                  "started_at", "ended_at", "participants", "version",
                  "created_at", "updated_at")

    def get_participants(self, obj):
        return [{"user_id": str(p.user_id), "name": p.user_name,
                 "attendance": p.attendance, "delegated": p.delegated}
                for p in obj.participants.all()]


class AgendaSerializer(serializers.ModelSerializer):
    related_edge_ids = serializers.SerializerMethodField()

    class Meta:
        model = Agenda
        fields = ("id", "title", "sort_order", "category", "owner_id", "status",
                  "content", "direction_label", "created_by_agent", "related_edge_ids")

    def get_related_edge_ids(self, obj):
        return [str(i) for i in (self.context.get("edge_map") or {}).get(obj.id, [])]


class FlowEdgeSerializer(serializers.ModelSerializer):
    from_node_id = serializers.SerializerMethodField()
    to_node_ids = serializers.SerializerMethodField()
    participant_avatar_urls = serializers.SerializerMethodField()

    class Meta:
        model = FlowEdge
        fields = ("id", "from_node_id", "to_node_ids", "content_type", "surface",
                  "label", "direction_label", "participant_avatar_urls",
                  "extra_participant_count", "document_id", "agenda_id",
                  "occurred_at", "opacity")

    def get_from_node_id(self, obj):
        return (obj.from_node or {}).get("id")

    def get_to_node_ids(self, obj):
        return [n.get("id") for n in (obj.to_nodes or [])]

    def get_participant_avatar_urls(self, obj):
        return [n.get("avatar_url") for n in (obj.to_nodes or []) if n.get("avatar_url")]


class UtteranceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utterance
        fields = ("participant_id", "participant_name", "body", "spoken_at")


class MeetingSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingSummary
        fields = ("discovered_issues", "changes", "next_plans", "one_line", "main_opinions")


class DocumentRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingDocumentRef
        fields = ("id", "title", "visibility", "sections", "delivery_context",
                  "direction_label")


class FlowFilterPresetSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlowFilterPreset
        fields = ("id", "name", "participant_ids", "content_types", "surfaces",
                  "since_minutes", "created_at")
        read_only_fields = ("id", "created_at")


class AiBriefingSerializer(serializers.ModelSerializer):
    needs_answer = serializers.SerializerMethodField()

    class Meta:
        model = AiBriefing
        fields = ("meeting_id", "narrative", "used_answers", "deferred_answers",
                  "needs_answer", "settings_version")

    def get_needs_answer(self, obj):
        return self.context.get("needs_answer", [])
