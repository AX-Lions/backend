"""
Discord 연동에 필요한 연결 정보.

봇은 `discord_user_id` 와 `guild_id` 로 말하는데, 서버는 그것만으로 사람과 팀을
알 수 없습니다. 그 사이를 잇는 두 모델입니다.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStamped, UUIDModel


class GuildLink(TimeStamped):
    """
    Discord 서버 하나가 팀 하나에 묶입니다.

    `guild_id` 를 기본키로 둡니다. 봇이 늘 이 값으로 물어보고, 한 서버가 두 팀에
    묶이면 어느 팀의 회의인지 정할 수 없습니다.
    """
    guild_id = models.CharField(max_length=40, primary_key=True)
    team = models.ForeignKey("orgs.Team", on_delete=models.CASCADE,
                             related_name="guild_links")
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                  on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table = "discord_guild_link"

    def __str__(self):
        return f"{self.guild_id} → {self.team_id}"


class LinkCode(UUIDModel, TimeStamped):
    """
    계정 연결 코드.

    봇이 DM 으로 코드를 주고, 사용자가 웹에서 입력하면 두 계정이 이어집니다.
    **코드를 봇이 만들지 않고 서버가 만듭니다** — 봇이 만들면 서버가 그 코드를
    믿을 근거가 없습니다.

    짧게 만료시킵니다. DM 이 남아 있는 채널을 누가 보면 그대로 계정이 이어집니다.
    """
    TTL_MINUTES = 10

    code = models.CharField(max_length=12, unique=True, db_index=True)
    discord_user_id = models.CharField(max_length=40, db_index=True)
    guild_id = models.CharField(max_length=40, blank=True, default="")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             null=True, blank=True, related_name="discord_link_codes")

    class Meta:
        db_table = "discord_link_code"
        indexes = [models.Index(fields=["discord_user_id", "used_at"])]

    @property
    def alive(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
