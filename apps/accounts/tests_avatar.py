"""
사진이 없을 때 어떤 값이 나가는가 (이슈 #120).

같은 뜻인데 어떤 자리는 `null` 이고 어떤 자리는 빈 문자열이었습니다. 화면이
"사진 없음" 을 두 가지로 판정하게 되어, 기본 얼굴을 그릴지 말지를 자리마다
다시 정해야 합니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatMessage, ChatRoom, RoomMember, RoomType
from apps.chat.services import direct_key
from apps.orgs.models import Team, TeamMember, TeamRole


class AvatarShapeTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="b@bordo.dev", password="x" * 10,
                                             name="최비성", avatar_url="")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me,
                                  team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=self.team, user=self.mate,
                                  team_role=TeamRole.MEMBER)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_my_profile_gives_null(self):
        r = self.client.get("/api/v1/users/me")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["avatar_url"])

    def test_team_member_gives_null(self):
        r = self.client.get(f"/api/v1/teams/{self.team.id}/members")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(m["avatar_url"] is None for m in r.data["results"]))

    def test_message_sender_gives_null(self):
        room = ChatRoom.objects.create(type=RoomType.DIRECT,
                                       dedupe_key=direct_key(self.me.id, self.mate.id),
                                       created_by=self.me)
        for u in (self.me, self.mate):
            RoomMember.objects.create(room=room, user=u)
        ChatMessage.objects.create(room=room, sender=self.mate,
                                   sender_name="최비성", body="안녕하세요")

        r = self.client.get(f"/api/v1/chat/rooms/{room.id}/messages")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["results"][0]["sender"]["avatar_url"])

    def test_a_real_url_survives(self):
        # `force_authenticate` 가 들고 있는 인스턴스를 그대로 쓰므로
        # `.update()` 로는 안 바뀝니다.
        self.me.avatar_url = "https://cdn.bordo.dev/u/1.png"
        self.me.save(update_fields=["avatar_url"])
        r = self.client.get("/api/v1/users/me")
        self.assertEqual(r.data["avatar_url"], "https://cdn.bordo.dev/u/1.png")
