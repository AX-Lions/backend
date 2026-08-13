from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("이메일은 필수입니다.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(UUIDModel, TimeStamped, SoftDeletable, AbstractBaseUser, PermissionsMixin):
    """
    계정.

    `timezone` 은 표시용이 아닙니다 — 회의 슬롯 계산과 Discord 공지의 현지 시각
    표기에 직접 쓰입니다.
    """
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100)
    avatar_url = models.URLField(blank=True, default="")
    locale = models.CharField(max_length=8, default="ko",
                              choices=[("ko", "한국어"), ("en", "English")])
    timezone = models.CharField(max_length=64, default="Asia/Seoul")
    project_role = models.CharField(max_length=40, blank=True, default="",
                                    help_text="backend / frontend / design 등 직무")

    discord_user_id = models.CharField(max_length=40, blank=True, default="", db_index=True)

    # 환경설정 — 항목이 자주 늘어서 JSON 으로 둡니다.
    notification = models.JSONField(default=dict, blank=True)
    always_open_briefing = models.BooleanField(
        default=False, help_text="홈 팝업의 '항상 브리핑 보러가기'")
    dismissed_tooltips = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    objects = UserManager.from_queryset(models.QuerySet)()
    all_objects = models.Manager()

    class Meta:
        db_table = "app_user"

    def __str__(self):
        return f"{self.name} <{self.email}>"

    @property
    def display_name(self):
        return "(탈퇴한 사용자)" if self.deleted_at else self.name
