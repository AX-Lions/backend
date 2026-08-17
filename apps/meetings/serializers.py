from rest_framework import serializers

from .models import (Agenda, AiBriefing, BriefingConfirmation, BriefingRequest,
                     FlowEdge, FlowFilterPreset, Meeting, MeetingDocumentRef,
                     MeetingParticipant, MeetingSummary, Utterance)


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
        # `source` · `source_url` 은 작업 모드에서만 채워집니다. 필터 목록
        # (`filter_options.sources`)에는 내려가는데 화살표에는 없어서, 프론트가
        # **어느 화살표가 Github 인지 알 수 없었습니다.** 뱃지도 못 붙이고
        # 산출물로 넘어가지도 못합니다. 회의 모드에서는 빈 문자열입니다.
        fields = ("id", "from_node_id", "to_node_ids", "content_type", "surface",
                  "source", "source_url",
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


class BriefingConfirmationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BriefingConfirmation
        fields = ("id", "title", "body", "edge_id", "agenda_id",
                  "confirmed_at", "occurred_at")


class BriefingRequestSerializer(serializers.ModelSerializer):
    body = serializers.SerializerMethodField()

    class Meta:
        model = BriefingRequest
        fields = ("id", "title", "requester_name", "note", "body", "due_at",
                  "edge_id", "task_id", "accepted_at", "occurred_at")

    def get_body(self, obj):
        """
        카드 두 번째 줄을 서버가 완성해 내려줍니다.

        조각(`requester_name`)만 던지면 클라이언트마다 조사를 붙이게 되고,
        받침 유무를 안 보면 `서재민이가 요청했어요` 같은 문장이 나옵니다.
        기한이 없을 때는 `note` 가 그 자리를 대신합니다.
        """
        return obj.note or f"{obj.requester_name}님이 요청했어요."


class AiBriefingSerializer(serializers.ModelSerializer):
    needs_answer = serializers.SerializerMethodField()
    needs_confirmation = serializers.SerializerMethodField()
    requests_to_me = serializers.SerializerMethodField()
    generated_at = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = AiBriefing
        fields = ("meeting_id", "narrative", "location_chips", "needs_confirmation",
                  "requests_to_me", "needs_answer", "used_answers", "deferred_answers",
                  "settings_version", "generated_at")

    # 세 섹션 모두 뷰가 한 번에 모아 context 로 넘깁니다. 여기서 조회하면
    # 브리핑 하나당 쿼리가 세 번씩 더 나갑니다.
    def get_needs_answer(self, obj):
        return self.context.get("needs_answer", [])

    def get_needs_confirmation(self, obj):
        return self.context.get("needs_confirmation", [])

    def get_requests_to_me(self, obj):
        return self.context.get("requests_to_me", [])
