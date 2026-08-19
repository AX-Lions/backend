"""
내 요청함(`GET /me/inbox`) 테스트.

회의마다 흩어져 있는 `답변 필요` · `확인 필요` · `승인 필요` 를 날짜별로 모읍니다.
보는 것은 셋입니다.

- **브리핑을 읽음으로 만들지 않는가** — 목록을 띄운 것만으로 홈의 브리핑 버튼이
  사라지면 안 됩니다
- **날짜 묶음이 요청자 시간대인가** — 서버 시간대로 자르면 시간대가 다른 팀원이
  같은 회의를 다른 날짜 칸에서 봅니다
- **남의 것이 섞이지 않는가**
"""
from datetime import datetime, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import PendingQuestion
from apps.meetings.models import (AiBriefing, BriefingConfirmation, Meeting,
                                  MeetingStatus)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole
from apps.tasks.models import Task, TaskStatus


class InboxTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="유수인", timezone="Asia/Seoul")
        cls.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                            name="임수연", timezone="Asia/Seoul")
        team = Team.objects.create(name="AX Lions", created_by=cls.me)
        for u in (cls.me, cls.mate):
            TeamMember.objects.create(team=team, user=u, team_role=TeamRole.MEMBER)
        cls.project = Project.objects.create(team=team, team_name="AX Lions",
                                             name="Bordo", created_by=cls.me)
        for u in (cls.me, cls.mate):
            ProjectMember.objects.create(project=cls.project, user=u)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def meeting(self, title="정기 회의", when=None):
        return Meeting.objects.create(
            project=self.project, project_name=self.project.name, title=title,
            status=MeetingStatus.ENDED, scheduled_at=when or timezone.now(),
            ended_at=when or timezone.now(), created_by=self.me)

    def question(self, meeting, user=None):
        return PendingQuestion.objects.create(
            meeting=meeting, target_user=user or self.me, asker=self.mate,
            asker_name="임수연", title="배포일", body="목요일로 당길까요?")

    def confirmation(self, meeting, user=None):
        return BriefingConfirmation.objects.create(
            meeting=meeting, user=user or self.me, source_key="edge:1",
            title="배포일이 바뀌었습니다", occurred_at=timezone.now())

    def task(self, meeting, user=None):
        return Task.objects.create(
            project=self.project, title="리허설 준비", assignee=user or self.me,
            status=TaskStatus.PENDING_APPROVAL, created_by_agent=True,
            source_meeting=meeting)

    def get(self):
        r = self.client.get("/api/v1/me/inbox")
        self.assertEqual(r.status_code, 200)
        return r.data

    # ── 모으기

    def test_empty_when_nothing_is_pending(self):
        self.meeting()
        self.assertEqual(self.get()["groups"], [])

    def test_counts_the_three_kinds(self):
        m = self.meeting()
        self.question(m)
        self.question(m)
        self.confirmation(m)
        t = self.task(m)

        item = self.get()["groups"][0]["items"][0]
        self.assertEqual(item["meeting_id"], str(m.id))
        self.assertEqual(item["needs_answer"], 2)
        self.assertEqual(item["needs_confirm"], 1)
        self.assertEqual(item["needs_approval"], 1)
        self.assertEqual(item["pending_approval_task_ids"], [str(t.id)])
        self.assertTrue(item["urgent"])

    def test_project_label_is_assembled_by_the_server(self):
        """화면이 팀·프로젝트를 다시 조합하면 카드마다 표기가 갈립니다."""
        self.question(self.meeting())
        self.assertEqual(self.get()["groups"][0]["items"][0]["project_label"],
                         "AX Lions · Bordo")

    def test_answered_and_confirmed_drop_out(self):
        m = self.meeting()
        q, c = self.question(m), self.confirmation(m)
        PendingQuestion.objects.filter(pk=q.pk).update(answered_at=timezone.now())
        BriefingConfirmation.objects.filter(pk=c.pk).update(confirmed_at=timezone.now())
        self.assertEqual(self.get()["groups"], [])

    def test_other_peoples_items_are_not_mine(self):
        m = self.meeting()
        self.question(m, user=self.mate)
        self.confirmation(m, user=self.mate)
        self.task(m, user=self.mate)
        self.assertEqual(self.get()["groups"], [])

    def test_approved_task_leaves_the_queue(self):
        m = self.meeting()
        t = self.task(m)
        Task.objects.filter(pk=t.pk).update(status=TaskStatus.TODO)
        self.assertEqual(self.get()["groups"], [])

    # ── 날짜 묶음

    def test_today_is_labelled(self):
        self.question(self.meeting())
        group = self.get()["groups"][0]
        self.assertTrue(group["date_label"].startswith("오늘 · "))

    def test_days_are_grouped_and_newest_first(self):
        now = timezone.now()
        self.question(self.meeting("오늘 회의", when=now))
        self.question(self.meeting("어제 회의", when=now - timezone.timedelta(days=1)))

        groups = self.get()["groups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(groups[0]["date_label"].startswith("오늘 · "))
        self.assertTrue(groups[1]["date_label"].startswith("어제 · "))

    def test_date_key_follows_the_requesters_timezone(self):
        """
        2026-08-19 00:30 KST = 2026-08-18 15:30 UTC.

        서버 시간대로 자르면 한국에서 자정 넘겨 끝난 회의가 전날 칸에 들어가,
        어제를 펼쳐야 오늘 할 일이 나옵니다.
        """
        User.objects.filter(pk=self.me.pk).update(timezone="Asia/Seoul")
        when = datetime(2026, 8, 18, 15, 30, tzinfo=dt_timezone.utc)
        self.question(self.meeting("자정 넘겨 끝난 회의", when=when))
        self.assertEqual(self.get()["groups"][0]["date_key"], "2026-08-19")

    # ── 브리핑을 건드리지 않는다

    def test_does_not_mark_the_briefing_as_read(self):
        """목록을 띄운 것만으로 홈의 `Bordo 브리핑 보러가기` 가 사라지면 안 됩니다."""
        m = self.meeting()
        self.question(m)
        AiBriefing.objects.create(meeting=m, user=self.me, narrative="…")

        self.get()
        self.assertIsNone(AiBriefing.objects.get().read_at)
