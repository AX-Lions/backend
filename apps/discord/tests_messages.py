"""
봇이 넘기는 메시지 수신 (`/internal/v1/discord/messages`).

봇은 길드의 **모든** 메시지를 넘깁니다. 그중 회의록이 되어야 하는 것은 진행 중인
회의 스레드의 발언뿐이고, 나머지는 버립니다. 버리는 방식이 중요합니다 —
400 으로 세우면 평소 잡담 한 줄마다 오류가 쌓여 진짜 오류가 묻힙니다.
"""
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.meetings.models import Meeting, MeetingStatus, Utterance
from apps.orgs.models import Project, Team

TOKEN = "test-only-token"
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS, CELERY_TASK_ALWAYS_EAGER=False)
class DiscordMessagesTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="강다은", discord_user_id="dc-me")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="Bordo", created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="정기 회의",
            status=MeetingStatus.ACTIVE, scheduled_at=timezone.now(),
            discord_channel_id="thread-1", created_by=cls.me)
        # 웹에서 만든 회의. `discord_channel_id` 가 빈 문자열입니다.
        cls.web_meeting = Meeting.objects.create(
            project=cls.project, project_name=cls.project.name, title="웹 회의",
            status=MeetingStatus.ACTIVE, scheduled_at=timezone.now(),
            created_by=cls.me)

    def post(self, payload):
        return self.client.post("/internal/v1/discord/messages", payload,
                                content_type="application/json",
                                HTTP_X_SERVICE_TOKEN=TOKEN)

    def body(self, **kw):
        base = {"guild_id": "guild-1", "channel_id": "ch-1",
                "author_discord_id": "dc-me", "content": "안녕하세요",
                "created_at": timezone.now().isoformat(),
                "thread_id": "thread-1"}
        base.update(kw)
        return base

    def test_records_an_utterance_in_an_active_meeting(self):
        r = self.post(self.body())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Utterance.objects.get().meeting, self.meeting)

    def test_message_outside_a_thread_is_skipped_not_rejected(self):
        """
        스레드가 아닌 채널이면 봇이 `thread_id: null` 을 보냅니다. 400 을 내면
        평소 대화 한 줄마다 오류가 쌓입니다.
        """
        r = self.post(self.body(thread_id=None))
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["skipped"], "not_a_thread")
        self.assertFalse(Utterance.objects.exists())

    def test_blank_thread_does_not_leak_into_a_web_meeting(self):
        """
        빈 문자열로 조회하면 `discord_channel_id=""` 인 웹 회의가 잡힙니다.
        발언이 남의 회의로 새어 들어가면 그 회의 요약이 통째로 오염됩니다.
        """
        r = self.post(self.body(thread_id="  "))
        self.assertEqual(r.status_code, 202)
        self.assertFalse(Utterance.objects.filter(meeting=self.web_meeting).exists())

    def test_message_in_a_thread_without_an_active_meeting_is_skipped(self):
        r = self.post(self.body(thread_id="thread-없음"))
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["skipped"], "no_active_meeting")

    def test_empty_body_is_skipped(self):
        r = self.post(self.body(content="   "))
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["skipped"], "empty")
