"""
방 머리 시계 줄 · 자리 상태 · 부재 중 대리 응답 (이슈 #116).

화면은 다 그려져 있고 목 데이터로 돌고 있었습니다. 서버가 줘야 하는 것은
**시각이 아니라 그 계산에 넣을 재료**입니다 — 시각은 보는 사람의 지금이
기준이라 1분마다 서버를 부를 값이 아닙니다.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatMessage, ChatRoom, RoomMember, RoomType
from apps.chat.services import direct_key


def direct_room(a, b):
    room = ChatRoom.objects.create(type=RoomType.DIRECT,
                                   dedupe_key=direct_key(a.id, b.id), created_by=a)
    for u in (a, b):
        RoomMember.objects.create(room=room, user=u)
    return room


class RoomMembersTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                           name="유수인", timezone="Asia/Seoul")
        self.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                             name="강다은", timezone="Europe/Berlin")
        self.room = direct_room(self.me, self.mate)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def members(self):
        r = self.client.get("/api/v1/chat/rooms")
        self.assertEqual(r.status_code, 200)
        return r.data["results"][0]["members"]

    def test_members_carry_timezone_and_country(self):
        """`Europe/Berlin` → `독일` 은 표를 들고 있어야 하는 변환입니다."""
        by_name = {m["name"]: m for m in self.members()}
        self.assertEqual(by_name["강다은"]["timezone"], "Europe/Berlin")
        self.assertEqual(by_name["강다은"]["country"], "독일")
        self.assertEqual(by_name["유수인"]["country"], "대한민국")

    def test_members_mark_me(self):
        by_name = {m["name"]: m for m in self.members()}
        self.assertTrue(by_name["유수인"]["is_me"])
        self.assertFalse(by_name["강다은"]["is_me"])

    def test_members_carry_presence_and_agent_name(self):
        User.objects.filter(pk=self.mate.pk).update(presence="AWAY")
        by_name = {m["name"]: m for m in self.members()}
        self.assertEqual(by_name["강다은"]["presence"], "AWAY")
        self.assertEqual(by_name["강다은"]["agent_name"], "강다은의 Bordo")

    def test_direct_room_has_members_even_without_a_team(self):
        """1:1 방은 팀에 안 매달려 있어 팀 구성원 목록으로 대신할 수 없습니다."""
        self.assertIsNone(self.room.team_id)
        self.assertEqual(len(self.members()), 2)


class PresenceTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="p@bordo.dev", password="x" * 10,
                                           name="최비성")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_default_is_active(self):
        r = self.client.get("/api/v1/me/presence")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "ACTIVE")

    def test_switch_to_away_is_kept_on_the_server(self):
        """브라우저에 두면 창을 닫는 순간 대리인이 다시 조용해집니다."""
        r = self.client.patch("/api/v1/me/presence", {"status": "AWAY"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.me.refresh_from_db()
        self.assertEqual(self.me.presence, "AWAY")
        self.assertIsNotNone(self.me.presence_at)

    def test_unknown_status_is_400(self):
        """중간값을 만들면 그 상태에서 대리인이 어떻게 행동할지 아무도 모릅니다."""
        r = self.client.patch("/api/v1/me/presence", {"status": "BUSY"}, format="json")
        self.assertEqual(r.status_code, 400)


class AwayHandledTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="b@bordo.dev", password="x" * 10,
                                             name="서재민")
        self.room = direct_room(self.me, self.mate)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def agent_says(self, body, away=True, minutes_ago=0):
        msg = ChatMessage.objects.create(
            room=self.room, sender=self.me, sender_name="유수인의 Bordo",
            is_agent=True, body=body, answered_while_away=away)
        # `sent_at` 은 `auto_now_add` 라 한 테스트 안에서 만든 두 건이 같은
        # 시각으로 찍힙니다. 최신 한 건을 고르는 것을 보려면 시각을 벌려야
        # 합니다.
        ChatMessage.objects.filter(pk=msg.pk).update(
            sent_at=timezone.now() - timedelta(minutes=minutes_ago))
        return msg

    def get(self):
        r = self.client.get("/api/v1/chat/away-handled")
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_empty_when_nothing_was_handled(self):
        self.assertEqual(self.get()["results"], [])

    def test_groups_by_room_and_counts(self):
        """방마다 받아 세게 두면 목록 하나 그리려고 방 수만큼 요청이 나갑니다."""
        self.agent_says("첫 응답", minutes_ago=5)
        self.agent_says("둘째 응답", minutes_ago=1)
        body = self.get()["results"][0]
        self.assertEqual(body["room_id"], str(self.room.id))
        self.assertEqual(body["handled_count"], 2)
        self.assertEqual(body["last_reply"]["preview"], "둘째 응답")

    def test_agent_message_sent_while_present_is_excluded(self):
        """옆에서 시켜서 한 말은 없는 동안 오간 대화가 아닙니다."""
        self.agent_says("시켜서 한 말", away=False)
        self.assertEqual(self.get()["results"], [])

    def test_my_own_typing_is_excluded(self):
        ChatMessage.objects.create(room=self.room, sender=self.me,
                                   sender_name="유수인", body="내가 쓴 말")
        self.assertEqual(self.get()["results"], [])


class MessageOwnershipTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="c@bordo.dev", password="x" * 10,
                                             name="임수연")
        self.room = direct_room(self.me, self.mate)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def messages(self):
        r = self.client.get(f"/api/v1/chat/rooms/{self.room.id}/messages")
        self.assertEqual(r.status_code, 200)
        return r.data["results"]

    def test_my_agents_message_is_not_mine_but_is_from_my_agent(self):
        """
        자리는 내 쪽, 편집 권한은 없음.

        `is_mine` 을 켜면 대리인 발언에 `수정`·`삭제` 가 붙는데 눌러 봐야 서버가
        거절합니다 — 내가 쓴 문장이 아니기 때문입니다.
        """
        ChatMessage.objects.create(room=self.room, sender=self.me,
                                   sender_name="유수인의 Bordo",
                                   is_agent=True, body="대신 답했습니다")
        m = self.messages()[0]
        self.assertFalse(m["is_mine"])
        self.assertTrue(m["is_from_my_agent"])

    def test_my_own_message_is_mine(self):
        ChatMessage.objects.create(room=self.room, sender=self.me,
                                   sender_name="유수인", body="내가 씀")
        m = self.messages()[0]
        self.assertTrue(m["is_mine"])
        self.assertFalse(m["is_from_my_agent"])

    def test_other_persons_agent_is_neither(self):
        ChatMessage.objects.create(room=self.room, sender=self.mate,
                                   sender_name="임수연의 Bordo",
                                   is_agent=True, body="상대 대리인")
        m = self.messages()[0]
        self.assertFalse(m["is_mine"])
        self.assertFalse(m["is_from_my_agent"])
