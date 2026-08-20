"""
시연(영상 촬영) 전용 시드.

    python manage.py seed_showcase [--reset]

## `seed_demo` 와 무엇이 다른가

`seed_demo` 는 **개발·검증용**입니다. 화면마다 빈 자리가 없게 하려고 회의 8개,
채팅방 13개, 브리핑 5개를 깔아 두는데, 시연 화면으로는 과합니다 — 보는 사람이
"어느 회의가 지금 얘기하는 그 회의인지" 를 못 찾습니다.

이 명령은 그 반대입니다. **하나의 이야기가 흐르는 최소한만** 깔고, 나머지는
시연 중에 직접 만듭니다.

    회의를 잡는다 → 한 명이 불참을 등록하고 대리 참석을 준비한다
    → Discord 에서 회의를 한다 → 회의가 끝난다
    → 불참자가 돌아와 브리핑을 보고, 대리인이 유보한 질문에 답한다

**회의를 미리 만들지 않습니다.** 위 흐름의 첫 장면이 「회의를 잡는 화면」이라
시드가 미리 만들어 두면 그 장면을 찍을 수 없습니다. 시드가 까는 것은 그
회의가 열리기까지 **이미 있었어야 할 것들**입니다 — 팀·사람·프로젝트,
대리인이 대신 답할 근거(작업·계획·생각·문서·일정), Discord 연결.

## 개발용 데이터와 섞이지 않습니다

계정 도메인이 `@demo.bordo.dev` 라 `seed_demo --reset` 의
`email__endswith="@bordo.dev"` 에 걸리지 않습니다. 반대로 이 명령의
`--reset` 도 `@bordo.dev` 계정을 건드리지 않습니다. 한쪽을 다시 깔아도
다른 쪽이 사라지지 않아야 촬영 중에 개발용 검증 경로를 잃지 않습니다.
"""
import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentPrompt, AgentSettings
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

PASSWORD = "Bordo!2026"
DOMAIN = "@demo.bordo.dev"

#: 시연 팀은 셋입니다. 다섯을 넘기면 플로우 노드가 화면에서 겹치고, 둘이면
#: "한 명이 빠져도 회의가 굴러간다" 는 이 서비스의 전제가 안 보입니다.
#:
#: `(키, 이메일, 이름, 직무, 시간대, 아바타)`
#:
#: `discord_user_id` 는 여기 없습니다 — 실제 사람의 Discord 계정 번호라
#: 상수로 박아 두면 **서버에서 이 파일을 고쳐야** 합니다. 배포한 코드를
#: 서버에서 손대면 다음 배포가 `git reset --hard` 로 되돌려 버립니다.
#: `--discord` 나 환경변수로 받습니다.
#:
#: ## 시간대를 셋으로 벌립니다
#:
#: 영상 첫 장면이 국기 셋(🇺🇸 🇻🇳 🇰🇷)입니다. 거기서 시차를 말해 놓고 화면에
#: 찍히는 시각이 전부 한국 시각이면 **인트로가 그림일 뿐**이 됩니다. 참여자별
#: 현지 시각은 서버가 사람마다 환산해 내려주는 값이라(회의 카드·채팅방 머리)
#: 시간대를 벌려 두면 그 계산이 화면에서 그대로 보입니다.
#:
#: 배치는 문제 서술과 맞춥니다 — **자리를 비우는 사람이 미국**입니다.
#: "미국 개발자 퇴근 → 한국·베트남 회의" 가 곧 이 시연의 상황입니다.
#:
#: **회의를 만드는 진행자만 한국**입니다. 홈의 「오늘 일정」은 보는 사람의
#: 시간대로 하루를 자르는데, 회의를 만드는 사람이 다른 시간대에 있으면
#: 촬영자가 "오늘" 을 잘못 잡기 쉽습니다.
PEOPLE = [
    ("lead", f"taehyun{DOMAIN}", "강태현", "backend",
     "Asia/Seoul", "/flowchart/profile-1.jpeg"),
    ("away", f"emily{DOMAIN}", "에밀리 한", "design",
     "America/Los_Angeles", "/flowchart/profile-2.jpeg"),
    ("member", f"minh{DOMAIN}", "응우옌 민", "frontend",
     "Asia/Ho_Chi_Minh", "/flowchart/profile-3.jpeg"),
]

TEAM_NAME = "Bordo 시연팀"
PROJECT_NAME = "글로벌 회의 도구"


