from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from config.errors import BordoError

from .models import User


class UserSerializer(serializers.ModelSerializer):
    # 활성 MCP 토큰의 발급 시각. null 이면 미발급 — 설정 화면이 `[토큰 발급]` 을 그립니다.
    mcp_token_issued_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "name", "avatar_url", "locale", "timezone",
                  "project_role", "notification", "always_open_briefing",
                  "dismissed_tooltips", "mcp_token_issued_at")
        read_only_fields = ("id", "email", "mcp_token_issued_at")

    def get_mcp_token_issued_at(self, obj):
        from apps.mcp.models import McpToken
        row = McpToken.active_for(obj)
        return row.created_at if row else None


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    name = serializers.CharField(max_length=100)
    locale = serializers.ChoiceField(choices=["ko", "en"], default="ko")
    timezone = serializers.CharField(default="Asia/Seoul")
    project_role = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value):
        if User.all_objects.filter(email__iexact=value).exists():
            raise BordoError("AUTH_EMAIL_DUPLICATED", details={"email": value})
        return value.lower()

    def create(self, validated):
        password = validated.pop("password")
        return User.objects.create_user(password=password, **validated)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs["email"], password=attrs["password"])
        if user is None:
            raise BordoError("AUTH_INVALID_CREDENTIALS")
        if not user.is_active or user.deleted_at:
            raise BordoError("AUTH_ACCOUNT_INACTIVE")
        attrs["user"] = user
        return attrs


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "token_type": "Bearer",
        "expires_in": int(refresh.access_token.lifetime.total_seconds()),
    }
