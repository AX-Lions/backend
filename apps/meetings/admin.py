from django.contrib import admin

from .models import (Agenda, AiBriefing, FlowEdge, Meeting, MeetingDocumentRef,
                     MeetingParticipant, MeetingSummary, Utterance)


class ParticipantInline(admin.TabularInline):
    model = MeetingParticipant
    extra = 0


class AgendaInline(admin.TabularInline):
    model = Agenda
    extra = 0


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("title", "project_name", "status", "scheduled_at", "ended_at")
    list_filter = ("status", "project")
    inlines = [ParticipantInline, AgendaInline]


@admin.register(FlowEdge)
class FlowEdgeAdmin(admin.ModelAdmin):
    list_display = ("meeting", "category", "content_type", "surface",
                    "direction_label", "occurred_at", "opacity")
    list_filter = ("category", "content_type", "surface")


admin.site.register([MeetingSummary, AiBriefing, Utterance, MeetingDocumentRef])
