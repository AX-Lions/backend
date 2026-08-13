"""
멱등성 처리.

같은 `Idempotency-Key` 로 다시 들어오면 처음 결과를 그대로 돌려줍니다.
모바일에서 응답을 못 받고 재시도했을 때 태스크가 두 개 생기는 사고를 막습니다.
"""
import hashlib
import json

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from config.errors import BordoError


class IdempotencyRecord(models.Model):
    scope = models.CharField(max_length=200)          # "POST /api/v1/projects/{id}/tasks"
    key = models.CharField(max_length=200)
    user_id = models.UUIDField(null=True)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_record"
        constraints = [
            models.UniqueConstraint(fields=["scope", "key", "user_id"],
                                    name="uq_idem_scope_key_user"),
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.scope} {self.key}"


def _hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def replay_or_none(request, scope):
    """이미 처리한 요청이면 저장해둔 응답을, 아니면 None 을 돌려줍니다."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    body_hash = _hash(getattr(request, "data", None) or {})
    user_id = getattr(request.user, "id", None)
    rec = IdempotencyRecord.objects.filter(scope=scope, key=key, user_id=user_id).first()
    if not rec:
        return None
    if rec.request_hash != body_hash:
        # 같은 키로 다른 내용을 보냈다 — 클라이언트 버그일 가능성이 큽니다.
        raise BordoError("DUPLICATE_EVENT",
                         "같은 Idempotency-Key 로 다른 요청이 들어왔습니다.",
                         details={"idempotency_key": key})
    return rec


def remember(request, scope, status_code, payload):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return
    try:
        with transaction.atomic():
            IdempotencyRecord.objects.create(
                scope=scope, key=key,
                user_id=getattr(request.user, "id", None),
                request_hash=_hash(getattr(request, "data", None) or {}),
                status_code=status_code,
                response_snapshot=payload,
            )
    except IntegrityError:
        pass  # 동시에 두 번 들어온 경우. 먼저 쓴 쪽이 이깁니다.
