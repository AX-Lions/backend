from rest_framework import serializers

from .models import AgentConversation, AgentPrompt, AgentSettings


class AgentSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentSettings
        fields = ("mention_feasibility", "allow_schedule_change",
                  "allow_midmeeting_question",
                  "disclose_work", "disclose_plan", "disclose_thought",
                  # 옛 이름. 셋 중 하나라도 켜져 있으면 참인 파생값이라 읽기 전용입니다 —
                  # 쓰기까지 열어 두면 화면이 스위치 셋과 이 값을 함께 보냈을 때
                  # 어느 쪽이 이기는지가 요청 순서에 달립니다.
                  "disclose_work_plan_thought",
                  "tone", "agent_name", "active_version", "updated_at")
        read_only_fields = ("active_version", "updated_at",
                            "disclose_work_plan_thought")


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
