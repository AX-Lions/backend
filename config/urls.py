"""
Bordo API 라우팅.

1차 범위는 **홈 화면 + 플로우 화면**입니다. 채팅·태스크·캘린더·MCP·동기화는
2차로 미뤄져 있어 아직 라우팅에 없습니다.
"""
from django.contrib import admin
from django.urls import path

from apps.accounts import views as accounts
from apps.agent import views as agent
from apps.home import views as home
from apps.meetings import views as meetings
from apps.orgs import views as orgs

API = "api/v1"

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── 00. 홈
    path(f"{API}/home", home.home),
    path(f"{API}/me/briefing-dismiss", home.briefing_dismiss),
    path(f"{API}/meetings/<uuid:meeting_id>/favorite", home.meeting_favorite),

    # ── 01. 인증
    path(f"{API}/auth/signup", accounts.signup),
    path(f"{API}/auth/login", accounts.login),
    path(f"{API}/auth/refresh", accounts.refresh),
    path(f"{API}/auth/logout", accounts.logout),
    path(f"{API}/auth/me", accounts.me),

    # ── 02. 사용자
    path(f"{API}/users/me", accounts.me),
    path(f"{API}/users/me/preferences", accounts.preferences),

    # ── 03. 팀
    path(f"{API}/teams", orgs.teams),
    path(f"{API}/teams/join", orgs.join_team),
    path(f"{API}/teams/<uuid:team_id>", orgs.team_detail),
    path(f"{API}/teams/<uuid:team_id>/members", orgs.team_members),
    path(f"{API}/teams/<uuid:team_id>/invite-codes", orgs.invite_codes),

    # ── 03-1. 프로젝트
    path(f"{API}/teams/<uuid:team_id>/projects", orgs.projects),
    path(f"{API}/projects/<uuid:project_id>", orgs.project_detail),
    path(f"{API}/projects/<uuid:project_id>/members", orgs.project_members),
    path(f"{API}/projects/<uuid:project_id>/favorite", orgs.project_favorite),
    path(f"{API}/me/projects/recent", orgs.my_recent_projects),
    path(f"{API}/me/projects/favorites", orgs.my_favorite_projects),

    # ── 06. AI 대리인 설정
    path(f"{API}/me/agent/settings", agent.settings_view),
    path(f"{API}/me/agent/settings/history", agent.settings_history),
    path(f"{API}/me/agent/prompts", agent.prompts),
    path(f"{API}/me/agent/prompts/<uuid:prompt_id>", agent.prompt_detail),

    # ── 09. 회의 · 플로우
    path(f"{API}/projects/<uuid:project_id>/meetings", meetings.meetings),
    path(f"{API}/meetings/<uuid:meeting_id>", meetings.meeting_detail),
    path(f"{API}/meetings/<uuid:meeting_id>/delegate", meetings.delegate),
    path(f"{API}/meetings/<uuid:meeting_id>/flow", meetings.flow),
    path(f"{API}/meetings/<uuid:meeting_id>/indexes", meetings.indexes),
    path(f"{API}/meetings/<uuid:meeting_id>/summary-table", meetings.summary_table),
    path(f"{API}/meetings/<uuid:meeting_id>/context", meetings.context),
    path(f"{API}/meetings/<uuid:meeting_id>/agendas", meetings.agendas),
    path(f"{API}/meetings/<uuid:meeting_id>/agendas/<uuid:agenda_id>",
         meetings.agenda_detail),
    path(f"{API}/flow-edges/<uuid:edge_id>", meetings.flow_edge_detail),
    path(f"{API}/me/flow-filters", meetings.flow_filters),
    path(f"{API}/me/flow-filters/<uuid:preset_id>", meetings.flow_filter_detail),

    # ── 10. AI 대리인
    path(f"{API}/meetings/<uuid:meeting_id>/ai-briefing", meetings.ai_briefing),
    path(f"{API}/meetings/<uuid:meeting_id>/pending-questions",
         meetings.meeting_pending_questions),
    path(f"{API}/me/agent/conversations", agent.conversations),
    path(f"{API}/me/agent/conversations/<uuid:conversation_id>",
         agent.conversation_detail),
    path(f"{API}/me/agent/conversations/<uuid:conversation_id>/messages",
         agent.conversation_messages),
    path(f"{API}/me/pending-questions", agent.my_pending_questions),
    path(f"{API}/pending-questions/<uuid:question_id>/answer", agent.answer_question),
]
