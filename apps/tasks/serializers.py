from rest_framework import serializers

from .models import Task


class TaskSerializer(serializers.ModelSerializer):
    assignee_id = serializers.SerializerMethodField()
    assignee_name = serializers.SerializerMethodField()
    source_meeting = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ("id", "project_id", "title", "description", "status", "priority",
                  "assignee_id", "assignee_name", "due_at", "created_by_agent",
                  "source_meeting", "approved_at", "completed_at",
                  "rejected_reason", "created_at", "updated_at")

    def get_assignee_id(self, obj):
        return str(obj.assignee_id) if obj.assignee_id else None

    def get_assignee_name(self, obj):
        return obj.assignee.display_name if obj.assignee_id else None

    def get_source_meeting(self, obj):
        return str(obj.source_meeting_id) if obj.source_meeting_id else None
