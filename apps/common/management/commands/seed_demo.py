"""
데모 시드.

    python manage.py seed_demo

심사 시연과 프론트 개발용입니다. 홈 화면과 플로우 화면이 실제로 채워집니다.
"""
import random
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

PEOPLE = [
    ("susu@bordo.dev", "유수인", "design"),
    ("backend01@bordo.dev", "최비성", "backend"),
    ("front01@bordo.dev", "임수연", "frontend"),
    ("jaemin@bordo.dev", "서재민", "backend"),
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
        for email, name, role in PEOPLE:
            u = User.all_objects.filter(email=email).first()
            if not u:
                u = User.objects.create_user(email=email, password=PASSWORD, name=name,
                                             project_role=role, timezone="Asia/Seoul")
            users[name] = u
            AgentSettings.objects.get_or_create(user=u)

        owner = users["유수인"]
        team, _ = Team.objects.get_or_create(
            name="멋사 중앙해커톤",
            defaults={"created_by": owner, "description": "Bordo 개발팀",
                      "category_keys": ["backend", "frontend", "design"]})
        for i, (_, name, _r) in enumerate(PEOPLE):
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
            for _, pname, _r in PEOPLE:
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
                for _, pname, _r in PEOPLE:
                    MeetingParticipant.objects.get_or_create(
                        meeting=m, user=users[pname],
                        defaults={"user_name": pname})

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
        for i, (_, pname, _r) in enumerate(PEOPLE):
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

        for pname, body in [
            ("최비성", "지금 구조로는 팀별 시간대 계산이 매번 어긋납니다."),
            ("임수연", "시안이 늦어지면 개발이 통째로 밀려요."),
            ("서재민", "일정 1주 연장이면 감당 가능합니다."),
        ]:
            Utterance.objects.create(meeting=meeting, participant=users[pname],
                                     participant_name=pname, body=body,
                                     spoken_at=ended_at - timedelta(minutes=random.randint(5, 50)))

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

        self.stdout.write(self.style.SUCCESS(
            f"\n시드 완료\n"
            f"  로그인   : susu@bordo.dev / {PASSWORD}\n"
            f"  팀       : {team.name} ({team.id})\n"
            f"  프로젝트 : {main.name} ({main.id})\n"
            f"  회의     : {meeting.title} ({meeting.id})\n"
            f"  작업 엣지 : {work_edges}\n"))

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

        for days, (item, status, progress) in zip((3, 2, 1), changed):
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
        ]:
            def ask(a=asker, t=target, tp=topic, r=reason, q=question,
                    ans=answer, s=source, d=days):
                return draw_edge(AgentLookup.objects.create(
                    project=project, asker=users[a], target=users[t],
                    topic=tp, reason=r, question=q, answer=ans,
                    source=s, occurred_at=now - timedelta(days=d)))

            emit(days, ask)

        return drawn