class Command(BaseCommand):
    help = "영상 촬영용 최소 데이터셋을 만듭니다. 회의는 시연 중에 직접 만듭니다."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="기존 시연 데이터를 지웁니다. 개발용(@bordo.dev)은 안 건드립니다.")
        parser.add_argument(
            "--guild", default="",
            help="촬영에 쓸 Discord 서버 id. 없으면 SHOWCASE_GUILD_ID 환경변수를 봅니다. "
                 "둘 다 없으면 Discord 연결을 만들지 않습니다.")
        parser.add_argument(
            "--discord", nargs="*", default=[], metavar="KEY=ID",
            help="Discord 계정 번호. `lead=123 away=456 member=789`. "
                 "없으면 SHOWCASE_DISCORD_LEAD 등 환경변수를 봅니다.")

    @transaction.atomic
    def handle(self, *args, **opts):
        self.guild_id = opts["guild"] or os.environ.get("SHOWCASE_GUILD_ID", "")
        self.discord_ids = self._discord_ids(opts["discord"])
        if opts["reset"]:
            self._reset()

        now = timezone.now()
        people = self._users()
        lead, away, member = people["lead"], people["away"], people["member"]

        team, project = self._team_and_project(people, lead)
        self._integrations(team, people, lead)
        self._agent_setup(away, lead)
        self._evidence(project, people, now)
        self._chat(project, people, now)
        self._past_meeting(project, people, now)

        self._report(team, project, people)

    # ═══════════════════════════════════════════ 리셋

    def _reset(self):
        """
        시연 계정과 그들이 만든 것만 지웁니다.

        팀을 사람보다 **먼저** 지웁니다 — `Team.created_by` 가 PROTECT 라
        만든 사람만 사라지고 만든 것이 남는 상황을 모델이 거부합니다.
        """
        from apps.chat.models import ChatRoom, RoomType

        ChatRoom.objects.filter(
            type=RoomType.DIRECT,
            memberships__user__email__endswith=DOMAIN).distinct().delete()
        Team.all_objects.filter(created_by__email__endswith=DOMAIN).delete()
        User.all_objects.filter(email__endswith=DOMAIN).delete()
        self.stdout.write("기존 시연 데이터를 지웠습니다.")

    # ═══════════════════════════════════════════ 사람 · 팀 · 프로젝트

    def _users(self):
        """
        시연 계정 셋.

        시간대는 `PEOPLE` 에 적힌 대로 셋으로 갈립니다 — 한국·미국·베트남.
        영상 첫 장면의 국기 셋과 화면에 찍히는 시각이 어긋나면 안 됩니다.
        """
        people = {}
        for key, email, name, role, tz, avatar in PEOPLE:
            u = User.all_objects.filter(email=email).first()
            if not u:
                u = User.objects.create_user(email=email, password=PASSWORD, name=name,
                                             project_role=role, timezone=tz)
            u.avatar_url = avatar
            u.timezone = tz
            # 값을 안 받았으면 **비워 둡니다.** 가짜 번호를 채워 두면 봇이
            # 보낸 발언의 주인을 못 찾고, 그때 서버는 조용히 아무도
            # 대리하지 않습니다 - 시연 중에 알아채기 가장 어려운 실패입니다.
            u.discord_user_id = self.discord_ids.get(key, "")
            # 시작은 전원 `ACTIVE` 입니다. 자리를 비우는 것은 시연 중에 하는
            # 행위라, 미리 AWAY 로 두면 그 장면이 이미 지나간 것이 됩니다.
            u.presence = "ACTIVE"
            u.save(update_fields=["avatar_url", "timezone", "discord_user_id",
                                  "presence"])
            AgentSettings.objects.get_or_create(user=u)
            people[key] = u
        return people

    def _team_and_project(self, people, lead):
        """팀 하나, 프로젝트 하나. 사이드바에 고를 것이 없어야 헤매지 않습니다."""
        team, _ = Team.objects.get_or_create(
            name=TEAM_NAME,
            defaults={"created_by": lead, "description": "Bordo 시연용 팀",
                      "category_keys": ["backend", "frontend", "design"],
                      "timezone": "Asia/Seoul"})
        for key, _e, name, _r, _tz, _a in PEOPLE:
            TeamMember.objects.get_or_create(
                team=team, user=people[key],
                defaults={"team_role": TeamRole.OWNER if key == "lead"
                          else TeamRole.MEMBER})
        team.member_count = TeamMember.objects.filter(team=team).count()
        team.save(update_fields=["member_count"])

        project, _ = Project.objects.get_or_create(
            team=team, name=PROJECT_NAME,
            defaults={"team_name": team.name, "created_by": lead, "progress": 45,
                      "description": "시간대가 다른 팀이 회의를 이어가게 하는 도구"})
        for key in people:
            ProjectMember.objects.get_or_create(project=project, user=people[key])
        project.member_count = ProjectMember.objects.filter(project=project).count()
        project.save(update_fields=["member_count"])
        return team, project

    # ═══════════════════════════════════════════ Discord 연결

    def _discord_ids(self, pairs):
        """
        `--discord lead=123 away=456` 과 `SHOWCASE_DISCORD_LEAD` 를 모읍니다.

        인자가 환경변수를 이깁니다 — 서버 `.env` 에 촬영용 값을 넣어 두고,
        한 번만 다른 계정으로 돌려 볼 때 인자로 덮어쓸 수 있어야 합니다.
        """
        ids = {}
        for key, *_rest in PEOPLE:
            env = os.environ.get(f"SHOWCASE_DISCORD_{key.upper()}", "").strip()
            if env:
                ids[key] = env
        for pair in pairs:
            if "=" not in pair:
                raise ValueError(f"--discord 는 `키=번호` 모양입니다: {pair}")
            key, value = pair.split("=", 1)
            key = key.strip()
            if key not in {k for k, *_ in PEOPLE}:
                raise ValueError(f"모르는 키입니다: {key} (lead · away · member)")
            ids[key] = value.strip()
        return ids

    def _integrations(self, team, people, lead):
        """
        Discord 서버 ↔ 팀 연결.

        이게 없으면 봇이 보낸 것을 서버가 어느 팀의 일로 받을지 정하지
        못합니다. 시연 중에 연결 코드를 입력하는 장면까지 찍을 수도 있지만,
        그 장면은 봇 DM 이 필요해 촬영이 길어집니다 - **이미 연결된
        상태**에서 시작합니다.

        **값이 없으면 만들지 않습니다.** 아무 번호나 넣어 두면 화면은
        `연결됨` 이라고 말하는데 봇이 보낸 이벤트는 어느 팀에도 안 붙습니다.
        연결이 없는 것보다 나쁩니다 - 촬영 중에는 원인을 못 찾습니다.
        """
        from apps.discord.models import GuildLink

        if not self.guild_id:
            return
        GuildLink.objects.get_or_create(
            guild_id=self.guild_id, defaults={"team": team, "linked_by": lead})

    # ═══════════════════════════════════════════ 대리인 설정

    def _agent_setup(self, away, lead):
        """
        불참자(에밀리 한)의 대리인 설정.

        **`allow_schedule_change=False` 가 핵심입니다.** 회의에서 일정을 미루자는
        말이 나오면 대리인이 답하지 않고 유보하고, 그 유보가 `PendingQuestion`
        으로 남습니다. 돌아온 사람이 답해야 하는 질문이 바로 그것입니다 —
        이걸 켜 두면 대리인이 알아서 답해 버려서, 시연의 마지막 장면(사람이
        최종 승인한다)이 통째로 사라집니다.

        `disclose_thought=False` 도 같이 끕니다. 확정되지 않은 생각까지 대리인이
        옮기면 "AI 가 내 말을 지어냈다" 로 보입니다.
        """
        st = AgentSettings.objects.get(user=away)
        st.mention_feasibility = True
        st.allow_schedule_change = False        # ← 유보가 생기는 자리
        st.allow_midmeeting_question = True
        st.disclose_work = True
        st.disclose_plan = True
        st.disclose_thought = False
        st.tone = "FORMAL"
        st.agent_name = ""                      # 비우면 `에밀리 한의 Bordo`
        st.active_version = 2
        st.save()

        for body in [
            "디자인 시안 마감은 8월 18일입니다. 이 날짜는 확정된 것으로 답하십시오.",
            "일정을 미루는 결정은 제가 하지 않습니다. 확인이 필요하다고 답하고 "
            "질문으로 남기십시오.",
        ]:
            AgentPrompt.objects.get_or_create(user=away, body=body)

        # 진행자 대리인은 반대로 열어 둡니다 — 설정이 사람마다 다르다는 것이
        # 화면에서 보여야 「대리인은 사람에게 붙는다」 가 전달됩니다.
        lead_st = AgentSettings.objects.get(user=lead)
        lead_st.allow_midmeeting_question = True
        lead_st.tone = "FRIENDLY"
        lead_st.save()

    # ═══════════════════════════════════════════ 대리인이 답할 근거

    def _evidence(self, project, people, now):
        """
        대리인이 회의에서 인용할 재료.

        이게 비면 대리인은 회의 내내 "확인이 필요합니다" 만 반복합니다. 시연에서
        보여줘야 하는 것은 **근거를 들고 답하는 장면**이라, 불참자(에밀리 한)의
        작업·계획·생각을 먼저 깔아 둡니다.
        """
        self._states(project, people, now)
        self._documents(project, people)
        self._tasks_and_calendar(project, people, now)

    def _states(self, project, people, now):
        """작업 · 계획 · 생각 — 사람당 최소 하나씩."""
        from apps.states.models import (PlanItem, Priority, ThoughtItem, Visibility,
                                        WorkItem, WorkStatus)

        lead, away, member = people["lead"], people["away"], people["member"]

        WorkItem.objects.get_or_create(
            project=project, owner=away, title="회의 상세 화면 최종 시안",
            defaults={"category": "design", "status": WorkStatus.IN_PROGRESS,
                      "progress": 70,
                      "summary": "플로우 그래프와 우측 패널 전환까지 그렸고, "
                                 "브리핑 패널만 남았습니다.",
                      "expected_end_at": now + timedelta(days=2)})
        WorkItem.objects.get_or_create(
            project=project, owner=lead, title="회의 API 명세 정리",
            defaults={"category": "backend", "status": WorkStatus.IN_PROGRESS,
                      "progress": 55,
                      "summary": "회의·플로우 엔드포인트 응답 구조를 다시 맞추는 중입니다."})
        WorkItem.objects.get_or_create(
            project=project, owner=member, title="플로우 화면 컴포넌트 연결",
            defaults={"category": "frontend", "status": WorkStatus.BLOCKED,
                      "progress": 30,
                      "summary": "명세가 확정되기 전까지 목 데이터로 붙여 두었습니다.",
                      "blockers": ["회의 API 응답 구조 확정 대기"]})

        PlanItem.objects.get_or_create(
            project=project, owner=away, title="디자인 시안 8/18 확정",
            defaults={"category": "design", "priority": Priority.P1,
                      "status": WorkStatus.IN_PROGRESS,
                      "planned_end_at": now + timedelta(days=3)})
        PlanItem.objects.get_or_create(
            project=project, owner=lead, title="명세 확정 후 프론트 연동 지원",
            defaults={"category": "backend", "priority": Priority.P2,
                      "status": WorkStatus.TODO,
                      "planned_start_at": now + timedelta(days=3)})
        # 응우옌 민 몫. **대리인을 둘 돌릴 때 필요합니다** — 사람이 하나뿐인 촬영에서는
        # 강태현이 두 부재자의 대리인에게 각각 묻습니다. 응우옌 민에게 근거가 작업
        # 하나뿐이면 그 대리인은 진행률만 말하고 나머지는 유보로 흘러, 화면에
        # 「관련 기록을 찾지 못해」가 두 번 뜹니다.
        PlanItem.objects.get_or_create(
            project=project, owner=member, title="명세 확정 다음 날 연동 시작",
            defaults={"category": "frontend", "priority": Priority.P1,
                      "status": WorkStatus.TODO,
                      "planned_start_at": now + timedelta(days=1),
                      "planned_end_at": now + timedelta(days=4)})

        # 확신이 높고 논의가 필요한 생각 — 회의 전 준비 화면에서 논쟁점 예측의
        # 재료가 됩니다.
        ThoughtItem.objects.get_or_create(
            project=project, owner=away, topic="마감을 미루면 QA 기간이 먼저 줄어듭니다",
            defaults={"category": "design", "confidence": 0.8,
                      "requires_discussion": True,
                      "content": "일정을 1주 미루면 QA 가 3일로 줄어드는 구조라, "
                                 "미루는 쪽이 오히려 위험할 수 있습니다."})
        # 비공개 생각 — 대리인이 이걸 회의에서 말하지 않는다는 것을 보여주는 자리.
        ThoughtItem.objects.get_or_create(
            project=project, owner=away, topic="이번 주 개인 일정이 빠듯합니다",
            defaults={"confidence": 0.5, "visibility": Visibility.PRIVATE,
                      "content": "회의에 못 들어가는 이유. 회의에서 말할 내용은 아닙니다."})

    def _documents(self, project, people):
        """
        문서 둘. 하나는 회의 주제(API 명세), 하나는 불참자의 산출물입니다.

        `delivery_context` 를 채워 둔 문서가 하나 있어야 작업 플로우에 `공유`
        화살표가 그려집니다 — 링크 도메인에서 출처(Github/Figma)를 알아내므로
        둘을 다르게 둡니다.
        """
        from apps.documents.models import Document, content_hash

        spec_body = ("# 회의 API 명세 v1\n\n"
                     "- `GET /meetings/{id}` 회의 상세\n"
                     "- `GET /meetings/{id}/flow` 플로우 그래프\n"
                     "- 응답의 표시 문자열은 서버가 완성해 내려줍니다.\n")
        Document.objects.get_or_create(
            project=project, title="회의 API 명세 v1",
            defaults={"owner": people["lead"], "category": "backend",
                      "content": spec_body, "hash": content_hash(spec_body),
                      "summary": "회의·플로우 엔드포인트 계약",
                      "sections": [{"heading": "회의 상세",
                                    "one_line_summary": "회의 하나의 전체 응답",
                                    "body": "참여자·안건·요약을 한 번에 내려줍니다."},
                                   {"heading": "플로우",
                                    "one_line_summary": "사람 쌍마다 화살표 하나",
                                    "body": "낱개로 그리면 선이 겹쳐 읽을 수 없습니다."}],
                      "delivery_context": [
                          {"participant_name": "강태현",
                           "utterance": "명세 초안 올렸습니다. 이 기준으로 회의합시다.",
                           "url": "https://github.com/AX-Lions/backend/blob/develop/"
                                  "bordo-openapi.yaml"}]})

        Document.objects.get_or_create(
            project=project, title="회의 상세 화면 시안",
            defaults={"owner": people["away"], "category": "design",
                      "content": "플로우 그래프 · 우측 패널 · 브리핑 패널 구성.",
                      "summary": "회의 상세 화면 최종 시안",
                      "hash": content_hash("회의 상세 화면 시안"),
                      "delivery_context": [
                          {"participant_name": "에밀리 한",
                           "utterance": "시안 공유합니다. 8/18에 확정할 예정입니다.",
                           "url": "https://www.figma.com/design/bordo/meeting-detail"}]})

    def _tasks_and_calendar(self, project, people, now):
        """
        태스크 둘과 마감 일정 하나.

        마감 일정은 대리인이 "8월 18일" 을 근거로 답할 때 가리키는 자리입니다.
        승인 대기(`PENDING_APPROVAL`) 태스크는 **일부러 안 만듭니다** — 회의가
        끝난 뒤 AI 가 만드는 것이라, 미리 있으면 회의로 생긴 것인지 원래
        있던 것인지 구별되지 않습니다.
        """
        from apps.calendars.models import (CalendarEvent, EventKind, EventParticipant,
                                           EventStatus, Reminder)
        from apps.tasks.models import Task, TaskStatus

        lead, away, member = people["lead"], people["away"], people["member"]

        Task.objects.get_or_create(
            project=project, title="회의 API 응답 구조 확정",
            defaults={"status": TaskStatus.IN_PROGRESS, "priority": "P1",
                      "assignee": lead, "created_by": lead,
                      "due_at": now + timedelta(days=2)})
        Task.objects.get_or_create(
            project=project, title="플로우 화면 목 데이터 교체",
            defaults={"status": TaskStatus.TODO, "priority": "P2",
                      "assignee": member, "created_by": lead})

        deadline, created = CalendarEvent.objects.get_or_create(
            project=project, title="디자인 시안 마감",
            defaults={"kind": EventKind.DEADLINE, "status": EventStatus.CONFIRMED,
                      "start_at": now + timedelta(days=3),
                      "confirmed_by": lead, "confirmed_at": now - timedelta(hours=6)})
        if created:
            for u in (away, lead):
                EventParticipant.objects.get_or_create(event=deadline, user=u)
            Reminder.objects.get_or_create(
                event=deadline, notification_type=Reminder.Type.T_MINUS_1D,
                defaults={"scheduled_at": deadline.start_at - timedelta(days=1)})

    # ═══════════════════════════════════════════ 채팅

    def _chat(self, project, people, now):
        """
        방 셋 — 프로젝트 단체방 · 진행자와의 1:1 · 불참자의 AI 방.

        `PEER_AGENT`(동료의 대리인) 방은 안 만듭니다. 시연 흐름에 안 나오는데
        사이드바에만 남으면 "이건 뭐냐" 는 질문이 나와 흐름이 끊깁니다.

        대화를 **과거 시각으로** 넣습니다. 전부 지금으로 두면 세 방의 마지막
        메시지 시각이 같아 사이드바 정렬이 무의미해집니다.
        """
        from apps.chat.models import ChatMessage
        from apps.chat.services import ensure_ai_room, ensure_project_room, touch

        lead, away, member = people["lead"], people["away"], people["member"]

        room = ensure_project_room(project)
        group_talk = [
            (lead, "내일 회의에서 API 명세 확정하고 갑시다.", 300),
            (member, "프론트는 목 데이터로 붙여 뒀습니다. 응답만 확정되면 바로 교체합니다.", 290),
            (away, "저는 시안 정리 중입니다. 8/18 마감 기준으로 보고 있습니다.", 270),
        ]
        self._say(ChatMessage, room, group_talk, now)

        direct = self._direct_room(lead, away)
        self._say(ChatMessage, direct, [
            (lead, "내일 회의 시간 괜찮으세요?", 200),
            (away, "그 시간에 외부 일정이 있어서 참석이 어려울 것 같습니다.", 190),
        ], now)

        # 불참자의 AI 방은 **비워 둡니다.** 시연에서 이 방에 처음 말을 거는
        # 장면(대리인에게 회의 내용을 되묻는 장면)을 찍을 자리입니다.
        ensure_ai_room(away)
        ensure_ai_room(lead)

        for r in (room, direct):
            last = (ChatMessage.objects.filter(room=r, deleted_at__isnull=True)
                    .order_by("-sent_at").first())
            if last:
                touch(r, last.sent_at)

    def _direct_room(self, a, b):
        from apps.chat.models import ChatRoom, RoomMember, RoomType
        from apps.chat.services import direct_key

        key = direct_key(a.id, b.id)
        room, created = ChatRoom.objects.get_or_create(
            type=RoomType.DIRECT, dedupe_key=key, defaults={"created_by": a})
        if created:
            for u in (a, b):
                RoomMember.objects.get_or_create(room=room, user=u)
        return room

    def _say(self, ChatMessage, room, lines, now):
        """
        `sent_at` 은 `auto_now_add` 라 생성으로는 과거를 못 넣습니다.
        만든 뒤 UPDATE 로 밀어야 대화가 시간 순서를 갖습니다.
        """
        for sender, body, minutes_ago in lines:
            msg, created = ChatMessage.objects.get_or_create(
                room=room, client_message_id=f"showcase-{room.id}-{minutes_ago}",
                defaults={"sender": sender, "sender_name": sender.name, "body": body})
            if created:
                ChatMessage.objects.filter(pk=msg.pk).update(
                    sent_at=now - timedelta(minutes=minutes_ago))

    # ═══════════════════════════════════════════ 지난 회의 (이미 끝난 것)

    def _past_meeting(self, project, people, now):
        """
        **이미 끝난 회의 하나.** 시연에서 만들 회의와는 별개입니다.

        왜 필요한가 - 시드에 회의가 하나도 없으면 로그인했을 때 회의 화면·
        플로우·요약·브리핑이 전부 빈 채로 보입니다. "이 서비스가 무엇을
        만들어 내는가" 를 보여주는 자리가 통째로 비는 셈입니다.

        **완결된 상태로 둡니다.** 브리핑은 읽음, 유보 질문은 답변까지 끝난
        것으로. 미처리로 남기면 홈의 브리핑 배지가 떠서, 시연 중에 새로 여는
        회의의 결과와 섞입니다 - "지난 회의는 이렇게 마무리됐다" 와 "이번
        회의는 지금 만든다" 가 갈려야 합니다.

        확인 카드 하나만 미확인으로 남깁니다. 「확인이 필요해요」 는 눌러서
        비우는 목록이라, 전부 확인해 두면 그 동작을 보여줄 수가 없습니다.
        """
        from apps.agent.models import AgentRun, PendingQuestion
        from apps.meetings.models import (Agenda, AiBriefing, Attendance,
                                          BriefingConfirmation, BriefingRequest,
                                          FlowCategory, FlowContentType, FlowEdge,
                                          Meeting, MeetingDocumentRef,
                                          MeetingParticipant, MeetingStatus,
                                          MeetingSummary, Surface, Utterance)

        lead, away, member = people["lead"], people["away"], people["member"]
        ended_at = now - timedelta(days=2)

        meeting, created = Meeting.objects.get_or_create(
            project=project, title="킥오프 · 범위와 일정 합의",
            defaults={"project_name": project.name, "created_by": lead,
                      "scheduled_at": ended_at - timedelta(minutes=50),
                      "duration_min": 50, "discord_channel_id": "1509000000000000777",
                      "status": MeetingStatus.ENDED,
                      "started_at": ended_at - timedelta(minutes=50),
                      "ended_at": ended_at})
        if not created:
            return meeting

        # 에밀리 한만 대리 참석입니다. `delegated=True` 와 `attendance=DELEGATED` 는
        # 같은 행위의 두 표기라 반드시 함께 움직입니다 - 하나만 켜면 브리핑이
        # 아예 안 생깁니다(브리핑은 대리 참석자에게만).
        MeetingParticipant.objects.create(
            meeting=meeting, user=lead, user_name=lead.name,
            attendance=Attendance.PRESENT)
        MeetingParticipant.objects.create(
            meeting=meeting, user=member, user_name=member.name,
            attendance=Attendance.PRESENT)
        MeetingParticipant.objects.create(
            meeting=meeting, user=away, user_name=away.name,
            attendance=Attendance.DELEGATED, delegated=True,
            delegate_prompt="디자인 시안 마감은 8/18을 넘기지 말 것. "
                            "일정을 미루는 결정은 내 확인을 받을 것.")

        agendas = self._past_agendas(Agenda, meeting, lead, away, member)
        self._past_transcript(Utterance, meeting, people)

        MeetingSummary.objects.create(
            meeting=meeting,
            discovered_issues=["명세가 확정되지 않아 프론트가 목 데이터로 막혀 있다",
                               "디자인 시안 마감과 개발 착수가 맞물려 있다"],
            changes=["회의 API 응답 구조를 서버 완성형으로 통일",
                     "디자인 시안 마감 8월 18일로 확정"],
            next_plans=["명세 확정 후 프론트 연동 지원",
                        "다음 회의에서 API 명세 리뷰"],
            one_line="응답 구조를 서버가 완성해 내려주는 것으로 정리하고, 시안 마감을 8/18로 확정했어요.",
            main_opinions=[{"speaker": "응우옌 민",
                            "text": "표시 문자열을 클라이언트가 만들면 사람마다 다르게 보입니다"},
                           {"speaker": "에밀리 한의 Bordo",
                            "text": "시안 마감은 8월 18일을 넘기지 않습니다"}])

        doc = MeetingDocumentRef.objects.create(
            project=project, title="회의 API 명세 v1", owner=lead,
            direction_label="강태현 --> 응우옌 민",
            sections=[{"heading": "배경", "one_line_summary": "응답 구조가 화면마다 다르다",
                       "body": "표시 문자열을 클라이언트가 조립해 사람마다 다른 시각이 찍혔습니다."},
                      {"heading": "제안", "one_line_summary": "서버가 완성해 내려준다",
                       "body": "displayed_at · time_range 를 서버가 만들어 그대로 그립니다."}],
            delivery_context=[{"participant_name": "강태현",
                               "utterance": "명세 초안 공유드립니다."},
                              {"participant_name": "응우옌 민",
                               "utterance": "확인하고 목 데이터 맞추겠습니다."}])

        self._past_edges(FlowEdge, FlowCategory, FlowContentType, Surface,
                         meeting, people, agendas, doc, ended_at)
        self._past_briefing(AgentRun, AiBriefing, BriefingConfirmation,
                            BriefingRequest, PendingQuestion,
                            meeting, people, agendas, now, ended_at)
        return meeting

    def _past_agendas(self, Agenda, meeting, lead, away, member):
        rows = [
            ("응답 구조 통일", "표시 문자열을 서버가 완성해 내려주기로 합의.",
             "강태현 → 응우옌 민", lead, False),
            ("디자인 시안 마감", "8월 18일까지 확정. 대리인이 저장된 기준으로 답변.",
             "에밀리 한의 Bordo → 강태현", away, True),
            ("개발 일정 1주 연장", "의견은 모였으나 확정은 에밀리 한 확인 후로 유보.",
             "응우옌 민 → 에밀리 한의 Bordo", member, True),
        ]
        out = []
        for i, (title, content, direction, owner, by_agent) in enumerate(rows):
            out.append(Agenda.objects.create(
                meeting=meeting, title=title, sort_order=i + 1, content=content,
                direction_label=direction, status=Agenda.Status.DISCUSSED,
                owner=owner, created_by_agent=by_agent))
        return out

    def _past_transcript(self, Utterance, meeting, people):
        """
        발언 열넷.

        **대리인 발언에 `is_agent=True` 를 답니다.** 이게 없으면 회의록에서
        본인이 직접 한 말과 대리인이 대신 한 말을 가를 수 없어, 돌아온 사람이
        「나는 그런 말 한 적 없는데」 를 수습하게 됩니다.
        """
        started = meeting.started_at

        def say(minute, key, body, agent=False):
            u = people[key]
            Utterance.objects.create(
                meeting=meeting, participant=u,
                participant_name=f"{u.name}의 Bordo" if agent else u.name,
                is_agent=agent, body=body,
                spoken_at=started + timedelta(minutes=minute))

        say(0, "lead", "시작하겠습니다. 오늘 안건은 셋입니다 - 응답 구조, 시안 마감, 개발 일정.")
        say(1, "lead", "에밀리 한님은 오늘 자리를 비우셔서 대리인이 대신 들어와 있습니다.")
        say(3, "member", "응답 구조부터요. 지금은 표시 문자열을 저희가 조립하는데, "
                         "브라우저 시간대로 찍혀서 사람마다 다른 시각이 보입니다.")
        say(5, "lead", "그럼 displayed_at 같은 완성형 필드를 서버가 내려주는 걸로 바꾸죠.")
        say(7, "member", "그게 낫습니다. 저희 보정 로직은 걷어내겠습니다.")
        say(9, "lead", "정리하면 표시 문자열은 서버가 완성해 내려줍니다.")
        say(16, "lead", "다음, 디자인 시안 마감인데 에밀리 한님 쪽 일정이 어떻게 되나요?")
        say(17, "away", "에밀리 한님이 저장해 두신 기준으로는 8월 18일입니다. "
                        "현재 시안 작업은 70% 진행돼 있고, 남은 것은 브리핑 패널입니다.",
            agent=True)
        say(19, "member", "그 날짜면 저희 연동 일정도 맞출 수 있습니다.")
        say(21, "lead", "좋습니다. 시안 마감은 8월 18일로 확정하겠습니다.")
        say(30, "member", "마지막인데요, 디자인이 밀리면 개발도 1주 정도 미뤄야 할 것 같습니다. "
                          "에밀리 한님 대리인께 여쭤봐도 될까요?")
        say(32, "away", "일정을 미루는 결정은 제가 정할 수 있는 항목이 아닙니다. "
                        "오늘은 회의에서 나온 의견만 정리해 전달드리고, "
                        "최종 확정은 에밀리 한님 확인 후에 다시 말씀드리겠습니다.", agent=True)
        say(34, "lead", "알겠습니다. 그럼 연장은 확인 대기로 두겠습니다.")
        say(48, "lead", "오늘은 여기까지 - 응답 구조 통일, 시안 8/18 확정, "
                        "개발 일정은 확인 대기입니다.")

    def _past_edges(self, FlowEdge, FlowCategory, FlowContentType, Surface,
                    meeting, people, agendas, doc, ended_at):
        """
        화살표 열둘.

        같은 사람 쌍에 여러 건을 겹칩니다 - 화면의 화살표는 쌍마다 하나이고
        그 위에 `의견 3` 처럼 종류별 개수가 붙습니다. 한 건씩만 두면 집계가
        전부 1 이라 뱃지가 제대로 도는지 알 수 없습니다.

        대리인 노드를 섞는 것도 같은 이유입니다. 「본인 직접」 과 「대리인
        경유」 화살표가 한 화면에 같이 있어야 색이 갈리는 게 보입니다.
        """
        lead, away, member = people["lead"], people["away"], people["member"]

        def node(u, agent=False):
            return {"id": f"{u.id}:agent" if agent else str(u.id),
                    "kind": "AGENT" if agent else "USER",
                    "user_id": str(u.id),
                    "name": f"{u.name}의 Bordo" if agent else u.name,
                    "avatar_url": u.avatar_url or None}

        F = FlowContentType
        agent_name = f"{away.name}의 Bordo"
        rows = [
            # 회의 전 사전 지시 - 본인이 자기 대리인에게.
            (F.REQUEST, "요청사항", node(away), [node(away, agent=True)],
             agendas[1], None, Surface.SERVICE, 55,
             [(away.name, "시안 진행률과 마감일은 저장해 둔 기록대로 답해 주세요. "
                          "일정을 미루는 결정은 제 확인 없이 하지 마시고요.")]),

            (F.OPINION, "의견", node(member), [node(lead)], agendas[0], None,
             Surface.DISCORD, 47,
             [(member.name, "응답 구조부터요. 지금은 표시 문자열을 저희가 조립하는데, "
                            "브라우저 시간대로 찍혀서 사람마다 다른 시각이 보입니다.")]),
            (F.OPINION, "의견", node(member), [node(lead)], agendas[0], None,
             Surface.DISCORD, 45,
             [(member.name, "그게 낫습니다. 저희 보정 로직은 걷어내겠습니다.")]),
            (F.CHANGE, "변동사항", node(lead), [node(member)], agendas[0], doc,
             Surface.DISCORD, 41,
             [(lead.name, "그럼 displayed_at 같은 완성형 필드를 서버가 내려주는 걸로 바꾸죠."),
              (member.name, "프론트에서 만들던 문자열은 지우겠습니다.")]),
            (F.CONCLUSION, "결론", node(lead), [node(member), node(away, agent=True)],
             agendas[0], None, Surface.DISCORD, 39,
             [(lead.name, "정리하면 표시 문자열은 서버가 완성해 내려줍니다.")]),

            (F.REQUEST, "요청사항", node(lead), [node(away, agent=True)],
             agendas[1], None, Surface.DISCORD, 34,
             [(lead.name, "다음, 디자인 시안 마감인데 에밀리 한님 쪽 일정이 어떻게 되나요?")]),
            (F.OPINION, "의견", node(away, agent=True), [node(lead)],
             agendas[1], None, Surface.DISCORD, 33,
             [(agent_name, "에밀리 한님이 저장해 두신 기준으로는 8월 18일입니다. "
                           "현재 시안 작업은 70% 진행돼 있고, 남은 것은 브리핑 패널입니다.")]),
            (F.SCHEDULE, "일정", node(away, agent=True), [node(lead), node(member)],
             agendas[1], None, Surface.DISCORD, 31,
             [(agent_name, "마감일은 8월 18일로 잡혀 있습니다. 그 뒤 일정을 바꾸는 것은 "
                           "에밀리 한님 확인이 필요합니다."),
              (member.name, "그 날짜면 저희 연동 일정도 맞출 수 있습니다.")]),
            (F.CONCLUSION, "결론", node(lead), [node(member), node(away, agent=True)],
             agendas[1], None, Surface.DISCORD, 29,
             [(lead.name, "좋습니다. 시안 마감은 8월 18일로 확정하겠습니다.")]),

            (F.REQUEST, "요청사항", node(member), [node(away, agent=True)],
             agendas[2], None, Surface.DISCORD, 20,
             [(member.name, "디자인이 밀리면 개발도 1주 정도 미뤄야 할 것 같습니다. "
                            "에밀리 한님 대리인께 여쭤봐도 될까요?")]),
            (F.SCHEDULE, "일정", node(away, agent=True), [node(member), node(lead)],
             agendas[2], None, Surface.DISCORD, 18,
             [(agent_name, "개발 1주 연장 건은 회의에서 나온 의견으로 정리해 "
                           "에밀리 한님께 전달드리겠습니다.")]),
            # 유보를 남긴 자리. 화면 필터에 `유보` 칸이 없어 `기타` 로 들어갑니다.
            (F.ETC, "기타", node(away, agent=True), [node(lead)],
             agendas[2], None, Surface.SERVICE, 16,
             [(agent_name, "일정을 미루는 결정은 제가 정할 수 있는 항목이 아닙니다. "
                           "최종 확정은 에밀리 한님 확인 후에 다시 말씀드리겠습니다."),
              (lead.name, "알겠습니다. 그럼 연장은 확인 대기로 두겠습니다.")]),
        ]
        for ctype, label, src, dsts, agenda, document, surface, mins_ago, says in rows:
            e = FlowEdge.objects.create(
                meeting=meeting, project=meeting.project,
                category=FlowCategory.MEETING, content_type=ctype, surface=surface,
                from_node=src, to_nodes=dsts, label=label,
                direction_label=f"{src['name']} → {', '.join(d['name'] for d in dsts)}",
                participant_ids=[src["user_id"]] + [d["user_id"] for d in dsts],
                agenda=agenda, document=document,
                # 카드 본문에 찍히는 실제 대사입니다. 이게 없으면 우측 패널이
                # 제목과 `Discord` 만 남아, 회의록 열네 줄이 옆에 있는데도
                # 화면에서는 무슨 말이 오갔는지 볼 수 없습니다.
                delivery_context=[{"participant_name": who, "utterance": what}
                                  for who, what in says],
                occurred_at=ended_at - timedelta(minutes=mins_ago))
            e.opacity = e.compute_opacity()
            e.save(update_fields=["opacity"])

    def _past_briefing(self, AgentRun, AiBriefing, BriefingConfirmation,
                       BriefingRequest, PendingQuestion,
                       meeting, people, agendas, now, ended_at):
        """
        대리인이 무엇을 보고 답했는지(`evidence`)와 그 결과(브리핑).

        `evidence` 는 **제목 스냅샷**입니다. 근거가 된 문서가 나중에 지워져도
        "그때 이걸 보고 답했다" 가 남아야 추적이 끊기지 않습니다.
        """
        lead, away, member = people["lead"], people["away"], people["member"]

        answered = AgentRun.objects.create(
            user=away, meeting=meeting, status=AgentRun.Status.COMPLETED,
            settings_snapshot=away.agent_settings.as_snapshot(),
            steps=[{"kind": "search", "detail": "에밀리 한의 계획·작업에서 마감일 확인"},
                   {"kind": "policy", "detail": "공개 범위 확인 - 계획 공개 허용"},
                   {"kind": "verdict", "answer": True,
                    "reason": "저장된 계획에 마감일이 명시돼 있음"}],
            evidence=[{"kind": "plan", "title_snapshot": "디자인 시안 8/18 확정",
                       "excerpt": "planned_end_at = 8월 18일"},
                      {"kind": "work", "title_snapshot": "회의 상세 화면 최종 시안",
                       "excerpt": "진행률 70%, 남은 것은 브리핑 패널"}],
            result="에밀리 한님이 저장해 두신 기준으로는 8월 18일입니다. "
                   "현재 시안 작업은 70% 진행돼 있습니다.")
        deferred = AgentRun.objects.create(
            user=away, meeting=meeting, status=AgentRun.Status.COMPLETED,
            settings_snapshot=away.agent_settings.as_snapshot(),
            steps=[{"kind": "search", "detail": "일정 변경 권한 확인"},
                   {"kind": "policy", "reason": "allow_schedule_change=false",
                    "detail": "일정 변경은 위임되지 않음"},
                   {"kind": "verdict", "answer": False,
                    "reason": "allow_schedule_change=false"}],
            evidence=[{"kind": "settings", "title_snapshot": "대리인 설정 v2",
                       "excerpt": "allow_schedule_change = false"}],
            result="일정을 미루는 결정은 제가 정할 수 있는 항목이 아닙니다.")
        for run, minutes in ((answered, 33), (deferred, 18)):
            AgentRun.objects.filter(pk=run.pk).update(
                created_at=ended_at - timedelta(minutes=minutes))

        AiBriefing.objects.create(
            meeting=meeting, user=away,
            narrative=("시안 마감은 저장해 두신 8월 18일 기준으로 답했고, "
                       "개발 일정 1주 연장은 위임하지 않으신 항목이라 유보했습니다. "
                       "응답 구조를 서버 완성형으로 바꾸기로 한 결정은 확인이 필요합니다."),
            location_chips=self._chips(meeting),
            used_answers=[{"run_id": str(answered.id), "agenda_id": str(agendas[1].id),
                           "body": answered.result,
                           "evidence": [e["title_snapshot"] for e in answered.evidence],
                           "excerpt": "시안 마감은 8월 18일입니다.", "reason": None}],
            deferred_answers=[{"run_id": str(deferred.id), "agenda_id": str(agendas[2].id),
                               "body": deferred.result,
                               "evidence": [e["title_snapshot"] for e in deferred.evidence],
                               "excerpt": "개발 일정 연장은 제가 정할 수 없습니다.",
                               "reason": "allow_schedule_change=false"}],
            settings_version=2, read_at=now - timedelta(days=1, hours=20))

        # 확인 카드 둘 - 하나는 이미 확인, 하나는 남겨 둡니다.
        BriefingConfirmation.objects.create(
            meeting=meeting, user=away, source_key="response-shape",
            title="회의 API 응답 구조가 바뀌었습니다",
            body="표시 문자열을 클라이언트가 조립하지 않고 서버가 완성해 내려주기로 했습니다. "
                 "시안의 시각 표기도 이 기준으로 맞춰야 합니다.",
            agenda=agendas[0], occurred_at=ended_at)
        BriefingConfirmation.objects.create(
            meeting=meeting, user=away, source_key="design-deadline",
            title="디자인 시안 마감이 8월 18일로 확정됐습니다",
            body="대리인이 전달한 날짜 그대로 확정됐습니다.",
            agenda=agendas[1], occurred_at=ended_at,
            confirmed_at=now - timedelta(days=1, hours=19))

        BriefingRequest.objects.create(
            meeting=meeting, user=away, source_key="extension-confirm",
            title="개발 일정 1주 연장 확인 요청",
            requester_name=member.name,
            note="회의에서 의견은 모였고, 확정만 남았습니다.",
            due_at=ended_at + timedelta(days=3), occurred_at=ended_at)

        # 유보가 남긴 질문. **답변까지 끝난 상태**로 둡니다 - 미답변으로
        # 남기면 홈 배지가 떠서 시연 중 새 회의의 결과와 섞입니다.
        PendingQuestion.objects.create(
            meeting=meeting, run=deferred, asker=member, asker_name=member.name,
            target_user=away, title="개발 일정 1주 연장, 확정해도 될까요",
            body="디자인 시안이 8/18이면 개발 착수가 그만큼 밀립니다. 1주 연장으로 확정할까요?",
            answered_at=now - timedelta(days=1, hours=18),
            answer_body="1주 연장으로 확정해 주세요. 대신 QA 기간은 그대로 5일 유지하겠습니다.")

    def _chips(self, meeting):
        """
        요약 아래 정보 위치 칩.

        **실제 생성 코드를 그대로 부릅니다.** 시드가 따로 집계하면 종류가
        늘었을 때 시드만 옛 목록을 들고 있게 됩니다.
        """
        from apps.agent.services.briefing import _location_chips
        return _location_chips(meeting)

    # ═══════════════════════════════════════════ 안내

    def _did(self, user):
        return f" · Discord {user.discord_user_id}" if user.discord_user_id else ""

    def _discord_warning(self):
        """
        Discord 값이 비었으면 **끝에서 한 번 더** 말합니다.

        빠뜨렸을 때 나는 증상이 "봇은 잘 도는데 대리인만 조용하다" 라,
        촬영장에서 원인을 짚기가 가장 어렵습니다.
        """
        missing = [k for k, *_ in PEOPLE if k not in self.discord_ids]
        if not self.guild_id:
            missing = ["guild"] + missing
        if not missing:
            return ""
        return ("\n  [주의] Discord 값이 비어 있습니다: " + ", ".join(missing) + "\n"
                "         이대로면 회의는 열려도 대리인이 아무 말도 하지 않습니다.\n"
                "         python manage.py seed_showcase --reset \\\n"
                "           --guild <서버id> --discord lead=<id> away=<id> member=<id>\n")

    def _report(self, team, project, people):
        lead, away, member = people["lead"], people["away"], people["member"]
        self.stdout.write(self.style.SUCCESS(
            "\n시연 시드 완료 - 회의는 촬영 중에 직접 만드십시오.\n"))
        self.stdout.write(
            f"  팀        : {team.name} ({team.id})\n"
            f"  프로젝트  : {project.name} ({project.id})\n"
            f"  Guild     : {self.guild_id or '(없음 - Discord 연결을 안 만들었습니다)'}\n"
            f"\n  계정 (비밀번호 전부 {PASSWORD})\n"
            f"    진행자   {lead.email}   {lead.name} · 팀 OWNER · {lead.timezone}"
            f"{self._did(lead)}\n"
            f"    불참자   {away.email}   {away.name} · 대리 참석 · {away.timezone}"
            f"{self._did(away)}\n"
            f"    참석자   {member.email}   {member.name} · {member.timezone}"
            f"{self._did(member)}\n"
            f"{self._discord_warning()}"
            f"\n  촬영 순서 (자세한 것은 docs/시연-시나리오.md)\n"
            f"    1. {lead.name} → 회의 `API 명세 리뷰` 를 만든다\n"
            f"    2. {away.name} → **불참 등록만** 하고 나간다\n"
            f"       회의 전 준비(논쟁점 · 입장)는 마지막 부록입니다. 여기서 찍으면\n"
            f"       '미리 써 두는 도구' 로 보여 시연의 결론이 사라집니다\n"
            f"    3. Discord `/meeting-start` → **이름을 넣어** 묻는다\n"
            f"       - {away.name}님 시안 작업 지금 어디까지 됐나요?  → 답변\n"
            f"       - 1주 연장으로 확정해도 될까요?                  → 유보\n"
            f"         (`allow_schedule_change=False` 로 깔아 뒀습니다)\n"
            f"    4. `/meeting-end` → 플로우 → {away.name} 계정으로 브리핑 · 답변 대기 질문\n")
