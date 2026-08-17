from rest_framework import serializers

from .models import AgentConversation, AgentPrompt, AgentSettings


class AgentSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentSettings
        fields = ("mention_feasibility", "allow_schedule_change",
                  "allow_midmeeting_question", "disclose_work_plan_thought",
                  "tone", "active_version", "updated_at")
        read_only_fields = ("active_version", "updated_at")


class AgentPromptSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentPrompt
        fields = ("id", "body", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class AgentConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentConversation
        fields = ("id", "title", "last_message_preview", "updated_at")
        read_only_fields = ("id", "last_message_preview", "updated_at")
