from django.contrib import admin

from .models import (AgentConversation, AgentPrompt, AgentRun, AgentSettings,
                     AgentSettingsVersion, PendingQuestion)

admin.site.register([AgentSettings, AgentSettingsVersion, AgentPrompt,
                     AgentConversation, AgentRun, PendingQuestion])
