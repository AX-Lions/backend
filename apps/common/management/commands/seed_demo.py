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
            (MT, FlowContentType.OPINION, "의견",
             node(users["최비성"]), [node(users["임수연"])], agendas[0], None,
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

            (FlowCategory.WORK, FlowContentType.DOCUMENT, "문서",
             node(users["최비성"]), [node(users["임수연"])], None, doc,
             Surface.SERVICE, 40),
            (FlowCategory.WORK, FlowContentType.PLAN, "계획",
             node(users["서재민"]), [node(owner, agent=True)], None, None,
             Surface.SERVICE, 8),
        ]
        for cat, ctype, label, src, dsts, agenda, document, surface, mins_ago in edges:
            e = FlowEdge.objects.create(
                meeting=meeting, category=cat, content_type=ctype, surface=surface,
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

        self.stdout.write(self.style.SUCCESS(
            f"\n시드 완료\n"
            f"  로그인   : susu@bordo.dev / {PASSWORD}\n"
            f"  팀       : {team.name} ({team.id})\n"
            f"  프로젝트 : {main.name} ({main.id})\n"
            f"  회의     : {meeting.title} ({meeting.id})\n"))
