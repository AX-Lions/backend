"""
팀 · 프로젝트.

팀은 조직 단위, 프로젝트는 작업 단위입니다.
문서·회의·작업이 매달리는 곳은 프로젝트입니다.
"""
from django.conf import settings
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel, Versioned


class TeamRole(models.TextChoices):
    OWNER = "OWNER", "소유자"
    ADMIN = "ADMIN", "관리자"
    MEMBER = "MEMBER", "멤버"


class Team(UUIDModel, TimeStamped, SoftDeletable):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="created_teams")

    # 카테고리는 목록 조회에 늘 따라붙는데 개수가 적어 JSON 으로 둡니다.
    # 문서 수 같은 집계가 필요해지면 Category 테이블로 분리하십시오.
    category_keys = models.JSONField(default=list, blank=True)

    member_count = models.PositiveIntegerField(default=0)   # 카운터

    class Meta:
        db_table = "team"

    def __str__(self):
        return self.name


class TeamMember(TimeStamped):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="team_memberships")
    team_role = models.CharField(max_length=10, choices=TeamRole.choices,
                                 default=TeamRole.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "team_member"
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="uq_team_member"),
            # 팀당 OWNER 는 항상 1명.
            models.UniqueConstraint(fields=["team"], condition=models.Q(team_role="OWNER"),
                                    name="uq_team_single_owner"),
        ]
        indexes = [models.Index(fields=["user", "team"])]

    def __str__(self):
        return f"{self.team_id}:{self.user_id}({self.team_role})"


class Project(UUIDModel, TimeStamped, SoftDeletable, Versioned):
    """
    작업 단위.

    `team_name` 은 비정규화입니다 — 채팅방 헤더의 `팀 A(프로젝트b)` 와
    사이드바 트리를 그릴 때 팀을 매번 조인하지 않기 위해서입니다.
    """
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="projects")
    team_name = models.CharField(max_length=120)            # 비정규화
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    progress = models.PositiveSmallIntegerField(
        default=0, help_text="0~100. 태스크 완료율에서 파생되며 서버가 계산합니다.")
    thumbnail_url = models.URLField(blank=True, default="")
    member_count = models.PositiveIntegerField(default=0)   # 카운터
    group_chat_room_id = models.UUIDField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="created_projects")

    class Meta:
        db_table = "project"
        indexes = [models.Index(fields=["team", "deleted_at"])]

    def __str__(self):
        return f"{self.team_name} / {self.name}"

    def save(self, *args, **kwargs):
        if self.team_id and not self.team_name:
            self.team_name = self.team.name
        super().save(*args, **kwargs)


class ProjectMember(TimeStamped):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="project_memberships")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_member"
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uq_project_member"),
        ]
        indexes = [models.Index(fields=["user", "project"])]


class InviteCode(TimeStamped):
    code = models.CharField(max_length=32, primary_key=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invite_codes")
    default_role = models.CharField(max_length=10, choices=TeamRole.choices,
                                    default=TeamRole.MEMBER)
    max_uses = models.PositiveIntegerField(default=10)
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invite_code"

    @property
    def is_usable(self):
        from django.utils import timezone
        return (self.revoked_at is None
                and self.expires_at > timezone.now()
                and self.used_count < self.max_uses)


class Favorite(models.Model):
    """프로젝트·회의 공용 즐겨찾기. 대상이 둘뿐이라 다형 키로 둡니다."""
    class Target(models.TextChoices):
        PROJECT = "PROJECT", "프로젝트"
        MEETING = "MEETING", "회의"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="favorites")
    target_type = models.CharField(max_length=10, choices=Target.choices)
    target_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_favorite"
        constraints = [
            models.UniqueConstraint(fields=["user", "target_type", "target_id"],
                                    name="uq_favorite"),
        ]
        indexes = [models.Index(fields=["user", "target_type"])]


class RecentProject(models.Model):
    """사이드바 `최근 항목`. 팀을 가로지릅니다."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="recent_projects")
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    opened_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_recent_project"
        constraints = [
            models.UniqueConstraint(fields=["user", "project"], name="uq_recent_project"),
        ]
        indexes = [models.Index(fields=["user", "-opened_at"])]
