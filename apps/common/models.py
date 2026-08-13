"""공통 모델 베이스 — 소프트 삭제, 시각 기록, 낙관적 잠금."""
import uuid

from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)

    def soft_delete(self):
        return self.update(deleted_at=timezone.now())


class AliveManager(models.Manager):
    """기본 매니저. 지워진 행은 애초에 안 보입니다."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class AllManager(models.Manager):
    """복구·감사용. 지워진 행까지 봅니다."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeletable(models.Model):
    """
    소프트 삭제.

    `objects` 는 살아 있는 행만, `all_objects` 는 전부 봅니다.
    조회할 때마다 `deleted_at IS NULL` 을 손으로 붙이면 언젠가 빠뜨리므로
    기본 매니저 쪽에서 막습니다.
    """
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = AliveManager()
    all_objects = AllManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def soft_delete(self, save=True):
        self.deleted_at = timezone.now()
        if save:
            self.save(update_fields=["deleted_at"])
        return self.deleted_at

    def restore(self, save=True):
        self.deleted_at = None
        if save:
            self.save(update_fields=["deleted_at"])


class Versioned(models.Model):
    """낙관적 잠금. 수정할 때마다 1씩 올리고 If-Match 로 검사합니다."""
    version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True
