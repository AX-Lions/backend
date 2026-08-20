"""
사이드바의 단체방 요약 (「채팅에서 프론트가 못 붙이는 것들」 중 하나).

지금까지 `group_chat_room_id` 만 내려줬습니다. 방 객체가 없어서 「모두 채팅
바로가기」로 연 방의 제목을 대화창이 한 번 더 읽어야 했고, 팀 미읽음 합계에서
단체방 몫을 뺄 수 없어 사이드바를 통째로 다시 읽었습니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatMessage
from apps.chat.services import ensure_project_room, ensure_team_room
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class SidebarGroupRoomTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="t@bordo.dev", password="x" * 10,
                                             name="최비성")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=self.team, user=self.mate,
                                  team_role=TeamRole.MEMBER)
        self.project = Project.objects.create(team=self.team, team_name=self.team.name,
                                              name="결제 모듈", created_by=self.me)
        for u in (self.me, self.mate):
            ProjectMember.objects.create(project=self.project, user=u)
        self.team_room = ensure_team_room(self.team)
        self.project_room = ensure_project_room(self.project)

        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def sidebar(self):
        r = self.client.get("/api/v1/chat/sidebar")
        self.assertEqual(r.status_code, 200)
        return r.data

    def team_node(self):
        return self.sidebar()["teams"][0]

    def test_team_node_carries_the_room(self):
        node = self.team_node()
        self.assertEqual(node["group_chat_room"]["id"], str(self.team_room.id))
        self.assertEqual(node["group_chat_room"]["title"], "AX Lions")

    def test_project_node_carries_the_room(self):
        node = self.team_node()["projects"][0]
        self.assertEqual(node["group_chat_room"]["id"], str(self.project_room.id))
        self.assertEqual(node["group_chat_room"]["title"], "결제 모듈")

    def test_the_old_id_field_is_still_there(self):
        """이 키만 읽는 클라이언트가 있습니다."""
        node = self.team_node()
        self.assertEqual(node["group_chat_room_id"], str(self.team_room.id))

    def test_unread_can_be_subtracted_from_the_team_total(self):
        """
        단체방을 읽었을 때 합계에서 그 몫을 뺄 수 있어야 합니다. 못 빼면
        사이드바를 통째로 다시 읽게 됩니다.
        """
        ChatMessage.objects.create(room=self.team_room, sender=self.mate,
                                   sender_name="최비성", body="공지")
        ChatMessage.objects.create(room=self.project_room, sender=self.mate,
                                   sender_name="최비성", body="결제 얘기")
        node = self.team_node()
        self.assertEqual(node["unread_count"], 2)
        self.assertEqual(node["group_chat_room"]["unread_count"], 1)
        self.assertEqual(node["projects"][0]["group_chat_room"]["unread_count"], 1)
