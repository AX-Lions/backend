"""
개인 AI 클라이언트용 토큰.

**원문을 저장하지 않습니다.** 발급 화면에서 한 번 보여주고 해시만 남깁니다.
이 토큰은 개발자 컴퓨터의 설정 파일에 평문으로 앉혀지므로, 서버에까지 평문으로
두면 유출 경로가 두 곳이 됩니다.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.common.models import TimeStamped, UUIDModel

PREFIX = "brd_"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class McpToken(UUIDModel, TimeStamped):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="mcp_tokens")
    token_hash = models.CharField(max_length=64, db_index=True)   # sha256 hexdigest
    prefix = models.CharField(max_length=12, help_text="목록에서 알아보는 용도. `brd_xxxx`")
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mcp_token"
        indexes = [models.Index(fields=["user", "revoked_at"])]

    def __str__(self):
        return f"{self.prefix}… ({self.user_id})"

    # ── 발급 · 폐기 ────────────────────────────────────────
    @classmethod
    def issue(cls, user) -> tuple["McpToken", str]:
        """
        새 토큰을 만들고 **원문을 딱 한 번** 돌려줍니다.

        사용자당 활성 1개입니다. 재발급이 곧 이전 것의 폐기입니다 — 여러 개를
        허용하면 "어느 컴퓨터 것을 지울지" 고르는 화면이 필요해집니다.
        """
        raw = PREFIX + secrets.token_urlsafe(32)
        with transaction.atomic():
            cls.revoke_all(user)
            row = cls.objects.create(user=user, token_hash=_digest(raw), prefix=raw[:12])
        return row, raw

    @classmethod
    def revoke_all(cls, user) -> int:
        return (cls.objects.filter(user=user, revoked_at__isnull=True)
                .update(revoked_at=timezone.now()))

    @classmethod
    def active_for(cls, user) -> "McpToken | None":
        return cls.objects.filter(user=user, revoked_at__isnull=True).first()

    # ── 검증 ──────────────────────────────────────────────
    @classmethod
    def authenticate(cls, raw: str) -> "McpToken | None":
        """
        인덱스로 찾은 뒤 상수 시간 비교를 한 번 더 합니다.

        인덱스 조회 자체는 상수 시간이 아닙니다. 찾은 행의 해시를 다시
        `compare_digest` 로 맞춰 봐야 앞자리부터 맞춰 가는 공격을 막습니다.
        """
        if not raw or not raw.startswith(PREFIX):
            return None
        digest = _digest(raw)
        row = (cls.objects.filter(token_hash=digest, revoked_at__isnull=True)
               .select_related("user").first())
        if row is None or not hmac.compare_digest(row.token_hash, digest):
            return None
        if not row.user.is_active or row.user.deleted_at:
            return None
        return row

    def touch(self) -> None:
        # 요청마다 UPDATE 를 치지 않습니다 — 개인 AI 는 분당 수십 번 부릅니다.
        now = timezone.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < 60:
            return
        type(self).objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now
