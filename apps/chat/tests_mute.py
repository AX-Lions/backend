"""
방별 알림 끄기 (「채팅에서 프론트가 못 붙이는 것들」 중 하나).

채팅 설정 화면에 항목이 이름 변경·나가기 둘뿐이었습니다. 알림 설정이 서버에
없어서 프론트가 스위치를 안 그리고 「아직 서버에 없습니다」로 적어 뒀습니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatRoom, RoomMember, RoomType
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class Base(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                           name="유수인", timezone="Asia/Seoul")
        self.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                             name="강다은", timezone="Europe/Berlin")
        self.gone = User.objects.create_user(email="g@bordo.dev", password="x" * 10,
                                             name="나간 사람")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me, team_role=TeamRole.OWNER)
        for u in (self.mate, self.gone):
            TeamMember.objects.create(team=self.team, user=u, team_role=TeamRole.MEMBER)
        self.project = Project.objects.create(team=self.team, team_name=self.team.name,
                                              name="Bordo", created_by=self.me)
        for u in (self.me, self.mate, self.gone):
            ProjectMember.objects.create(project=self.project, user=u)

        self.room = ChatRoom.objects.create(
            type=RoomType.PROJECT, project=self.project, project_name=self.project.name,
            team=self.team, team_name=self.team.name, title=self.project.name)
        for u in (self.me, self.mate):
            RoomMember.objects.create(room=self.room, user=u)
        RoomMember.objects.create(room=self.room, user=self.gone,
                                  left_at=timezone.now())

        self.client = APIClient()
        self.client.force_authenticate(self.me)


class RoomMuteTest(Base):

    def mute(self, muted):
        r = self.client.patch(f"/api/v1/chat/rooms/{self.room.id}/mute",
                              {"muted": muted}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def room_body(self):
        r = self.client.get("/api/v1/chat/rooms")
        self.assertEqual(r.status_code, 200)
        return next(x for x in r.data["results"] if x["id"] == str(self.room.id))

    def test_default_is_not_muted(self):
        self.assertFalse(self.room_body()["muted"])

    def test_mute_and_unmute(self):
        self.assertTrue(self.mute(True)["muted"])
        self.assertTrue(self.room_body()["muted"])
        self.assertFalse(self.mute(False)["muted"])
        self.assertFalse(self.room_body()["muted"])

    def test_muting_is_per_person(self):
        """한 사람이 껐다고 남의 목록에서도 꺼지면 안 됩니다."""
        self.mute(True)
        self.client.force_authenticate(self.mate)
        self.assertFalse(self.room_body()["muted"])

    def test_missing_flag_is_400(self):
        r = self.client.patch(f"/api/v1/chat/rooms/{self.room.id}/mute", {},
                              format="json")
        self.assertEqual(r.status_code, 400)

    def test_muting_does_not_touch_the_unread_count(self):
        """껐다는 것과 다 읽었다는 것이 화면에서 구별돼야 합니다."""
        from apps.chat.models import ChatMessage

        ChatMessage.objects.create(room=self.room, sender=self.mate,
                                   sender_name="강다은", body="새 메시지")
        before = self.room_body()["unread_count"]
        self.mute(True)
        self.assertEqual(self.room_body()["unread_count"], before)
