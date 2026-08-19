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
                                          name="강다은", discord_user_id="1234567890")
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
                "author_discord_id": "1234567890", "content": "안녕하세요",
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

    # ── 멘션

    def test_mentions_become_names(self):
        """
        `targeting.pick` 이 원문만 봅니다. 스노플레이크 숫자를 한국어 이름
        목록과 맞춰야 하면 대개 `아무에게도 향하지 않음` 을 골라, 멘션으로 부른
        대리인이 입을 안 엽니다.
        """
        self.post(self.body(content="<@1234567890> 이거 어떻게 생각해?",
                            mentions=["1234567890"]))
        self.assertEqual(Utterance.objects.get().body, "강다은 이거 어떻게 생각해?")

    def test_nickname_form_is_handled(self):
        """별명이 있으면 `<@!123>` 으로 옵니다."""
        self.post(self.body(content="<@!1234567890> 확인 부탁", mentions=["1234567890"]))
        self.assertEqual(Utterance.objects.get().body, "강다은 확인 부탁")

    def test_unlinked_person_is_left_alone(self):
        """지우면 누구를 부른 것인지 사라지고, 아무 이름이나 넣으면 없는 사람이 등장합니다."""
        self.post(self.body(content="<@999999999> 저기요", mentions=["999999999"]))
        self.assertEqual(Utterance.objects.get().body, "<@999999999> 저기요")

    def test_works_without_the_mentions_field(self):
        """옛 봇은 이 키를 안 보냅니다. 본문에서 찾아냅니다."""
        self.post(self.body(content="<@1234567890> 안녕", mentions=None))
        self.assertEqual(Utterance.objects.get().body, "강다은 안녕")

    def test_empty_body_is_skipped(self):
        r = self.post(self.body(content="   "))
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["skipped"], "empty")
