"""
데모 시드.

    python manage.py seed_demo

심사 시연과 프론트 개발용입니다. 홈 화면과 플로우 화면이 실제로 채워집니다.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentSettings, PendingQuestion
from apps.meetings.models import (Agenda, AiBriefing, Attendance, FlowCategory,
                                  FlowContentType, FlowEdge, Meeting,
                                  MeetingDocumentRef, MeetingParticipant,
                                  MeetingStatus, MeetingSummary, Surface, Utterance)
from apps.orgs.models import (Favorite, Project, ProjectMember, RecentProject, Team,
                              TeamMember, TeamRole)

#: 데모 팀. `(이메일, 이름, 역할, 아바타)`
#
# 아바타 경로는 **프론트 정적 자산**입니다. 같은 출처에서 서빙되므로 그대로
# 씁니다. 비워 두면 화면이 사람마다 겹친 원(AvatarStack)으로 대신 그리는데,
# 플로우 화면은 노드가 얼굴로 구별되는 그림이라 전부 같아 보입니다.
PEOPLE = [
    ("susu@bordo.dev", "유수인", "design", "/flowchart/profile-2.jpeg"),
    ("backend01@bordo.dev", "최비성", "backend", "/flowchart/profile-1.jpeg"),
    ("front01@bordo.dev", "임수연", "frontend", "/flowchart/profile-3.jpeg"),
    ("jaemin@bordo.dev", "서재민", "backend", ""),
    ("daeun@bordo.dev", "강다은", "discord", ""),
]
PASSWORD = "Bordo!2026"


class Command(BaseCommand):
    help = "데모용 팀·프로젝트·회의·플로우를 만듭니다."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="기존 데모 데이터를 지웁니다.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts["reset"]:
            # 팀을 먼저 지웁니다.
            #
            # 사람부터 지우면 `Team.created_by` · `Project.created_by` ·
            # `Meeting.created_by` 가 PROTECT 라 막힙니다. 만든 사람만 사라지고
            # 만든 것이 남는 상황을 모델이 거부하는 것이고, 그게 맞습니다.
            # 시드가 순서를 맞춥니다.
            Team.all_objects.filter(
                created_by__email__endswith="@bordo.dev").delete()
            User.all_objects.filter(email__endswith="@bordo.dev").delete()
            self.stdout.write("기존 데모 데이터를 지웠습니다.")

        users = {}
        for email, name, role, avatar in PEOPLE:
            u = User.all_objects.filter(email=email).first()
            if not u:
                u = User.objects.create_user(email=email, password=PASSWORD, name=name,
                                             project_role=role, timezone="Asia/Seoul")
            # 이미 있는 계정에도 아바타를 채웁니다. --reset 없이 다시 돌릴 때
            # 사람만 남고 그림이 비면 노드가 전부 같아 보입니다.
            if u.avatar_url != avatar:
                u.avatar_url = avatar
                u.save(update_fields=["avatar_url"])
            users[name] = u
            AgentSettings.objects.get_or_create(user=u)

        owner = users["유수인"]
        team, _ = Team.objects.get_or_create(
            name="멋사 중앙해커톤",
            defaults={"created_by": owner, "description": "Bordo 개발팀",
                      "category_keys": ["backend", "frontend", "design"]})
        for i, (_, name, _r, _a) in enumerate(PEOPLE):
            TeamMember.objects.get_or_create(
                team=team, user=users[name],
                defaults={"team_role": TeamRole.OWNER if i == 0 else TeamRole.MEMBER})
        team.member_count = TeamMember.objects.filter(team=team).count()
        team.save(update_fields=["member_count"])

        projects = []
        for name, progress in [("글로벌 회의 도구", 63), ("연합학술제", 41), ("결제 모듈", 12)]:
            p, _ = Project.objects.get_or_create(
                team=team, name=name,
                defaults={"team_name": team.name, "created_by": owner,
                          "progress": progress})
            for _, pname, _r, _a in PEOPLE:
                ProjectMember.objects.get_or_create(project=p, user=users[pname])
            p.member_count = ProjectMember.objects.filter(project=p).count()
            p.progress = progress
            p.save(update_fields=["member_count", "progress"])
            projects.append(p)

        Favorite.objects.get_or_create(user=owner, target_type=Favorite.Target.PROJECT,
                                       target_id=projects[0].id)
        for p in projects[:2]:
            RecentProject.objects.update_or_create(user=owner, project=p)

        now = timezone.now()
        main = projects[0]

        # ── 오늘 일정 3건 (Discord 에서 열립니다)
        upcoming = {}
        for hour, title in [(9, "정기 팀 회의"), (13, "디자인 리뷰"), (17, "개발팀 Sync")]:
            at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if at < now:
                at += timedelta(days=1)
            m, created = Meeting.objects.get_or_create(
                project=main, title=title, scheduled_at=at,
                defaults={"project_name": main.name, "created_by": owner,
                          "duration_min": 60, "discord_channel_id": "556677889900",
                          "status": MeetingStatus.CONFIRMED})
            if created:
                MeetingSummary.objects.get_or_create(meeting=m)
                for _, pname, _r, _a in PEOPLE:
                    MeetingParticipant.objects.get_or_create(
                        meeting=m, user=users[pname],
                        defaults={"user_name": pname})
            upcoming[title] = m

        # ── 끝난 회의 하나 — 플로우와 브리핑이 붙습니다
        ended_at = now - timedelta(hours=3)
        meeting, created = Meeting.objects.get_or_create(
            project=main, title="글로벌 회의 일정 및 개발 방향 논의",
            defaults={"project_name": main.name, "created_by": owner,
                      "scheduled_at": ended_at - timedelta(hours=1),
                      "duration_min": 60, "discord_channel_id": "556677889900",
                      "status": MeetingStatus.ENDED,
                      "started_at": ended_at - timedelta(hours=1),
                      "ended_at": ended_at})
        if not created:
            self.stdout.write("이미 시드가 있습니다. --reset 으로 다시 만드십시오.")
            return

        parts = {}
        for i, (_, pname, _r, _a) in enumerate(PEOPLE):
            att = Attendance.DELEGATED if pname == "유수인" else Attendance.PRESENT
            parts[pname] = MeetingParticipant.objects.create(
                meeting=meeting, user=users[pname], user_name=pname,
                attendance=att, delegated=(att == Attendance.DELEGATED),
                delegate_prompt=("일정 관련 결정은 내 확인을 받도록 하고, "
                                 "디자인 시안 일정은 8/18을 넘기지 말 것"
                                 if att == Attendance.DELEGATED else ""))

        MeetingSummary.objects.create(
            meeting=meeting,
            discovered_issues=["팀별 시간대가 달라 회의 슬롯이 겹친다",
                               "디자인 시안 확정이 개발 착수를 막고 있다"],
            changes=["8월 18일까지 디자인 시안 확정", "개발 일정 1주 연장"],
            next_plans=["디자인 작업 우선 진행 후 개발팀에 전달",
                        "다음 회의에서 API 명세 리뷰"],
            one_line="디자인 작업을 우선 진행한 후 개발팀에 전달하기로 결정했어요.",
            main_opinions=[{"speaker": "임수연", "text": "시안이 늦어지면 개발이 통째로 밀립니다"},
                           {"speaker": "서재민", "text": "일정 1주 연장이면 감당 가능합니다"}])

        self._seed_meeting_transcript(meeting, users, owner)

        agendas = []
        for i, (title, content, direction) in enumerate([
            ("회의 일정 조율", "시간대가 다른 팀원을 고려해 슬롯을 다시 잡는다.", "최비성 → 임수연, 서재민"),
            ("디자인 시안 마감", "8월 18일까지 확정하기로 합의.", "임수연 → 최비성"),
            ("개발 일정 연장", "대리인이 유수인 대신 일정 연장 요청을 전달.", "유수인의 Bordo → 최비성, 서재민"),
        ]):
            agendas.append(Agenda.objects.create(
                meeting=meeting, title=title, sort_order=i + 1, content=content,
                direction_label=direction, status=Agenda.Status.DISCUSSED,
                owner=owner if i == 2 else users["최비성"],
                created_by_agent=(i == 2)))

        doc = MeetingDocumentRef.objects.create(
            project=main, title="글로벌 회의 운영 기획안", owner=users["최비성"],
            direction_label="최비성 --> 임수연",
            sections=[{"heading": "배경", "one_line_summary": "시간대가 달라 회의가 겹친다",
                       "body": "팀원이 서울·LA에 나뉘어 있어…"},
                      {"heading": "제안", "one_line_summary": "슬롯 자동 추천",
                       "body": "참석자 timezone 을 모아 겹치는 구간을…"}],
            delivery_context=[{"participant_name": "최비성",
                               "utterance": "기획안 먼저 공유드립니다."},
                              {"participant_name": "임수연",
                               "utterance": "확인하고 시안에 반영할게요."}])

        def node(u, agent=False):
            return {"id": f"{u.id}:agent" if agent else str(u.id),
                    "kind": "AGENT" if agent else "USER",
                    "user_id": str(u.id),
                    "name": f"{u.name}의 Bordo" if agent else u.name,
                    "avatar_url": u.avatar_url or None}

        # 같은 사람 쌍에 여러 건을 넣습니다 — 화면의 화살표는 쌍마다 하나이고
        # 그 위에 `의견 3` `요청사항 5` 처럼 종류별 개수가 붙기 때문입니다.
        # 한 건씩만 두면 집계가 전부 1 로 나와 뱃지가 제대로인지 알 수 없습니다.
        MT = FlowCategory.MEETING
        edges = [
            # 문서를 여기 답니다. 회의 중에 기획안이 오간 자리라, 화살표를 누르면
            # 문서 전달 맥락으로 넘어갑니다. 어디에도 안 달면 문서가 어느 흐름에서
            # 나왔는지 화면에서 짚을 수 없습니다.
            (MT, FlowContentType.OPINION, "의견",
             node(users["최비성"]), [node(users["임수연"])], agendas[0], doc,
             Surface.DISCORD, 48),
            (MT, FlowContentType.OPINION, "의견",
             node(users["최비성"]), [node(users["임수연"])], agendas[0], None,
             Surface.DISCORD, 45),
            (MT, FlowContentType.OPINION, "의견",
             node(users["최비성"]), [node(users["임수연"])], agendas[1], None,
             Surface.SERVICE, 44),
            (MT, FlowContentType.REQUEST, "요청사항",
             node(users["최비성"]), [node(users["임수연"])], agendas[1], None,
             Surface.DISCORD, 41),
            (MT, FlowContentType.REQUEST, "요청사항",
             node(users["최비성"]), [node(users["임수연"])], agendas[1], None,
             Surface.DISCORD, 39),
            (MT, FlowContentType.CHANGE, "변동사항",
             node(users["최비성"]), [node(users["임수연"])], agendas[1], None,
             Surface.SERVICE, 33),

            (MT, FlowContentType.REQUEST, "요청사항",
             node(users["임수연"]), [node(owner, agent=True)], agendas[2], None,
             Surface.DISCORD, 28),
            (MT, FlowContentType.CHANGE, "변동사항",
             node(users["임수연"]), [node(owner, agent=True)], agendas[2], None,
             Surface.DISCORD, 26),
            (MT, FlowContentType.SCHEDULE, "일정",
             node(users["임수연"]), [node(owner, agent=True)], agendas[2], None,
             Surface.SERVICE, 24),

            (MT, FlowContentType.CONCLUSION, "결론",
             node(owner, agent=True),
             [node(users["최비성"]), node(users["서재민"])], agendas[2], None,
             Surface.DISCORD, 18),
            (MT, FlowContentType.ETC, "기타",
             node(owner, agent=True), [node(users["서재민"])], None, None,
             Surface.SERVICE, 14),

        ]
        for cat, ctype, label, src, dsts, agenda, document, surface, mins_ago in edges:
            e = FlowEdge.objects.create(
                meeting=meeting, project=meeting.project,
                category=cat, content_type=ctype, surface=surface,
                from_node=src, to_nodes=dsts, label=label,
                direction_label=f"{src['name']} → {', '.join(d['name'] for d in dsts)}",
                participant_ids=[src["user_id"]] + [d["user_id"] for d in dsts],
                agenda=agenda, document=document,
                occurred_at=ended_at - timedelta(minutes=mins_ago))
            e.opacity = e.compute_opacity()
            e.save(update_fields=["opacity"])

        AiBriefing.objects.create(
            meeting=meeting, user=owner,
            narrative=("일정 조율 논쟁에서는 저장해 두신 '8/18 마감' 기준으로 답했고, "
                       "개발 일정 1주 연장은 확인이 필요하다고 보아 유보했습니다."),
            used_answers=[{"edge_id": None, "agenda_id": str(agendas[1].id),
                           "excerpt": "디자인 시안은 8월 18일을 넘기지 않습니다.",
                           "reason": None}],
            deferred_answers=[{"edge_id": None, "agenda_id": str(agendas[2].id),
                               "excerpt": "개발 일정 연장은 제가 정할 수 없습니다.",
                               "reason": "allow_schedule_change=false"}],
            settings_version=1)

        PendingQuestion.objects.create(
            meeting=meeting, asker=users["서재민"], asker_name="서재민",
            target_user=owner, title="디자인 시안 마감 관련",
            body="8/18 마감이면 QA 기간이 3일뿐인데 괜찮을까요?")

        work_edges = self._seed_work_flow(main, users, now)

        # 작업 플로우 화면을 시연에서 제일 많이 보여줄 예정이라, 나머지 두
        # 프로젝트도 비워두지 않는다. main 만큼 촘촘하지는 않지만(다섯 카테고리
        # 전부는 채운다) — 시연 중 프로젝트를 바꿔도 빈 화면이 나오지 않게 하는
        # 것이 목적이라 main보다 옅게 채워도 된다.
        academic_edges = self._seed_academic_festival_flow(projects[1], users, now)
        payment_edges = self._seed_payment_module_flow(projects[2], users, now)

        # 연합학술제 · 결제 모듈에는 회의 자체가 아예 없었다 — 각자 끝난 회의를
        # 하나씩 만든다(요약·발언·안건·플로우 포함, main보다는 옅게).
        self._seed_academic_meeting(projects[1], users, now)
        self._seed_payment_meeting(projects[2], users, now)

        # 지금까지 하나도 안 채워져 있던 화면들.
        self._seed_tasks(team, projects, users, meeting, now)
        self._seed_calendar(projects, users, meeting, now)
        self._seed_agent_chat(users, meeting, now)
        self._seed_debate(upcoming["디자인 리뷰"], users, owner, now)
        self._seed_briefing_cards(meeting, agendas, users, owner, now)
        self._seed_chat_rooms(team, users, owner, now)

        self.stdout.write(self.style.SUCCESS(
            f"\n시드 완료\n"
            f"  로그인   : susu@bordo.dev / {PASSWORD}\n"
            f"  팀       : {team.name} ({team.id})\n"
            f"  프로젝트 : {main.name} ({main.id})\n"
            f"  회의     : {meeting.title} ({meeting.id})\n"
            f"  작업 엣지 : {work_edges} (글로벌 회의 도구) · "
            f"{academic_edges} (연합학술제) · {payment_edges} (결제 모듈)\n"))

    # ═══════════════════════════════════════════ 작업 플로우

    def _seed_work_flow(self, project, users, now):
        """
        작업 모드 화면에 들어갈 데이터.

        ## FlowEdge 를 직접 심지 않습니다

        위의 회의 엣지는 `FlowEdge.objects.create()` 로 직접 심습니다. 여기서는
        **실제 모델을 만들어 시그널이 그리게** 합니다.

        직접 심으면 화면은 채워지지만 **생성기가 도는지는 아무도 모릅니다.**
        실제로 그래서 배포하고 나서야 작업 엣지가 두 개뿐이고 그마저 옛 종류인
        걸 알았습니다. 이렇게 두면 시드를 돌릴 때마다 생성기가 함께 확인됩니다 —
        시그널이 끊기면 마지막 줄의 엣지 수가 0 으로 떨어집니다.

        ## 화면의 다섯 칸을 모두 채웁니다

            작업     WorkItem 생성
            수정     WorkItem 상태 변경 · 문서 수정
            공유     문서에 전달 맥락이 붙는 순간
            피드백   프로젝트 방의 중요 표시된 메시지
            AI 조회  대리인이 다른 대리인에게 물어본 기록

        하나라도 비면 필터 목록에 그 칸이 안 나옵니다 — 목록은 실제로 존재하는
        종류만 내려주기 때문입니다.
        """
        from apps.agent.models import AgentLookup
        from apps.agent.services.lookup import draw_edge
        from apps.chat.models import ChatMessage
        from apps.chat.services import ensure_project_room
        from apps.documents.models import Document
        from apps.meetings.models import FlowCategory, FlowEdge, FlowSource
        from apps.states.models import WorkItem, WorkStatus

        drawn = 0

        def emit(days_ago, make):
            """
            모델을 만들고, 그때 그려진 화살표를 과거로 옮깁니다.

            시그널은 `timezone.now()` 로 찍습니다. 시드가 만든 것이 전부 같은
            시각이면 **진하기가 균일해져** 최근일수록 진해지는 규칙이 화면에서
            확인되지 않습니다(작업 플로우의 진하기는 조회 기간 기준입니다).

            어느 엣지가 새로 생겼는지는 만들기 전후의 id 집합을 비교해 찾습니다.
            시그널이 몇 개를 그리는지는 시드가 알 바가 아니고, 알아야 한다면
            시드가 생성기 내부를 흉내 내는 셈이 됩니다.
            """
            nonlocal drawn
            scope = FlowEdge.objects.filter(project=project,
                                            category=FlowCategory.WORK)
            before = set(scope.values_list("id", flat=True))
            obj = make()
            drawn += scope.exclude(id__in=before).update(
                occurred_at=now - timedelta(days=days_ago))
            return obj

        def work(owner, title, status, progress):
            return lambda: WorkItem.objects.create(
                project=project, owner=owner, title=title,
                status=status, progress=progress)

        def move(item, status, progress):
            def go():
                item.status, item.progress = status, progress
                item.save()
                return item
            return go

        # ── 작업 · 수정
        #
        # 앞의 셋은 스프린트 초에 열려 최근에 상태가 바뀐 것이고(→ `수정`),
        # 뒤의 셋은 이번 주에 새로 열린 것입니다(→ `작업` 만).
        # 생성과 상태 변경 사이가 벌어져 있어야 진하기 차이가 보입니다.
        changed = [
            (emit(13, work(users["유수인"], "우측 패널 너비 수정",
                           WorkStatus.IN_PROGRESS, 30)), WorkStatus.DONE, 100),
            (emit(12, work(users["최비성"], "API 응답 구조 변경",
                           WorkStatus.IN_PROGRESS, 45)), WorkStatus.DONE, 100),
            (emit(11, work(users["임수연"], "프로필 UI 수정",
                           WorkStatus.TODO, 0)), WorkStatus.IN_PROGRESS, 60),
        ]
        emit(7, work(users["임수연"], "회의 상세 화면 제작", WorkStatus.IN_PROGRESS, 55))
        emit(6, work(users["최비성"], "로그인 API 구현", WorkStatus.IN_PROGRESS, 40))
        emit(5, work(users["유수인"], "참여자 프로필 제작", WorkStatus.TODO, 0))

        # 서재민 · 강다은도 자기 작업을 갖습니다.
        #
        # 이게 없으면 플로우 화면에서 **그 사람 노드를 눌러도 우측 패널이 빕니다.**
        # 노드는 다섯인데 볼 것이 있는 사람은 셋뿐인 상태가 되고, 시연에서
        # "다른 사람은 눌러도 아무것도 안 나온다" 로 보입니다.
        changed += [
            (emit(10, work(users["서재민"], "반응형 기준 변경",
                           WorkStatus.IN_PROGRESS, 40)), WorkStatus.DONE, 100),
            (emit(9, work(users["강다은"], "검색 인터렉션 수정",
                          WorkStatus.TODO, 0)), WorkStatus.IN_PROGRESS, 50),
        ]
        emit(8, work(users["서재민"], "모바일 화면 제작", WorkStatus.IN_PROGRESS, 65))
        emit(7, work(users["서재민"], "로그인 API 연동", WorkStatus.DONE, 100))
        emit(6, work(users["서재민"], "회의 화면 반응형 작업", WorkStatus.IN_PROGRESS, 35))
        emit(4, work(users["강다은"], "검색 기능 구현", WorkStatus.IN_PROGRESS, 45))
        emit(3, work(users["강다은"], "Bordo 브리핑 개선", WorkStatus.TODO, 0))

        for days, (item, status, progress) in zip((3, 2, 1, 5, 2), changed):
            emit(days, move(item, status, progress))

        # ── 공유
        #
        # 출처가 섞이게 둡니다. 필터의 `출처` 가 Github · Figma · Notion 세 칸인데
        # 출처는 전달 맥락의 링크에서 알아냅니다 — 링크가 한 종류면 칸이 하나만
        # 뜨고, 없으면 아예 안 뜹니다.
        design = emit(9, lambda: Document.objects.create(
            project=project, owner=users["유수인"], title="디자인 최종안",
            category="design", summary="회의 상세·플로우 화면 최종 시안"))

        def share_design():
            # 만들 때는 비어 있다가 **나중에** 전달 맥락이 붙습니다. 실제 사용
            # 경로가 이 모양이라(문서를 쓰고, 나중에 넘긴다) 여기서도 나눕니다.
            # 만들면서 같이 채우면 `공유` 화살표만 남고 `작업` 이 안 생깁니다.
            design.delivery_context = [
                {"participant_name": "유수인",
                 "utterance": "최종안 올렸습니다. 여기서 확정할게요.",
                 "url": "https://www.figma.com/design/bordo/final"},
                {"participant_name": "임수연",
                 "utterance": "확인하고 컴포넌트부터 붙이겠습니다."}]
            design.save()
            return design

        emit(4, share_design)

        emit(6, lambda: Document.objects.create(
            project=project, owner=users["서재민"], title="글로벌 회의 운영 기획안",
            category="planning", summary="시간대가 다른 팀의 회의 운영 방식",
            delivery_context=[
                {"participant_name": "서재민",
                 "utterance": "기획안 정리해 뒀습니다. 여기 기준으로 잡겠습니다.",
                 "url": "https://www.notion.so/bordo/meeting-ops"}]))

        emit(3, lambda: Document.objects.create(
            project=project, owner=users["최비성"], title="API 명세서 v2",
            category="backend", summary="회의·플로우 엔드포인트 계약",
            delivery_context=[
                {"participant_name": "최비성",
                 "utterance": "명세 갱신했습니다. 응답 구조가 바뀌었습니다.",
                 "url": "https://github.com/AX-Lions/backend/blob/develop/bordo-openapi.yaml"},
                {"participant_name": "임수연",
                 "utterance": "프론트 목 데이터도 맞추겠습니다."}]))

        # ── 피드백
        #
        # 프로젝트 방이어야 합니다. 1:1 방에도 `project` 가 붙지만 그건 그리지
        # 않습니다 — 팀 화면에 사적인 대화가 실립니다.
        room = ensure_project_room(project)

        def say(sender_name, body, important=True):
            return lambda: ChatMessage.objects.create(
                room=room, sender=users[sender_name], sender_name=sender_name,
                body=body, is_important=important)

        emit(5, say("임수연", "우측 패널이 1280 이하에서 잘립니다. 너비를 다시 봐야 합니다."))
        emit(2, say("최비성", "응답 구조를 바꿨습니다. 기존 필드는 한 주만 같이 내려갑니다."))
        # 사람마다 피드백이 있어야 우측 패널의 `피드백` 칩이 0 이 아닙니다.
        emit(6, say("서재민", "로그인 실패 원인에 따라 오류 메시지를 구분하는 게 좋겠습니다."))
        emit(4, say("서재민", "회의 상세 화면은 모바일에서 좌우 스크롤이 생깁니다. 기준폭을 낮춥시다."))
        emit(3, say("강다은", "Discord 공지 문구에 회의 시각이 두 번 들어갑니다."))

        # 중요 표시는 화면에서 **나중에** 켭니다(`PATCH .../important`). 켜지는
        # 순간에도 그려지는지 시드가 함께 확인합니다.
        later = say("유수인", "프로필 이미지는 원형으로 통일해 주십시오.", important=False)()

        def flag():
            later.is_important = True
            later.save()
            return later

        emit(1, flag)

        # ── AI 조회
        #
        # 시그널이 아니라 `lookup.draw_edge()` 를 직접 부릅니다. 원래 경로는
        # 대리인이 실제로 도는 것이라 시드에서 LLM 을 부를 수 없습니다.
        # 그리는 코드 자체는 같은 것을 씁니다.
        for days, asker, target, topic, reason, question, answer, source in [
            (4, "유수인", "최비성", "로그인 API 구현 현황",
             "디자인 최종안을 넘기기 전에 API 가 어디까지 됐는지 알아야 했습니다.",
             "로그인 API 는 지금 어디까지 됐습니까? 응답 형태가 확정됐는지 궁금합니다.",
             "토큰 발급까지 돼 있고 재발급은 이번 주에 붙습니다. 응답 형태는 확정입니다.",
             FlowSource.GITHUB),
            # 답이 비어 있는 건 유보입니다. 4단 상세에서 `확인된 내용` 이 비는
            # 경우가 화면에 어떻게 나오는지 프론트가 볼 수 있어야 합니다.
            (2, "서재민", "임수연", "회의 상세 화면 진행 상황",
             "응답 구조를 바꾸기 전에 프론트가 어디를 붙였는지 확인이 필요했습니다.",
             "회의 상세 화면에서 지금 붙인 API 가 무엇입니까?",
             "",
             FlowSource.FIGMA),
            # 화면에서 `AI 조회` 는 주황 선으로 그려집니다. 두 건뿐이면 선이
            # 한 쌍만 주황이라 색으로 갈랐다는 것이 눈에 안 들어옵니다.
            (5, "최비성", "유수인", "디자인 최종안 확정 여부",
             "API 응답 구조를 바꾸기 전에 화면이 확정됐는지 알아야 했습니다.",
             "회의 상세 화면 시안이 확정됐습니까?",
             "8월 18일에 확정하기로 했고 지금은 검토 중입니다.",
             FlowSource.FIGMA),
            (3, "서재민", "강다은", "Discord 공지 형식",
             "모바일 반응형 작업 중 공지 문구 길이가 화면에 영향을 줬습니다.",
             "Discord 공지 문구 형식이 정해졌습니까?",
             "회의 시각과 링크만 넣기로 했습니다.",
             FlowSource.NOTION),
            (1, "강다은", "서재민", "검색 기능 연동 시점",
             "검색 인터렉션을 고치기 전에 API 가 언제 나오는지 확인했습니다.",
             "검색 API 는 언제쯤 붙일 수 있습니까?",
             "",
             FlowSource.GITHUB),
        ]:
            def ask(a=asker, t=target, tp=topic, r=reason, q=question,
                    ans=answer, s=source, d=days):
                return draw_edge(AgentLookup.objects.create(
                    project=project, asker=users[a], target=users[t],
                    topic=tp, reason=r, question=q, answer=ans,
                    source=s, occurred_at=now - timedelta(days=d)))

            emit(days, ask)

        return drawn

    # ═══════════════════════════════════════════ 연합학술제 · 결제 모듈

    def _seed_academic_festival_flow(self, project, users, now):
        """
        "연합학술제" 프로젝트의 작업 플로우.

        `_seed_work_flow` 만큼 촘촘하지는 않다 — 시연에서 이 프로젝트를 메인으로
        쓰지는 않고, **프로젝트를 바꿔도 빈 화면이 나오지 않게** 하는 것이
        목적이라 다섯 카테고리(작업·수정·공유·피드백·AI 조회)만 전부 채운다.
        """
        from apps.agent.models import AgentLookup
        from apps.agent.services.lookup import draw_edge
        from apps.chat.models import ChatMessage
        from apps.chat.services import ensure_project_room
        from apps.documents.models import Document
        from apps.meetings.models import FlowCategory, FlowEdge, FlowSource
        from apps.states.models import WorkItem, WorkStatus

        drawn = 0

        def emit(days_ago, make):
            nonlocal drawn
            scope = FlowEdge.objects.filter(project=project, category=FlowCategory.WORK)
            before = set(scope.values_list("id", flat=True))
            obj = make()
            drawn += scope.exclude(id__in=before).update(
                occurred_at=now - timedelta(days=days_ago))
            return obj

        def work(owner, title, status, progress):
            return lambda: WorkItem.objects.create(
                project=project, owner=owner, title=title,
                status=status, progress=progress)

        def move(item, status, progress):
            def go():
                item.status, item.progress = status, progress
                item.save()
                return item
            return go

        # ── 작업 · 수정 (다섯 명 전원 — 노드를 눌렀을 때 빈 사람이 없게)
        changed = [
            (emit(11, work(users["유수인"], "학술제 포스터 디자인",
                          WorkStatus.IN_PROGRESS, 50)), WorkStatus.DONE, 100),
            (emit(9, work(users["최비성"], "참가 신청 API 구현",
                         WorkStatus.IN_PROGRESS, 60)), WorkStatus.DONE, 100),
        ]
        emit(8, work(users["임수연"], "세션 시간표 페이지 제작", WorkStatus.TODO, 0))
        emit(6, work(users["서재민"], "부스 배치도 작업", WorkStatus.IN_PROGRESS, 35))
        emit(5, work(users["강다은"], "후원사 안내 메시지 작성", WorkStatus.TODO, 0))
        emit(4, work(users["유수인"], "발표자 프로필 카드 디자인", WorkStatus.TODO, 0))
        emit(3, work(users["최비성"], "참가자 명단 엑셀 연동", WorkStatus.IN_PROGRESS, 20))

        for days, (item, status, progress) in zip((4, 2), changed):
            emit(days, move(item, status, progress))

        # ── 공유
        emit(6, lambda: Document.objects.create(
            project=project, owner=users["서재민"], title="연합학술제 운영 계획서",
            category="planning", summary="부스·세션·후원사 운영 전체 일정",
            delivery_context=[
                {"participant_name": "서재민",
                 "utterance": "운영 계획서 초안 올렸습니다. 일정표 확인 부탁드려요.",
                 "url": "https://www.notion.so/bordo/festival-plan"},
                {"participant_name": "임수연",
                 "utterance": "세션 시간표에 맞춰 페이지 구성 반영하겠습니다."}]))

        # ── 피드백 (프로젝트마다 방이 따로다 — ensure_project_room이 이 project 것을 새로 만든다)
        room = ensure_project_room(project)

        def say(sender_name, body):
            return lambda: ChatMessage.objects.create(
                room=room, sender=users[sender_name], sender_name=sender_name,
                body=body, is_important=True)

        emit(5, say("임수연", "세션 시간표에 발표자 사진이 안 뜹니다. 이미지 경로 확인해주세요."))
        emit(3, say("최비성", "참가 신청 폼에 소속 학교 필드를 추가했습니다."))
        emit(2, say("강다은", "후원사 로고 배치가 겹쳐 보입니다."))

        # ── AI 조회 (하나는 유보 — answer를 비워 둔다)
        for days, asker, target, topic, reason, question, answer, source in [
            (3, "유수인", "최비성", "참가 신청 마감일",
             "포스터에 마감일을 넣기 전에 확정된 날짜가 필요했습니다.",
             "참가 신청 마감이 언제로 확정됐습니까?",
             "9월 5일로 확정됐고, API에도 반영했습니다.",
             FlowSource.GITHUB),
            (1, "강다은", "임수연", "세션 시간표 최종본",
             "후원사 안내 문구에 세션 일정을 넣기 전에 최종본인지 확인이 필요했습니다.",
             "세션 시간표가 최종본입니까?",
             "",
             FlowSource.NOTION),
        ]:
            def ask(a=asker, t=target, tp=topic, r=reason, q=question,
                    ans=answer, s=source, d=days):
                return draw_edge(AgentLookup.objects.create(
                    project=project, asker=users[a], target=users[t],
                    topic=tp, reason=r, question=q, answer=ans,
                    source=s, occurred_at=now - timedelta(days=d)))

            emit(days, ask)

        return drawn

    def _seed_payment_module_flow(self, project, users, now):
        """"결제 모듈" 프로젝트의 작업 플로우. `_seed_academic_festival_flow`와
        같은 이유로, main만큼은 아니지만 다섯 카테고리를 전부 채운다."""
        from apps.agent.models import AgentLookup
        from apps.agent.services.lookup import draw_edge
        from apps.chat.models import ChatMessage
        from apps.chat.services import ensure_project_room
        from apps.documents.models import Document
        from apps.meetings.models import FlowCategory, FlowEdge, FlowSource
        from apps.states.models import WorkItem, WorkStatus

        drawn = 0

        def emit(days_ago, make):
            nonlocal drawn
            scope = FlowEdge.objects.filter(project=project, category=FlowCategory.WORK)
            before = set(scope.values_list("id", flat=True))
            obj = make()
            drawn += scope.exclude(id__in=before).update(
                occurred_at=now - timedelta(days=days_ago))
            return obj

        def work(owner, title, status, progress):
            return lambda: WorkItem.objects.create(
                project=project, owner=owner, title=title,
                status=status, progress=progress)

        def move(item, status, progress):
            def go():
                item.status, item.progress = status, progress
                item.save()
                return item
            return go

        # ── 작업 · 수정
        changed = [
            (emit(10, work(users["최비성"], "결제 API 연동",
                          WorkStatus.IN_PROGRESS, 55)), WorkStatus.DONE, 100),
            (emit(8, work(users["서재민"], "정산 로직 구현",
                         WorkStatus.IN_PROGRESS, 40)), WorkStatus.IN_PROGRESS, 70),
        ]
        emit(7, work(users["유수인"], "결제 화면 UI 제작", WorkStatus.TODO, 0))
        emit(6, work(users["임수연"], "환불 플로우 디자인", WorkStatus.TODO, 0))
        emit(5, work(users["강다은"], "결제 실패 알림 문구 작성", WorkStatus.IN_PROGRESS, 30))
        emit(4, work(users["최비성"], "PG사 연동 테스트", WorkStatus.TODO, 0))
        emit(2, work(users["서재민"], "결제 로그 모니터링 구축", WorkStatus.IN_PROGRESS, 15))

        for days, (item, status, progress) in zip((3, 1), changed):
            emit(days, move(item, status, progress))

        # ── 공유
        emit(5, lambda: Document.objects.create(
            project=project, owner=users["최비성"], title="결제 모듈 API 명세",
            category="backend", summary="결제·환불·정산 엔드포인트 계약",
            delivery_context=[
                {"participant_name": "최비성",
                 "utterance": "결제 API 명세 정리했습니다. 응답에 결제수단 코드가 추가됐어요.",
                 "url": "https://github.com/AX-Lions/backend/blob/develop/bordo-openapi.yaml"},
                {"participant_name": "서재민",
                 "utterance": "정산 배치도 이 명세 기준으로 맞추겠습니다."}]))

        # ── 피드백
        room = ensure_project_room(project)

        def say(sender_name, body):
            return lambda: ChatMessage.objects.create(
                room=room, sender=users[sender_name], sender_name=sender_name,
                body=body, is_important=True)

        emit(4, say("임수연", "환불 버튼 위치가 결제 버튼과 너무 가깝습니다."))
        emit(3, say("강다은", "결제 실패 메시지가 너무 딱딱합니다. 톤 조정 부탁드려요."))
        emit(2, say("서재민", "정산 배치가 자정마다 도는데 시간대 확인이 필요합니다."))

        # ── AI 조회 (하나는 유보)
        for days, asker, target, topic, reason, question, answer, source in [
            (3, "유수인", "최비성", "결제 API 응답 형태",
             "결제 화면 UI를 그리기 전에 응답 필드가 확정됐는지 알아야 했습니다.",
             "결제 API 응답 형태가 확정됐습니까?",
             "확정입니다. 응답에 결제수단 코드가 추가됐어요.",
             FlowSource.GITHUB),
            (1, "임수연", "서재민", "정산 반영 소요 시간",
             "환불 플로우 디자인 전에 정산까지 걸리는 시간을 확인해야 했습니다.",
             "환불 처리 후 정산 반영까지 얼마나 걸립니까?",
             "",
             FlowSource.GITHUB),
        ]:
            def ask(a=asker, t=target, tp=topic, r=reason, q=question,
                    ans=answer, s=source, d=days):
                return draw_edge(AgentLookup.objects.create(
                    project=project, asker=users[a], target=users[t],
                    topic=tp, reason=r, question=q, answer=ans,
                    source=s, occurred_at=now - timedelta(days=d)))

            emit(days, ask)

        return drawn

    def _seed_academic_meeting(self, project, users, now):
        """연합학술제의 끝난 회의 하나. main의 회의만큼 촘촘하지는 않지만
        요약·발언·안건·플로우를 전부 갖춘다. 이번엔 강다은을 대리 참석자로
        둬서, 대리 참석이 유수인 말고 다른 사람에게도 똑같이 동작하는 걸
        보여준다."""
        ended_at = now - timedelta(hours=5)
        started_at = ended_at - timedelta(minutes=45)
        meeting = Meeting.objects.create(
            project=project, project_name=project.name,
            title="연합학술제 부스·세션 운영 점검",
            status=MeetingStatus.ENDED, scheduled_at=started_at,
            duration_min=45, discord_channel_id="556677889901",
            created_by=users["유수인"], started_at=started_at, ended_at=ended_at)

        for pname in users:
            delegated = pname == "강다은"
            MeetingParticipant.objects.create(
                meeting=meeting, user=users[pname], user_name=pname,
                attendance=Attendance.DELEGATED if delegated else Attendance.PRESENT,
                delegated=delegated,
                delegate_prompt=("후원사 로고 배치 같은 계약서에 이미 정해진 내용은 "
                                 "바로 답하고, 그 외 새로운 결정은 유보할 것") if delegated else "")

        MeetingSummary.objects.create(
            meeting=meeting,
            discovered_issues=["부스 간 통로 폭이 좁아 혼잡이 예상된다",
                               "후원사 로고 노출 순서에 대한 이견이 있다"],
            changes=["통로 폭 1.5m로 확대", "후원사 로고는 등급순으로 배치"],
            next_plans=["최종 배치도는 9/1까지 공유", "포스터 인쇄는 9/3 진행"],
            one_line="부스 통로를 넓히고 후원사 로고는 등급순으로 배치하기로 했어요.",
            main_opinions=[{"speaker": "최비성", "text": "통로가 좁으면 안전 문제로 이어질 수 있습니다"},
                          {"speaker": "강다은의 Bordo", "text": "후원사 등급 기준은 이미 계약서에 명시돼 있습니다"}])

        def say(minute, pname, body, agent=False):
            Utterance.objects.create(
                meeting=meeting, participant=users[pname],
                participant_name=f"{pname}의 Bordo" if agent else pname,
                body=body, spoken_at=started_at + timedelta(minutes=minute))

        say(0, "유수인", "학술제 준비 점검 회의 시작할게요. 부스 배치랑 후원사 로고 건 보겠습니다.")
        say(2, "최비성", "부스 배치도 초안 봤는데 통로가 너무 좁아요. 사람 몰리면 위험할 것 같습니다.")
        say(4, "임수연", "저도 그 부분 걱정했어요. 통로 폭을 좀 늘리는 게 좋을 것 같아요.")
        say(6, "최비성", "1.5m 정도면 여유 있을 것 같은데 어떠세요?")
        say(7, "유수인", "좋습니다, 1.5m로 조정해서 다시 그려주세요.")
        say(9, "임수연", "그럼 후원사 로고 얘기로 넘어갈게요. 배치 순서를 어떻게 할지 정해야 해요.")
        say(10, "강다은", "후원사 등급 기준은 이미 계약서에 명시돼 있어서, 그 순서대로 배치하면 됩니다.",
            agent=True)
        say(11, "최비성", "그럼 등급순으로 정리해서 배치도에 반영할게요.")
        say(13, "유수인", "최종 배치도는 9월 1일까지 공유해주세요.")
        say(14, "임수연", "포스터 인쇄는 언제 넘어가나요?")
        say(15, "최비성", "포스터는 9월 3일에 인쇄 넣을 예정입니다.")
        say(17, "유수인", "정리하면 통로 넓히고, 로고는 등급순, 배치도는 9/1까지네요. 오늘은 여기까지 할게요.")

        agendas = [
            Agenda.objects.create(
                meeting=meeting, title="부스 배치 통로 폭", sort_order=1,
                content="통로 폭을 1.5m로 확대해 혼잡을 줄인다.",
                direction_label="최비성 → 임수연", status=Agenda.Status.DISCUSSED,
                owner=users["최비성"]),
            Agenda.objects.create(
                meeting=meeting, title="후원사 로고 배치", sort_order=2,
                content="계약서상 등급 기준대로 로고를 배치한다.",
                direction_label="강다은의 Bordo → 최비성", status=Agenda.Status.DISCUSSED,
                owner=users["강다은"], created_by_agent=True),
        ]

        def node(u, agent=False):
            return {"id": f"{u.id}:agent" if agent else str(u.id),
                    "kind": "AGENT" if agent else "USER", "user_id": str(u.id),
                    "name": f"{u.name}의 Bordo" if agent else u.name,
                    "avatar_url": u.avatar_url or None}

        MT = FlowCategory.MEETING
        edges = [
            (FlowContentType.OPINION, "의견", node(users["최비성"]), [node(users["임수연"])],
             agendas[0], Surface.DISCORD, 40),
            (FlowContentType.REQUEST, "요청사항", node(users["최비성"]), [node(users["임수연"])],
             agendas[0], Surface.DISCORD, 39),
            (FlowContentType.CHANGE, "변동사항", node(users["임수연"]), [node(users["최비성"])],
             agendas[0], Surface.SERVICE, 38),
            (FlowContentType.REQUEST, "요청사항", node(users["임수연"]),
             [node(users["강다은"], agent=True)], agendas[1], Surface.DISCORD, 35),
            (FlowContentType.CONCLUSION, "결론", node(users["강다은"], agent=True),
             [node(users["최비성"])], agendas[1], Surface.DISCORD, 34),
            (FlowContentType.SCHEDULE, "일정", node(users["유수인"]),
             [node(users["최비성"]), node(users["임수연"])], None, Surface.SERVICE, 28),
        ]
        for ctype, label, src, dsts, agenda, surface, mins_ago in edges:
            e = FlowEdge.objects.create(
                meeting=meeting, project=project, category=MT, content_type=ctype,
                surface=surface, from_node=src, to_nodes=dsts, label=label,
                direction_label=f"{src['name']} → {', '.join(d['name'] for d in dsts)}",
                participant_ids=[src["user_id"]] + [d["user_id"] for d in dsts],
                agenda=agenda, occurred_at=ended_at - timedelta(minutes=mins_ago))
            e.opacity = e.compute_opacity()
            e.save(update_fields=["opacity"])

    def _seed_payment_meeting(self, project, users, now):
        """결제 모듈의 끝난 회의 하나. 이번엔 서재민을 대리 참석자로 둔다."""
        ended_at = now - timedelta(hours=4)
        started_at = ended_at - timedelta(minutes=40)
        meeting = Meeting.objects.create(
            project=project, project_name=project.name,
            title="결제 모듈 정산 정책 논의",
            status=MeetingStatus.ENDED, scheduled_at=started_at,
            duration_min=40, discord_channel_id="556677889902",
            created_by=users["유수인"], started_at=started_at, ended_at=ended_at)

        for pname in users:
            delegated = pname == "서재민"
            MeetingParticipant.objects.create(
                meeting=meeting, user=users[pname], user_name=pname,
                attendance=Attendance.DELEGATED if delegated else Attendance.PRESENT,
                delegated=delegated,
                delegate_prompt=("정산 주기 관련 결정은 최비성 확인을 받고, "
                                 "그 외는 회의 의견만 정리해 전달할 것") if delegated else "")

        MeetingSummary.objects.create(
            meeting=meeting,
            discovered_issues=["정산 배치가 자정 기준이라 시간대 오차가 생긴다",
                               "환불 안내 문구가 화면마다 다르게 나간다"],
            changes=["정산 기준 시각을 한국 시간 자정으로 통일", "환불 안내 문구를 한 곳에서 관리"],
            next_plans=["정산 배치 스크립트 수정은 다음 주까지", "환불 문구는 임수연이 정리해서 공유"],
            one_line="정산 기준 시각을 통일하고 환불 문구를 한 곳에서 관리하기로 했어요.",
            main_opinions=[{"speaker": "최비성", "text": "정산 시각이 흔들리면 신뢰도가 떨어집니다"},
                          {"speaker": "임수연", "text": "환불 문구가 화면마다 달라서 사용자가 헷갈려해요"}])

        def say(minute, pname, body, agent=False):
            Utterance.objects.create(
                meeting=meeting, participant=users[pname],
                participant_name=f"{pname}의 Bordo" if agent else pname,
                body=body, spoken_at=started_at + timedelta(minutes=minute))

        say(0, "최비성", "결제 모듈 정산·환불 정책 점검할게요.")
        say(2, "최비성", "정산 배치가 자정마다 도는데, 서버 시간대 기준이라 "
                       "실제 한국 시간이랑 몇 분씩 어긋나요.")
        say(4, "서재민", "정산 주기 관련해서는 최비성님 확인을 받아야 하는 부분이라, "
                       "우선 의견만 정리해서 전달드릴게요.", agent=True)
        say(5, "최비성", "네, 한국 시간 자정 기준으로 통일하는 게 맞을 것 같아요.")
        say(6, "서재민", "알겠습니다, 그 의견 그대로 전달드리겠습니다.", agent=True)
        say(8, "임수연", "환불 쪽도 봐야 하는데, 화면마다 환불 안내 문구가 달라서 사용자들이 헷갈려해요.")
        say(10, "최비성", "저희도 그 얘기 나왔었는데, 한 곳에서 관리하는 걸로 정리하죠.")
        say(11, "임수연", "제가 문구 정리해서 공유드릴게요.")
        say(13, "최비성", "감사합니다. 정산 배치 스크립트는 제가 다음 주까지 수정할게요.")
        say(15, "유수인", "정리하면 정산 시각은 자정 통일, 환불 문구는 임수연님이 정리, "
                        "스크립트는 다음 주까지네요.")
        say(16, "최비성", "네 맞습니다.")
        say(17, "서재민", "확인 후에 정산 시각 관련해서는 다시 말씀드리겠습니다.", agent=True)

        agendas = [
            Agenda.objects.create(
                meeting=meeting, title="정산 기준 시각 통일", sort_order=1,
                content="정산 배치 기준 시각을 한국 시간 자정으로 맞춘다.",
                direction_label="최비성 → 서재민의 Bordo", status=Agenda.Status.DISCUSSED,
                owner=users["서재민"], created_by_agent=True),
            Agenda.objects.create(
                meeting=meeting, title="환불 안내 문구 통일", sort_order=2,
                content="환불 문구를 한 곳에서 관리하도록 정리한다.",
                direction_label="임수연 → 최비성", status=Agenda.Status.DISCUSSED,
                owner=users["임수연"]),
        ]

        def node(u, agent=False):
            return {"id": f"{u.id}:agent" if agent else str(u.id),
                    "kind": "AGENT" if agent else "USER", "user_id": str(u.id),
                    "name": f"{u.name}의 Bordo" if agent else u.name,
                    "avatar_url": u.avatar_url or None}

        MT = FlowCategory.MEETING
        edges = [
            (FlowContentType.REQUEST, "요청사항", node(users["최비성"]),
             [node(users["서재민"], agent=True)], agendas[0], Surface.DISCORD, 36),
            (FlowContentType.CHANGE, "변동사항", node(users["서재민"], agent=True),
             [node(users["최비성"])], agendas[0], Surface.DISCORD, 34),
            (FlowContentType.OPINION, "의견", node(users["임수연"]), [node(users["최비성"])],
             agendas[1], Surface.SERVICE, 32),
            (FlowContentType.OPINION, "의견", node(users["임수연"]), [node(users["최비성"])],
             agendas[1], Surface.SERVICE, 30),
            (FlowContentType.CONCLUSION, "결론", node(users["최비성"]), [node(users["임수연"])],
             agendas[1], Surface.DISCORD, 27),
            (FlowContentType.SCHEDULE, "일정", node(users["유수인"]),
             [node(users["최비성"]), node(users["서재민"])], None, Surface.SERVICE, 23),
        ]
        for ctype, label, src, dsts, agenda, surface, mins_ago in edges:
            e = FlowEdge.objects.create(
                meeting=meeting, project=project, category=MT, content_type=ctype,
                surface=surface, from_node=src, to_nodes=dsts, label=label,
                direction_label=f"{src['name']} → {', '.join(d['name'] for d in dsts)}",
                participant_ids=[src["user_id"]] + [d["user_id"] for d in dsts],
                agenda=agenda, occurred_at=ended_at - timedelta(minutes=mins_ago))
            e.opacity = e.compute_opacity()
            e.save(update_fields=["opacity"])

    def _seed_meeting_transcript(self, meeting, users, owner):
        """끝난 회의의 발언 전체. 세 안건(일정 조율 · 디자인 시안 마감 ·
        개발 일정 연장) 순서를 그대로 따라가게 짜서, MeetingSummary의
        discovered_issues·changes·next_plans와 AiBriefing의 used_answers·
        deferred_answers가 실제 대화에서 나온 것처럼 보이게 한다.

        유수인은 이 회의에 DELEGATED로 등록돼 있으므로, 일정 관련 발언은
        본인이 아니라 "유수인의 Bordo"가 한다 — delegate_prompt("일정 관련
        결정은 내 확인을 받도록 할 것")대로 최종 확정은 유보하고 회의에서
        나온 의견만 전달한다. AiBriefing의 deferred_answers와 어긋나면 안 되니,
        여기서 대리인이 개발 일정을 확정해 버리면 안 된다.
        """
        started = meeting.started_at

        def say(minute, pname, body, agent=False):
            name = f"{pname}의 Bordo" if agent else pname
            Utterance.objects.create(
                meeting=meeting, participant=users[pname], participant_name=name,
                body=body, spoken_at=started + timedelta(minutes=minute))

        # ── 안건 1: 회의 일정 조율
        say(0, "최비성", "다들 모이셨네요. 오늘 안건 세 개 보고 시작할게요 — "
                        "회의 일정 조율, 디자인 시안 마감, 개발 일정입니다.")
        say(2, "최비성", "지금 구조로는 팀별 시간대 계산이 매번 어긋납니다.")
        say(4, "임수연", "맞아요, 저도 회의 알림이 제 시간대 기준으로 안 와서 몇 번 놓쳤어요.")
        say(6, "서재민", "슬롯 계산을 서버에서 하나로 통일하면 될 것 같은데, "
                        "프론트마다 다르게 보정하고 있는 게 문제예요.")
        say(8, "최비성", "그럼 서버가 계산한 시각을 그대로 쓰는 걸로 정리할게요.")
        say(9, "임수연", "네, 그러면 저희 쪽 보정 로직은 걷어내겠습니다.")
        say(10, "최비성", "좋습니다. 슬롯 재조정은 제가 다음 주까지 새 시간표로 공유드릴게요.")

        # ── 안건 2: 디자인 시안 마감
        say(15, "임수연", "다음 안건이요 — 디자인 시안이 늦어지면 개발이 통째로 밀려요.")
        say(17, "최비성", "저희도 마감이 계속 밀리는 게 제일 걱정이에요. 이번엔 확실히 날짜를 정하죠.")
        say(19, "임수연", "8월 18일까지 확정할게요. 그 전에 중간 리뷰도 한 번 잡겠습니다.")
        say(21, "강다은", "확정되면 Discord 공지도 같이 올릴게요. 날짜만 알려주세요.")
        say(22, "임수연", "네, 8/18 확정되는 대로 공유드릴게요.")
        say(24, "최비성", "그럼 디자인 시안은 8월 18일 마감으로 정리하겠습니다.")

        # ── 안건 3: 개발 일정 연장 (대리인이 대신 참석)
        say(35, "임수연", "마지막 안건인데요 — 디자인이 늦어진 만큼 개발 일정도 "
                         "1주 정도 미뤄야 할 것 같아요. 유수인님 대리인께 여쭤봐도 될까요?")
        say(36, "유수인", "네, 전달받았습니다. 일정 변경은 제가 유수인님께 확인을 받아야 "
                         "하는 항목이라, 오늘은 회의에서 나온 의견만 정리해 전달드릴게요.",
            agent=True)
        say(37, "서재민", "일정 1주 연장이면 감당 가능합니다.")
        say(38, "최비성", "저도 동의합니다. 1주면 무리 없어요.")
        say(40, "유수인", "그럼 회의에서는 1주 연장으로 의견이 모였다고 정리하겠습니다. "
                         "다만 최종 확정은 유수인님 확인 후에 다시 말씀드리겠습니다.",
            agent=True)
        say(45, "서재민", "확인해주시면 저희는 그 기준으로 마일스톤 다시 잡겠습니다.")
        say(50, "최비성", "오늘 정리하면 — 슬롯 재조정, 시안 8/18 마감, 개발 일정 1주 연장(확인 대기)입니다. "
                        "다음 회의에서는 API 명세 리뷰 이어갈게요.")
        say(52, "임수연", "네 좋습니다, 오늘 여기까지 할게요.")
        say(53, "유수인", "감사합니다. 확인되는 대로 바로 회신드리겠습니다.", agent=True)

    # ═══════════════════════════════════════════ 지금까지 하나도 없던 화면들

    def _seed_tasks(self, team, projects, users, meeting, now):
        """태스크 화면. AI 후보(PENDING_APPROVAL)를 반드시 섞는다 — 승인
        대기 큐가 비어 있으면 설계 1원칙(사람 최종 승인)을 화면에서 보여줄
        방법이 없다."""
        from apps.tasks.models import Task, TaskEvent, TaskStatus

        main, academic, payment = projects
        owner = users["유수인"]

        Task.objects.create(
            project=main, title="결제 API 응답 스키마 정리",
            description="결제수단 코드 추가 반영해 필드 목록 다시 정리",
            status=TaskStatus.TODO, priority="P1",
            assignee=users["최비성"], created_by=users["최비성"],
            due_at=now + timedelta(days=3))
        Task.objects.create(
            project=main, title="우측 패널 반응형 QA",
            status=TaskStatus.IN_PROGRESS, priority="P2",
            assignee=users["임수연"], created_by=users["임수연"])
        Task.objects.create(
            project=payment, title="PG사 연동 테스트 자동화",
            status=TaskStatus.BLOCKED, priority="P2",
            assignee=users["최비성"], created_by=users["서재민"])

        done = Task.objects.create(
            project=main, title="로그인 재발급 로직 구현",
            status=TaskStatus.COMPLETED, priority="P1",
            assignee=users["서재민"], created_by=users["서재민"],
            completed_at=now - timedelta(days=1),
            completion_note="토큰 재발급까지 붙였고 만료 시간은 30분으로 맞췄습니다.")
        TaskEvent.objects.create(
            task=done, actor=users["서재민"], action="complete",
            from_status=TaskStatus.IN_PROGRESS, to_status=TaskStatus.COMPLETED,
            detail={"note": "재발급 로직 구현 완료"})

        rejected = Task.objects.create(
            project=payment, title="결제 모듈 우선순위를 학술제보다 앞으로",
            status=TaskStatus.REJECTED, priority="P0",
            assignee=users["최비성"], created_by=users["서재민"],
            rejected_reason="이번 스프린트는 학술제 마감이 먼저입니다. 다음 스프린트에 다시 올려주세요.")
        TaskEvent.objects.create(
            task=rejected, actor=owner, action="reject",
            from_status=TaskStatus.PENDING_APPROVAL, to_status=TaskStatus.REJECTED,
            detail={"reason": rejected.rejected_reason})

        # AI가 회의 결정을 보고 만든 후보 — 사람이 승인하기 전까지는 TODO가
        # 안 된다는 걸 보여주는 자리다.
        Task.objects.create(
            project=main, title="디자인 시안 8/18 마감 공지 등록",
            description="회의에서 합의된 마감일을 캘린더에 반영",
            status=TaskStatus.PENDING_APPROVAL, priority="P1",
            assignee=users["임수연"], created_by=owner,
            created_by_agent=True, source_meeting=meeting)
        Task.objects.create(
            project=main, title="개발 일정 1주 연장 반영",
            description="회의 결정에 따라 마일스톤 일정을 1주 미룹니다.",
            status=TaskStatus.PENDING_APPROVAL, priority="P2",
            assignee=users["최비성"], created_by=owner,
            created_by_agent=True, source_meeting=meeting)

    def _seed_calendar(self, projects, users, meeting, now):
        """일정 화면. 회의에 딸린 일정 하나, 딸리지 않은 일정 몇 개(마감·집중
        시간)를 섞는다 — related_meeting은 선택 필드라 회의 없이도 일정이
        존재할 수 있다는 걸 보여준다."""
        from apps.calendars.models import (CalendarEvent, EventKind, EventParticipant,
                                           EventStatus, Reminder)

        main, academic, payment = projects

        deadline = CalendarEvent.objects.create(
            project=main, title="디자인 시안 마감", kind=EventKind.DEADLINE,
            status=EventStatus.CONFIRMED,
            start_at=now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1),
            confirmed_by=users["유수인"], confirmed_at=now - timedelta(hours=2))
        for pname in ("유수인", "임수연"):
            EventParticipant.objects.get_or_create(event=deadline, user=users[pname])
        Reminder.objects.get_or_create(
            event=deadline, notification_type=Reminder.Type.T_MINUS_1D,
            defaults={"scheduled_at": deadline.start_at - timedelta(days=1)})

        kickoff = CalendarEvent.objects.create(
            project=payment, title="결제 모듈 킥오프 회의", kind=EventKind.MEETING,
            status=EventStatus.SCHEDULED, start_at=now + timedelta(days=2, hours=1),
            end_at=now + timedelta(days=2, hours=2))
        for pname in ("최비성", "서재민", "유수인"):
            EventParticipant.objects.get_or_create(event=kickoff, user=users[pname])

        booth = CalendarEvent.objects.create(
            project=academic, title="부스 신청 마감", kind=EventKind.DEADLINE,
            status=EventStatus.SCHEDULED, start_at=now + timedelta(days=5, hours=9))
        EventParticipant.objects.get_or_create(event=booth, user=users["강다은"])
        Reminder.objects.get_or_create(
            event=booth, notification_type=Reminder.Type.T_MINUS_1D,
            defaults={"scheduled_at": booth.start_at - timedelta(days=1)})

        focus = CalendarEvent.objects.create(
            project=main, title="집중 작업 시간", kind=EventKind.BLOCK,
            status=EventStatus.CONFIRMED,
            start_at=now.replace(hour=14, minute=0, second=0, microsecond=0),
            end_at=now.replace(hour=16, minute=0, second=0, microsecond=0))
        EventParticipant.objects.get_or_create(event=focus, user=users["최비성"])

        # 끝난 회의에 딸린 일정 — related_meeting이 실제로 채워진 사례.
        linked = CalendarEvent.objects.create(
            project=main, title=meeting.title, kind=EventKind.MEETING,
            status=EventStatus.CONFIRMED, start_at=meeting.started_at,
            end_at=meeting.ended_at, related_meeting=meeting, discord_notified=True)
        for u in users.values():
            EventParticipant.objects.get_or_create(event=linked, user=u)

    def _seed_agent_chat(self, users, meeting, now):
        """나의 AI 대리인 개인 대화. AgentConversation·AgentMessage가
        지금까지 하나도 없어서 이 화면을 열면 완전히 빈 채로 보였다."""
        from apps.agent.models import AgentConversation, AgentMessage, AgentRun

        owner = users["유수인"]

        run = AgentRun.objects.create(
            user=owner, meeting=meeting, status=AgentRun.Status.COMPLETED,
            settings_snapshot=owner.agent_settings.as_snapshot(),
            steps=[{"step": "search", "detail": "회의 요약·안건에서 관련 내용 검색"},
                  {"step": "answer", "detail": "저장된 지시(delegate_prompt) 기준으로 응답 작성"}],
            evidence=[{"kind": "meeting_summary", "meeting_id": str(meeting.id),
                      "excerpt": "디자인 작업을 우선 진행한 후 개발팀에 전달하기로 결정했어요."}],
            result="디자인 시안은 8월 18일까지 확정하기로 했습니다.")

        def send(conversation, sent_at, **kwargs):
            # AgentMessage.sent_at은 auto_now_add=True라 create()에 넘긴
            # 값을 Django가 무시하고 저장 시각으로 덮어쓴다. 여기서 의도한
            # 과거 시각을 남기려면 생성 직후 queryset.update()로 다시
            # 써야 한다 — update()는 save()를 안 거치고 SQL UPDATE를 바로
            # 날려서 auto_now_add를 안 건드린다.
            msg = AgentMessage.objects.create(conversation=conversation, **kwargs)
            AgentMessage.objects.filter(pk=msg.pk).update(sent_at=sent_at)
            return msg

        conv = AgentConversation.objects.create(
            user=owner, title="이번 주 회의 정리해줘",
            last_message_preview="디자인 시안은 8월 18일까지 확정하기로 했습니다.")
        send(conv, now - timedelta(hours=2, minutes=10),
             role=AgentMessage.Role.USER,
             body="이번 주 회의에서 뭐가 정해졌는지 정리해줘.")
        send(conv, now - timedelta(hours=2, minutes=9),
             role=AgentMessage.Role.AGENT, run=run,
             body=("디자인 시안은 8월 18일까지 확정하기로 했고, 개발 일정은 1주 연장하기로 "
                  "했습니다. 다음 회의에서는 API 명세를 다시 봅니다."))
        send(conv, now - timedelta(hours=2),
             role=AgentMessage.Role.USER,
             body="개발 일정 연장은 내가 승인한 거야?")
        send(conv, now - timedelta(hours=1, minutes=59),
             role=AgentMessage.Role.AGENT,
             body=("아직입니다. 일정 수정 자동 승인 설정이 꺼져 있어서 "
                  "제가 결정하지 않고 확인을 요청해 뒀습니다."))

        conv2 = AgentConversation.objects.create(
            user=users["최비성"], title="결제 API 진행 상황 요약",
            last_message_preview="결제수단 코드 추가까지 반영했습니다.")
        send(conv2, now - timedelta(days=1),
             role=AgentMessage.Role.USER,
             body="결제 API 지금 어디까지 됐는지 요약해줘.")
        send(conv2, now - timedelta(days=1) + timedelta(minutes=1),
             role=AgentMessage.Role.AGENT,
             body="결제수단 코드 추가까지 반영했고, PG사 연동 테스트가 남아있습니다.")

    def _seed_debate(self, meeting, users, owner, now):
        """회의 전 준비 — 논쟁점을 미리 예측해 두고, 대리 참석 예정인 사람의
        입장을 미리 받아 둔다. 지금까지 하나도 없어서 준비 화면이 항상 비어
        있었다. 논쟁점 하나는 일부러 입장을 안 받아 둔다 — 유보가 화면에
        어떻게 나오는지 봐야 한다."""
        from apps.meetings.models import DebatePoint, DebateStance

        p1 = DebatePoint.objects.create(
            meeting=meeting, source_key="design-deadline", order=1,
            title="디자인 시안을 이번 주에 확정할까요?",
            options=[{"key": "a", "title": "이번 주 확정", "description": "8/18까지 확정하고 개발 착수"},
                    {"key": "b", "title": "다음 주로 연기", "description": "QA 기간을 더 확보"}],
            rationale="지난 회의에서 8/18 마감 얘기가 나왔지만 최종 확정은 아직 안 됐습니다.",
            evidence=[{"kind": "utterance", "title": "임수연 발언",
                      "body": "시안이 늦어지면 개발이 통째로 밀려요.",
                      "at": (now - timedelta(hours=5)).isoformat(), "who": "임수연"}],
            created_by_agent=True)
        DebateStance.objects.create(
            point=p1, user=owner, option_key="a",
            body="이번 주 확정하겠습니다. 일정이 밀리면 개발 착수가 늦어집니다.")

        DebatePoint.objects.create(
            meeting=meeting, source_key="dev-schedule-extend", order=2,
            title="개발 일정을 1주 연장할까요?",
            options=[{"key": "a", "title": "1주 연장", "description": "디자인 지연을 반영"},
                    {"key": "b", "title": "기존 일정 유지", "description": "다른 작업을 줄여서 맞춤"}],
            rationale="일정 수정 자동 승인 설정이 꺼져 있어 대리인이 스스로 정하지 않습니다.",
            evidence=[], created_by_agent=True)

    def _seed_briefing_cards(self, meeting, agendas, users, owner, now):
        """돌아온 사람이 보는 확인이 필요해요·나에게 요청한 내용 카드.
        AiBriefing(narrative)은 이미 있었지만 이 카드들은 하나도 없었다."""
        from apps.meetings.models import BriefingConfirmation, BriefingRequest
        from apps.tasks.models import Task, TaskStatus

        BriefingConfirmation.objects.create(
            meeting=meeting, user=owner, source_key="schedule-slot",
            title="회의 일정 조율 결과",
            body="시간대가 겹쳐 슬롯을 다시 잡기로 했습니다. 최비성님이 새 슬롯을 공유할 예정입니다.",
            agenda=agendas[0], occurred_at=meeting.ended_at)
        BriefingConfirmation.objects.create(
            meeting=meeting, user=owner, source_key="design-deadline",
            title="디자인 시안 마감일 확정",
            body="8월 18일까지 확정하기로 했습니다.",
            agenda=agendas[1], occurred_at=meeting.ended_at,
            confirmed_at=now - timedelta(hours=2))

        task = Task.objects.create(
            project=meeting.project, title="QA 기간 재검토",
            status=TaskStatus.PENDING_APPROVAL, priority="P2",
            assignee=owner, created_by=owner,
            created_by_agent=True, source_meeting=meeting)
        BriefingRequest.objects.create(
            meeting=meeting, user=owner, source_key="qa-period",
            title="QA 기간 관련 확인 요청", requester_name="서재민",
            note="8/18 마감이면 QA 기간이 3일뿐인데 괜찮을까요?",
            due_at=now + timedelta(days=2), task=task, occurred_at=meeting.ended_at)

    def _seed_chat_rooms(self, team, users, owner, now):
        """팀 전체방·나의 AI 대리인방·1:1방·대리인에게 직접 묻는 방. 지금까지
        프로젝트방 하나만 있었다."""
        from apps.chat.models import ChatMessage, ChatRoom, RoomMember, RoomType
        from apps.chat.services import direct_key, ensure_ai_room, ensure_team_room, peer_agent_key

        team_room = ensure_team_room(team)
        for pname, body in [
            ("강다은", "다음 주 학술제 부스 배치도 공유드립니다."),
            ("유수인", "이번 주 금요일 전체 회고 시간 잡을게요."),
        ]:
            ChatMessage.objects.create(room=team_room, sender=users[pname],
                                       sender_name=pname, body=body)

        ai_room = ensure_ai_room(owner)
        ChatMessage.objects.create(room=ai_room, sender=owner, sender_name=owner.name,
                                   body="오늘 일정 알려줘.")
        ChatMessage.objects.create(
            room=ai_room, sender=owner, sender_name=f"{owner.name}의 Bordo",
            is_agent=True,
            body="오늘 9시 정기 팀 회의, 13시 디자인 리뷰, 17시 개발팀 Sync가 있습니다.")

        # 1:1 방 — 유수인 · 최비성. direct_key가 정렬해서 만드니 누가 먼저
        # 걸어도 같은 방이 된다.
        d_key = direct_key(owner.id, users["최비성"].id)
        direct_room, _ = ChatRoom.objects.get_or_create(
            type=RoomType.DIRECT, dedupe_key=d_key, defaults={"created_by": owner})
        for u in (owner, users["최비성"]):
            RoomMember.objects.get_or_create(room=direct_room, user=u)
        ChatMessage.objects.create(room=direct_room, sender=users["최비성"],
                                   sender_name="최비성", body="결제 API 명세 확인해주실 수 있나요?")
        ChatMessage.objects.create(room=direct_room, sender=owner,
                                   sender_name=owner.name, body="네, 오늘 중으로 볼게요.")

        # 대리인에게 직접 묻는 방 — 서재민이 유수인의 대리인에게. 방향이 있는
        # 키라 peer_agent_key(요청자, 대상)로 만든다.
        p_key = peer_agent_key(users["서재민"].id, owner.id)
        peer_room, _ = ChatRoom.objects.get_or_create(
            type=RoomType.PEER_AGENT, dedupe_key=p_key,
            defaults={"created_by": users["서재민"], "agent_owner": owner})
        for u in (users["서재민"], owner):
            RoomMember.objects.get_or_create(room=peer_room, user=u)
        ChatMessage.objects.create(
            room=peer_room, sender=users["서재민"], sender_name="서재민",
            body="유수인님 대신 여쭤봅니다 — 디자인 시안 오늘 확정되나요?")
        ChatMessage.objects.create(
            room=peer_room, sender=owner, sender_name=f"{owner.name}의 Bordo",
            is_agent=True,
            body="네, 오늘 중 확정 예정이라고 전달받았습니다.")
