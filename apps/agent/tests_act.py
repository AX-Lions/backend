"""
쓰기 스킬 테스트.

되돌릴 수 없는 결과를 남기는 자리입니다. 두 가지를 봅니다.

- **발언은 나가되 사람 발언과 섞이지 않는가** (`is_agent`)
- **산출물이 확정되지 않는가** (`PENDING_APPROVAL` · `DRAFT`)
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, OutboxEvent
from apps.agent.services.skills import (ProposeScheduleSkill, ProposeTaskSkill,
                                        SendMessageSkill, SkillContext,
                                        SpeakInMeetingSkill)
from apps.calendars.models import CalendarEvent, EventStatus
from apps.chat.models import ChatMessage, ChatRoom, RoomType
from apps.meetings.models import Meeting, Utterance
from apps.orgs.models import Project, Team
from apps.tasks.models import Task, TaskStatus


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.mate = User.objects.create_user(email="mate@bordo.dev", password="x" * 10,
                                            name="임수연")
        cls.team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=cls.team, team_name="팀",
                                             name="Bordo", created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me,
            discord_channel_id="ch-1")
        # 실제 실행에서는 루프가 AgentRun 을 먼저 만들고 그 id 를 넘깁니다.
        cls.agent_run = AgentRun.objects.create(user=cls.me, meeting=cls.meeting)

    def ctx(self, **kw):
        base = dict(principal_id=str(self.me.id), actor_id=str(self.mate.id),
                    project_id=str(self.project.id),
                    meeting_id=str(self.meeting.id), run_id=str(self.agent_run.id))
        base.update(kw)
        return SkillContext(**base)


class SendMessageTest(Base):

    def setUp(self):
        self.skill = SendMessageSkill()

    def test_sends_and_marks_as_agent(self):
        """사람 발언과 섞이면 나중에 누가 한 말인지 되짚을 수 없습니다."""
        r = self.skill.run({"to_user_id": str(self.mate.id), "body": "진행 중입니다"},
                           self.ctx())
        self.assertTrue(r.ok)
        msg = ChatMessage.objects.get()
        self.assertTrue(msg.is_agent)
        self.assertEqual(msg.sender, self.me)

    def test_uses_peer_agent_room(self):
        self.skill.run({"to_user_id": str(self.mate.id), "body": "x"}, self.ctx())
        self.assertEqual(ChatRoom.objects.get().type, RoomType.PEER_AGENT)

    def test_both_people_are_in_the_room(self):
        """본인 사이드바에서도 대리인이 무슨 말을 했는지 보여야 합니다."""
        self.skill.run({"to_user_id": str(self.mate.id), "body": "x"}, self.ctx())
        room = ChatRoom.objects.get()
        self.assertEqual(
            set(room.memberships.values_list("user_id", flat=True)),
            {self.me.id, self.mate.id})

    def test_reuses_the_same_room(self):
        for _ in range(2):
            self.skill.run({"to_user_id": str(self.mate.id), "body": "x"}, self.ctx())
        self.assertEqual(ChatRoom.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_unknown_target(self):
        import uuid
        r = self.skill.run({"to_user_id": str(uuid.uuid4()), "body": "x"}, self.ctx())
        self.assertEqual(r.error_code, "not_found")

    def test_empty_body(self):
        r = self.skill.run({"to_user_id": str(self.mate.id), "body": " "}, self.ctx())
        self.assertEqual(r.error_code, "validation")


class SpeakInMeetingTest(Base):

    def setUp(self):
        self.skill = SpeakInMeetingSkill()

    def test_goes_through_outbox_not_discord(self):
        """트랜잭션이 롤백돼도 메시지가 이미 나가 있는 상황을 막습니다."""
        r = self.skill.run({"body": "진행 중입니다"}, self.ctx())
        self.assertTrue(r.ok)
        e = OutboxEvent.objects.get()
        self.assertEqual(e.status, OutboxEvent.Status.PENDING)
        self.assertEqual(e.payload["body"], "진행 중입니다")
        self.assertTrue(e.payload["is_agent"])

    def test_channel_is_carried(self):
        self.skill.run({"body": "x"}, self.ctx())
        self.assertEqual(OutboxEvent.objects.get().channel_id, "ch-1")

    def test_same_run_speaks_once(self):
        """회의에 같은 말이 두 번 뜨면 어지럽습니다."""
        self.skill.run({"body": "처음"}, self.ctx())
        r2 = self.skill.run({"body": "다시"}, self.ctx())
        self.assertTrue(r2.ok)
        self.assertTrue(r2.data["duplicate"])
        self.assertEqual(OutboxEvent.objects.count(), 1)

    def test_missing_meeting(self):
        r = self.skill.run({"body": "x"}, self.ctx(meeting_id=None))
        self.assertEqual(r.error_code, "validation")

    # ── 회의록에도 남는가 (이슈 #84)

    def test_leaves_an_utterance(self):
        """
        회의 요약(`build_summary`)과 다음 회의의 논쟁점 예측은 `Utterance` 만
        읽습니다. 여기 안 남으면 자리를 비운 사람을 대신해 한 말이 요약에서
        통째로 빠집니다.
        """
        self.skill.run({"body": "목요일로 당겨 주세요"}, self.ctx())
        u = Utterance.objects.get()
        self.assertEqual(u.meeting, self.meeting)
        self.assertEqual(u.body, "목요일로 당겨 주세요")
        self.assertTrue(u.is_agent)

    def test_utterance_points_at_the_owner(self):
        """누구를 대신한 말인지가 사라지면 돌아온 사람이 자기 몫을 못 찾습니다."""
        self.skill.run({"body": "x"}, self.ctx())
        u = Utterance.objects.get()
        self.assertEqual(u.participant, self.me)
        self.assertEqual(u.participant_name, "서재민의 Bordo")

    def test_utterance_has_spoken_at(self):
        """비어 있으면 정렬(`spoken_at`)에서 회의 맨 앞으로 올라갑니다."""
        self.skill.run({"body": "x"}, self.ctx())
        self.assertIsNotNone(Utterance.objects.get().spoken_at)

    def test_same_run_records_once(self):
        """발송함이 한 번이면 회의록도 한 번이어야 합니다."""
        self.skill.run({"body": "처음"}, self.ctx())
        self.skill.run({"body": "다시"}, self.ctx())
        self.assertEqual(Utterance.objects.count(), 1)

    def test_agent_utterance_does_not_wake_an_agent(self):
        """
        `targeting.candidates()` 는 발언자 본인만 후보에서 뺍니다. 대리인 발언을
        그대로 흘리면 다른 불참자의 대리인이 받아 답하고, 그 답을 또 받습니다.
        """
        from apps.agent.tasks import run_agent_for_utterance

        self.skill.run({"body": "x"}, self.ctx())
        u = Utterance.objects.get()

        with patch("apps.agent.services.targeting.pick") as pick:
            run_agent_for_utterance(str(u.id))
        pick.assert_not_called()


class ProposeTaskTest(Base):

    def setUp(self):
        self.skill = ProposeTaskSkill()

    def test_starts_pending_approval(self):
        """대리인이 바로 TODO 로 넣으면 사람이 모르는 사이에 할 일이 늘어납니다."""
        r = self.skill.run({"title": "인덱스 재측정"}, self.ctx())
        self.assertTrue(r.ok)
        task = Task.objects.get()
        self.assertEqual(task.status, TaskStatus.PENDING_APPROVAL)
        self.assertTrue(task.created_by_agent)

    def test_status_argument_is_ignored(self):
        """모델이 status 를 끼워 넣어도 승인 단계를 건너뛸 수 없어야 합니다."""
        self.skill.run({"title": "x", "status": "TODO"}, self.ctx())
        self.assertEqual(Task.objects.get().status, TaskStatus.PENDING_APPROVAL)

    def test_links_the_source_meeting(self):
        """어느 회의에서 나온 할 일인지 알아야 승인 판단이 됩니다."""
        self.skill.run({"title": "x"}, self.ctx())
        self.assertEqual(Task.objects.get().source_meeting_id, self.meeting.id)

    def test_empty_title(self):
        self.assertEqual(self.skill.run({"title": ""}, self.ctx()).error_code,
                         "validation")


class ProposeScheduleTest(Base):

    def setUp(self):
        self.skill = ProposeScheduleSkill()

    def test_starts_draft(self):
        """POLICY 가 허용해도 확정은 사람이 합니다."""
        r = self.skill.run({"title": "스키마 리뷰",
                            "start_at": "2026-09-07T10:00:00+09:00"}, self.ctx())
        self.assertTrue(r.ok)
        self.assertEqual(CalendarEvent.objects.get().status, EventStatus.DRAFT)

    def test_bad_date_is_normal_failure(self):
        """모델이 만든 날짜는 형식이 어긋나기 쉽습니다. 루프가 죽으면 안 됩니다."""
        r = self.skill.run({"title": "x", "start_at": "내일 오후"}, self.ctx())
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "validation")
        self.assertEqual(CalendarEvent.objects.count(), 0)
