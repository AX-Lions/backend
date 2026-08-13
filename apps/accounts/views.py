from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from config.errors import BordoError

from .serializers import (LoginSerializer, SignupSerializer, UserSerializer, issue_tokens)


@api_view(["POST"])
@permission_classes([AllowAny])
def signup(request):
    s = SignupSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    user = s.save()
    return Response(UserSerializer(user).data, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    s = LoginSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    user = s.validated_data["user"]
    body = issue_tokens(user)
    body["user"] = UserSerializer(user).data
    return Response(body)


@api_view(["POST"])
@permission_classes([AllowAny])
def refresh(request):
    token = request.data.get("refresh_token")
    if not token:
        raise BordoError("VALIDATION_ERROR", "refresh_token 이 필요합니다.")
    try:
        rt = RefreshToken(token)
    except TokenError:
        raise BordoError("AUTH_INVALID_TOKEN", "refresh token 이 유효하지 않습니다.")
    return Response({
        "access_token": str(rt.access_token),
        "token_type": "Bearer",
        "expires_in": int(rt.access_token.lifetime.total_seconds()),
    })


@api_view(["POST"])
def logout(request):
    # 블랙리스트를 켜지 않은 상태라 서버는 기록만 남기고, 실제 폐기는 클라이언트가 합니다.
    return Response(status=204)


@api_view(["GET", "PATCH"])
def me(request):
    if request.method == "GET":
        return Response(UserSerializer(request.user).data)
    s = UserSerializer(request.user, data=request.data, partial=True)
    s.is_valid(raise_exception=True)
    s.save()
    return Response(s.data)


@api_view(["PATCH"])
def preferences(request):
    """환경설정. 홈 팝업 체크박스와 온보딩 툴팁 dismiss 상태가 여기 들어옵니다."""
    user = request.user
    changed = []
    for field in ("notification", "always_open_briefing", "dismissed_tooltips"):
        if field in request.data:
            setattr(user, field, request.data[field])
            changed.append(field)
    if changed:
        user.save(update_fields=changed + ["updated_at"])
    return Response({
        "notification": user.notification,
        "always_open_briefing": user.always_open_briefing,
        "dismissed_tooltips": user.dismissed_tooltips,
        "updated_at": timezone.now(),
    })
