"""
Bordo API 라우팅.

담당이 갈려 있어 구역을 주석으로 나눠 뒀습니다. 남의 구역에 경로를 끼워 넣으면
머지에서 부딪힙니다 — 자기 구역 끝에 붙이십시오.

* **D(API 서버)** — 채팅 · 현재 상태 · 태스크 · 캘린더 · 문서
* **A(Discord)** — `/internal/v1/...`
* **B(AI · 실시간)** — Agent Run 스트림, `/ws/projects/{id}`
* **MCP** — `/mcp` (개인 AI 클라이언트, `brd_` 토큰) · `/api/v1/me/mcp-token`

동기화(`sync-*`)는 2차라 아직 없습니다.
"""
from django.contrib import admin
from django.urls import path

from apps.accounts import views as accounts
from apps.agent import views as agent
from apps.calendars import views as calendars
from apps.discord import views as discord_internal
from apps.discord import web_views as discord_web
from apps.chat import views as chat
from apps.documents import views as documents
from apps.home import views as home
from apps.mcp import views as mcp
from apps.meetings import prep as meetings_prep
from apps.meetings import views as meetings
from apps.orgs import views as orgs
from apps.states import views as states
from apps.tasks import views as tasks

API = "api/v1"
INTERNAL = "internal/v1"

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── 00. 홈
    path(f"{API}/home", home.home),
    path(f"{API}/me/briefing-dismiss", home.briefing_dismiss),
    path(f"{API}/me/inbox", home.inbox),
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

    # ── 12. Discord 연동 (웹) — 봇이 DM 으로 준 코드를 여기서 입력합니다
    path(f"{API}/me/discord/link", discord_web.me_discord_link),
    path(f"{API}/teams/<uuid:team_id>/discord/link", discord_web.team_discord_link),
    path(f"{API}/teams/<uuid:team_id>/discord/status", discord_web.team_discord_status),

    # ── 09. 회의 · 플로우
    path(f"{API}/projects/<uuid:project_id>/meetings", meetings.meetings),
    path(f"{API}/meetings/<uuid:meeting_id>", meetings.meeting_detail),
    path(f"{API}/meetings/<uuid:meeting_id>/delegate", meetings.delegate),
    # 회의 대리 참석 준비 — 홈의 `회의에 참여하지 않아요` 가 여기로 들어옵니다
    path(f"{API}/meetings/<uuid:meeting_id>/absence", meetings_prep.absence),
    path(f"{API}/meetings/<uuid:meeting_id>/prep", meetings_prep.prep),
    path(f"{API}/meetings/<uuid:meeting_id>/agent-setup", meetings_prep.agent_setup),
    path(f"{API}/debate-points/<uuid:point_id>/stance", meetings_prep.stance),
    path(f"{API}/meetings/<uuid:meeting_id>/flow", meetings.flow),
    # 작업 플로우는 기간이 스코프라 회의 경로를 쓸 수 없습니다 (이슈 #54)
    path(f"{API}/projects/<uuid:project_id>/flow", meetings.project_flow),
    # 플로우 노드(사람)를 눌렀을 때 우측 패널이 부릅니다.
    path(f"{API}/projects/<uuid:project_id>/flow/participants/<uuid:user_id>",
         meetings.flow_participant),
    path(f"{API}/meetings/<uuid:meeting_id>/indexes", meetings.indexes),
    path(f"{API}/meetings/<uuid:meeting_id>/summary-table", meetings.summary_table),
    path(f"{API}/meetings/<uuid:meeting_id>/context", meetings.context),
    path(f"{API}/meetings/<uuid:meeting_id>/agendas", meetings.agendas),
    path(f"{API}/meetings/<uuid:meeting_id>/agendas/<uuid:agenda_id>",
         meetings.agenda_detail),
    path(f"{API}/flow-edges/<uuid:edge_id>", meetings.flow_edge_detail),
    # AI 조회 4단 상세 (이슈 #56)
    path(f"{API}/agent-lookups/<uuid:lookup_id>", agent.agent_lookup_detail),
    path(f"{API}/me/flow-filters", meetings.flow_filters),
    path(f"{API}/me/flow-filters/<uuid:preset_id>", meetings.flow_filter_detail),

    # ── 10. AI 대리인
    path(f"{API}/meetings/<uuid:meeting_id>/ai-briefing", meetings.ai_briefing),
    path(f"{API}/meetings/<uuid:meeting_id>/pending-questions",
         meetings.meeting_pending_questions),
    path(f"{API}/briefing-confirmations/<uuid:confirmation_id>/confirm",
         meetings.briefing_confirmation_confirm),
    path(f"{API}/briefing-requests/<uuid:request_id>/accept",
         meetings.briefing_request_accept),
    path(f"{API}/me/agent/conversations", agent.conversations),
    path(f"{API}/me/agent/conversations/<uuid:conversation_id>",
         agent.conversation_detail),
    path(f"{API}/me/agent/conversations/<uuid:conversation_id>/messages",
         agent.conversation_messages),
    path(f"{API}/me/pending-questions", agent.my_pending_questions),
    path(f"{API}/pending-questions/<uuid:question_id>/answer", agent.answer_question),
    # AI 실행 단계·근거. 대화 메시지가 내려주는 run_id 로 여기까지 옵니다.
    path(f"{API}/agent-runs/<uuid:run_id>", agent.agent_run_detail),
    path(f"{API}/agent-runs/<uuid:run_id>/steps", agent.agent_run_steps),
    path(f"{API}/agent-runs/<uuid:run_id>/evidence", agent.agent_run_evidence),

    # ── 04. 문서
    path(f"{API}/projects/<uuid:project_id>/documents", documents.documents),
    path(f"{API}/documents/<uuid:document_id>", documents.document_detail),
    path(f"{API}/documents/<uuid:document_id>/versions", documents.versions),
    path(f"{API}/documents/<uuid:document_id>/versions/<int:version>",
         documents.version_detail),
    path(f"{API}/documents/<uuid:document_id>/restore", documents.restore),
    path(f"{API}/documents/<uuid:document_id>/restore-deleted",
         documents.restore_deleted),

    # ── 05. 현재 상태 (work · plan · thought)
    path(f"{API}/projects/<uuid:project_id>/work-items", states.work_items),
    path(f"{API}/work-items/<uuid:work_item_id>", states.work_item_detail),
    path(f"{API}/projects/<uuid:project_id>/plans", states.plans),
    path(f"{API}/plans/<uuid:plan_id>", states.plan_detail),
    path(f"{API}/projects/<uuid:project_id>/thoughts", states.thoughts),
    path(f"{API}/thoughts/<uuid:thought_id>", states.thought_detail),
    path(f"{API}/projects/<uuid:project_id>/activity", states.activity),

    # ── 07. 태스크 — status 는 PATCH 가 아니라 전용 엔드포인트로만 움직입니다
    path(f"{API}/projects/<uuid:project_id>/tasks", tasks.tasks),
    path(f"{API}/tasks/<uuid:task_id>", tasks.task_detail),
    path(f"{API}/tasks/<uuid:task_id>/approve", tasks.approve),
    path(f"{API}/tasks/<uuid:task_id>/reject", tasks.reject),
    path(f"{API}/tasks/<uuid:task_id>/start", tasks.start),
    path(f"{API}/tasks/<uuid:task_id>/block", tasks.block),
    path(f"{API}/tasks/<uuid:task_id>/complete", tasks.complete),
    path(f"{API}/tasks/<uuid:task_id>/assign", tasks.assign),
    path(f"{API}/me/tasks", tasks.my_tasks),

    # ── 08. 캘린더
    path(f"{API}/projects/<uuid:project_id>/calendar/events", calendars.events),
    path(f"{API}/calendar/events/<uuid:event_id>", calendars.event_detail),
    path(f"{API}/calendar/events/<uuid:event_id>/confirm", calendars.confirm),
    path(f"{API}/calendar/events/<uuid:event_id>/cancel", calendars.cancel),
    path(f"{API}/calendar/events/<uuid:event_id>/notify-discord",
         calendars.notify_discord),

    # ── 16. 채팅
    path(f"{API}/chat/sidebar", chat.sidebar),
    path(f"{API}/chat/important", chat.important),
    path(f"{API}/chat/candidates", chat.candidates),
    path(f"{API}/chat/rooms", chat.rooms),
    path(f"{API}/chat/rooms/<uuid:room_id>", chat.room_detail),
    path(f"{API}/chat/rooms/<uuid:room_id>/members", chat.room_members),
    path(f"{API}/chat/rooms/<uuid:room_id>/mute", chat.room_mute),
    path(f"{API}/chat/rooms/<uuid:room_id>/members/<uuid:user_id>",
         chat.room_member_detail),
    path(f"{API}/chat/rooms/<uuid:room_id>/messages", chat.messages),
    path(f"{API}/chat/rooms/<uuid:room_id>/attachments", chat.attachments),
    path(f"{API}/chat/rooms/<uuid:room_id>/read", chat.read),
    path(f"{API}/chat/rooms/<uuid:room_id>/active-dates", chat.active_dates),
    path(f"{API}/chat/rooms/<uuid:room_id>/daily-summary", chat.daily_summary),
    path(f"{API}/chat/rooms/<uuid:room_id>/search", chat.search),
    path(f"{API}/chat/messages/<uuid:message_id>", chat.message_detail),
    path(f"{API}/chat/messages/<uuid:message_id>/important", chat.message_important),
    path(f"{API}/chat/messages/<uuid:message_id>/important/confirm",
         chat.message_important_confirm),
    path(f"{API}/chat/attachments/<uuid:attachment_id>", chat.attachment_detail),

    # ── /internal/v1 — Discord Bot 전용 (Service Token)
    #
    # 사람이 아니라 봇이 부릅니다. JWT 를 타지 않으므로 /api/v1 구역과 섞지 않습니다.
    # 이 구역은 파일 끝에 몰아 둡니다 — 다른 담당이 위쪽 구역을 고칠 때 부딪히지
    # 않게 하기 위해서입니다.
    path(f"{INTERNAL}/discord/connect/code", discord_internal.connect_code),
    path(f"{INTERNAL}/teams/current", discord_internal.teams_current),
    path(f"{INTERNAL}/teams/link", discord_internal.teams_link),
    path(f"{INTERNAL}/delegate/on", discord_internal.delegate_on),
    path(f"{INTERNAL}/delegate/off", discord_internal.delegate_off),
    path(f"{INTERNAL}/deputy/ask", discord_internal.deputy_ask),
    # 웹에서 만들어 둔 예정 회의 — /meeting-start 자동완성이 씁니다 (이슈 #89)
    path(f"{INTERNAL}/meetings/scheduled", discord_internal.meetings_scheduled),
    path(f"{INTERNAL}/meetings/start", discord_internal.meeting_start),
    path(f"{INTERNAL}/meetings/end", discord_internal.meeting_end),
    # 봇이 재시작해도 대리 참석자를 되물을 수 있게 (인메모리 set 을 대신합니다)
    path(f"{INTERNAL}/meetings/participants", discord_internal.meeting_participants),
    path(f"{INTERNAL}/meetings/absence", discord_internal.meeting_absence),
    path(f"{INTERNAL}/discord/messages", discord_internal.discord_messages),
    path(f"{INTERNAL}/discord/presence", discord_internal.discord_presence),

    # 발송함 — 서버가 쓰고 봇이 가져갑니다 (이슈 #52)
    path(f"{INTERNAL}/outbox-events", discord_internal.outbox_events),
    path(f"{INTERNAL}/outbox-events/<uuid:event_id>/ack", discord_internal.outbox_ack),
    path(f"{INTERNAL}/outbox-events/<uuid:event_id>/fail", discord_internal.outbox_fail),

    # ── /mcp — 개인 AI 클라이언트 (brd_ 토큰). JWT 를 타지 않으므로 /api/v1 과 섞지 않습니다.
    path("mcp", mcp.mcp),
    path(f"{API}/me/mcp-token", mcp.me_mcp_token),
]
