from rest_framework import serializers

from .models import PlanItem, ThoughtItem, WorkItem


class WorkItemSerializer(serializers.ModelSerializer):
    owner_id = serializers.UUIDField(source="owner.id", read_only=True)
    owner_name = serializers.CharField(source="owner.display_name", read_only=True)

    class Meta:
        model = WorkItem
        fields = ("id", "project_id", "title", "category", "summary", "status",
                  "progress", "blockers", "owner_id", "owner_name", "visibility",
                  "expected_end_at", "created_at", "updated_at")
        read_only_fields = ("id", "project_id", "owner_id", "created_at", "updated_at")


class PlanItemSerializer(serializers.ModelSerializer):
    owner_id = serializers.UUIDField(source="owner.id", read_only=True)
    owner_name = serializers.CharField(source="owner.display_name", read_only=True)

    class Meta:
        model = PlanItem
        fields = ("id", "project_id", "title", "category", "priority",
                  "planned_start_at", "planned_end_at", "dependencies", "status",
                  "owner_id", "owner_name", "visibility", "created_at", "updated_at")
        read_only_fields = ("id", "project_id", "owner_id", "created_at", "updated_at")


class ThoughtItemSerializer(serializers.ModelSerializer):
    owner_id = serializers.UUIDField(source="owner.id", read_only=True)
    owner_name = serializers.CharField(source="owner.display_name", read_only=True)

    class Meta:
        model = ThoughtItem
        fields = ("id", "project_id", "topic", "content", "category", "confidence",
                  "requires_discussion", "status", "owner_id", "owner_name",
                  "visibility", "promoted_from_question_id", "created_at", "updated_at")
        read_only_fields = ("id", "project_id", "owner_id", "created_at", "updated_at")
