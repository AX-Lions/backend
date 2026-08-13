from django.contrib import admin

from .models import InviteCode, Project, ProjectMember, Team, TeamMember


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    extra = 0


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "member_count", "created_by", "deleted_at")
    inlines = [TeamMemberInline]


class ProjectMemberInline(admin.TabularInline):
    model = ProjectMember
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "team_name", "progress", "member_count", "deleted_at")
    list_filter = ("team",)
    inlines = [ProjectMemberInline]


admin.site.register(InviteCode)
