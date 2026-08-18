from rest_framework import serializers

from .models import AgentConversation, AgentPrompt, AgentSettings


class AgentSettingsSerializer(serializers.ModelSerializer):
    #: 비워 뒀을 때 실제로 불릴 이름. 화면이 `{이름}의 Bordo` 를 직접 만들지
    #: 않게 하려고 완성해서 내려줍니다 — 규칙이 두 군데로 갈리면 서버가 형식을
    #: 바꿨을 때 화면만 옛 형식으로 남습니다.
    agent_display_name = serializers.CharField(source="display_name", read_only=True)

    class Meta:
        model = AgentSettings
        fields = ("mention_feasibility", "allow_schedule_change",
                  "allow_midmeeting_question", "disclose_work_plan_thought",
                  "tone", "agent_name", "agent_display_name",
                  "active_version", "updated_at")
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
