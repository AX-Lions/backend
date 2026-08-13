from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "project_role", "timezone", "deleted_at")
    search_fields = ("name", "email")
    list_filter = ("locale", "is_active")
