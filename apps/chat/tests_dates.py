"""
채팅의 날짜 기준.

서버 `TIME_ZONE` 은 UTC 입니다. 그것으로 하루를 자르면 **한국에서 자정 넘어
보낸 말이 전날로 묶입니다.** 화면은 브라우저 시간대로 날짜 구분선을 그리므로,
그 구분선을 눌러도 방금 본 메시지가 안 나옵니다.

시간대가 다른 팀이 이 서비스의 전제라 서버 시간대 고정은 답이 될 수 없습니다 —
같은 방을 서울에서 보는 사람과 베를린에서 보는 사람이 서로 다른 날짜로 묶어야
각자 자기 하루를 봅니다.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole

from .models import ChatMessage, ChatRoom, RoomMember, RoomType

#: 서울에서 2026-08-18 00:30 에 보낸 말. UTC 로는 전날 15:30 입니다.
#: 베를린(UTC+2)에서는 같은 순간이 2026-08-17 17:30 입니다.
LATE_NIGHT = datetime(2026, 8, 17, 15, 30, tzinfo=dt_timezone.utc)


class ChatDateScopeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.seoul = User.objects.create_user(email="seoul@bordo.dev", password="x" * 10,
                                             name="유수인", timezone="Asia/Seoul")
        cls.berlin = User.objects.create_user(email="berlin@bordo.dev", password="x" * 10,
                                              name="서재민", timezone="Europe/Berlin")
        team = Team.objects.create(name="팀", created_by=cls.seoul)
        TeamMember.objects.create(team=team, user=cls.seoul, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.berlin, team_role=TeamRole.MEMBER)
        project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                         created_by=cls.seoul)
        ProjectMember.objects.create(project=project, user=cls.seoul)
        ProjectMember.objects.create(project=project, user=cls.berlin)

        cls.room = ChatRoom.objects.create(type=RoomType.PROJECT, project=project,
                                           project_name="프로젝트", title="프로젝트",
                                           dedupe_key=f"project:{project.id}",
                                           created_by=cls.seoul)
        RoomMember.objects.create(room=cls.room, user=cls.seoul)
        RoomMember.objects.create(room=cls.room, user=cls.berlin)

        msg = ChatMessage.objects.create(room=cls.room, sender=cls.seoul,
                                         sender_name="유수인", body="자정 넘어 보낸 말")
        ChatMessage.objects.filter(pk=msg.pk).update(sent_at=LATE_NIGHT)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _bodies(self, client, date):
        r = client.get(f"/api/v1/chat/rooms/{self.room.id}/messages", {"date": date})
        self.assertEqual(r.status_code, 200)
        return [m["body"] for m in r.data["results"]]

    def test_day_jump_follows_the_readers_timezone(self):
        """
        서울에서는 18일, 베를린에서는 17일입니다. 같은 메시지인데 각자 자기
        달력의 그 날에서 찾을 수 있어야 합니다.
        """
        self.assertIn("자정 넘어 보낸 말", self._bodies(self._as(self.seoul), "2026-08-18"))
        self.assertEqual(self._bodies(self._as(self.seoul), "2026-08-17"), [])

        self.assertIn("자정 넘어 보낸 말", self._bodies(self._as(self.berlin), "2026-08-17"))
        self.assertEqual(self._bodies(self._as(self.berlin), "2026-08-18"), [])

    def test_active_dates_follow_the_readers_timezone(self):
        """
        달력이 검게 칠하는 날입니다. 여기가 어긋나면 대화가 있는 날이 회색으로
        남아 **누를 수가 없습니다.**
        """
        seoul = self._as(self.seoul).get(
            f"/api/v1/chat/rooms/{self.room.id}/active-dates", {"month": "2026-08"}).data
        self.assertEqual(seoul["active_dates"], ["2026-08-18"])

        berlin = self._as(self.berlin).get(
            f"/api/v1/chat/rooms/{self.room.id}/active-dates", {"month": "2026-08"}).data
        self.assertEqual(berlin["active_dates"], ["2026-08-17"])

    def test_search_hit_points_at_a_day_that_actually_opens(self):
        """
        결과를 누르면 이 `date` 로 목록을 다시 부릅니다. `?date=` 를 자르는
        기준과 다르면 눌러도 빈 날이 열립니다.
        """
        client = self._as(self.seoul)
        hit = client.get(f"/api/v1/chat/rooms/{self.room.id}/search",
                         {"q": "자정"}).data["results"][0]
        self.assertEqual(hit["date"], "2026-08-18")
        self.assertIn("자정 넘어 보낸 말", self._bodies(client, hit["date"]))

    def test_daily_summary_counts_the_readers_day(self):
        client = self._as(self.seoul)
        body = client.get(f"/api/v1/chat/rooms/{self.room.id}/daily-summary",
                          {"date": "2026-08-18"}).data
        self.assertEqual(body["message_count"], 1)
        self.assertEqual(body["status"], "PENDING")

    def test_has_newer_looks_past_the_readers_midnight(self):
        """
        `has_older` · `has_newer` 도 같은 경계를 써야 합니다. 하루의 끝이
        서로 다르면 다음 날이 있는데 없다고 나옵니다.
        """
        later = ChatMessage.objects.create(room=self.room, sender=self.seoul,
                                           sender_name="유수인", body="다음 날")
        # 서울 기준 8/19 09:00
        ChatMessage.objects.filter(pk=later.pk).update(
            sent_at=LATE_NIGHT + timedelta(days=1, hours=9))
        r = self._as(self.seoul).get(f"/api/v1/chat/rooms/{self.room.id}/messages",
                                     {"date": "2026-08-18"})
        self.assertTrue(r.data["has_newer"])
        self.assertFalse(r.data["has_older"])
