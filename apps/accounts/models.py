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

    #: 자리에 있는가. 대리인이 나설지 말지의 기준입니다.
    #:
    #: **브라우저에 둘 수 없습니다.** 자리를 비운다는 것은 창을 닫는 일이라,
    #: 로컬에 두면 닫는 순간 그 사람의 대리인이 다시 조용해집니다.
    #:
    #: 두 값뿐입니다. `바쁨` 같은 중간값을 만들면 그 상태에서 대리인이 어떻게
    #: 행동해야 하는지 아무도 정하지 못합니다.
    #: `db_default` 도 함께 둡니다. 마이그레이션 테스트가 옛 상태의 모델로 행을
    #: 만드는데, 그 모델에는 이 칸이 없어 DB 기본값이 없으면 NOT NULL 로 터집니다.
    presence = models.CharField(max_length=8, default="ACTIVE", db_default="ACTIVE",
                                choices=[("ACTIVE", "활동 중"), ("AWAY", "자리 비움")])
    presence_at = models.DateTimeField(null=True, blank=True,
                                       help_text="마지막으로 상태를 바꾼 시각")

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
