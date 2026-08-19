from rest_framework import serializers

from .models import Favorite, InviteCode, Project, Team, TeamMember


class TeamSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    categories = serializers.JSONField(source="category_keys", required=False)

    class Meta:
        model = Team
        # `timezone` 을 냅니다. 저장만 하고 안 돌려주면 화면이 방금 고른 값이
        # 들어갔는지 확인할 방법이 없습니다.
        fields = ("id", "name", "description", "timezone", "created_by", "my_role",
                  "categories", "member_count", "created_at")
        read_only_fields = ("id", "created_by", "member_count", "created_at")

    def get_my_role(self, obj):
        return getattr(obj, "_my_role", None)


class TeamMemberSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    timezone = serializers.CharField(source="user.timezone", read_only=True)
    project_role = serializers.CharField(source="user.project_role", read_only=True)
    avatar_url = serializers.CharField(source="user.avatar_url", read_only=True)

    class Meta:
        model = TeamMember
        fields = ("user_id", "name", "avatar_url", "team_role", "project_role",
                  "timezone", "joined_at")


class ProjectSummarySerializer(serializers.ModelSerializer):
    is_favorite = serializers.SerializerMethodField()
    last_opened_at = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ("id", "team_id", "team_name", "name", "progress",
                  "thumbnail_url", "is_favorite", "last_opened_at")

    def get_is_favorite(self, obj):
        return obj.id in (self.context.get("favorite_ids") or set())

    def get_last_opened_at(self, obj):
        return (self.context.get("recent_map") or {}).get(obj.id)


class ProjectSerializer(ProjectSummarySerializer):
    class Meta(ProjectSummarySerializer.Meta):
        fields = ProjectSummarySerializer.Meta.fields + (
            "description", "member_count", "group_chat_room_id",
            "version", "created_at", "updated_at")


class InviteCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = InviteCode
        fields = ("code", "team_id", "default_role", "max_uses", "used_count",
                  "expires_at", "revoked_at")
