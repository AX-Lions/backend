"""
방 찾기 (「채팅에서 프론트가 못 붙이는 것들」 중 하나).

사이드바 돋보기가 쓰던 것은 검색이 아니라 **이미 받아 온 목록을 좁히는 것**
이었습니다. 방이 늘면 목록에 안 실린 방은 아무리 쳐도 안 나옵니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentSettings
from apps.chat.models import ChatMessage, ChatRoom, RoomMember, RoomType
from apps.chat.services import direct_key
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class RoomSearchTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="m@bordo.dev", password="x" * 10,
                                             name="강다은")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=self.team, user=self.mate,
                                  team_role=TeamRole.MEMBER)
        self.project = Project.objects.create(team=self.team, team_name=self.team.name,
                                              name="결제 모듈", created_by=self.me)
        for u in (self.me, self.mate):
            ProjectMember.objects.create(project=self.project, user=u)

        self.project_room = ChatRoom.objects.create(
            type=RoomType.PROJECT, project=self.project, project_name=self.project.name,
            team=self.team, team_name=self.team.name, title=self.project.name)
        self.direct_room = ChatRoom.objects.create(
            type=RoomType.DIRECT, dedupe_key=direct_key(self.me.id, self.mate.id),
            title="강다은", created_by=self.me)
        for room in (self.project_room, self.direct_room):
            for u in (self.me, self.mate):
                RoomMember.objects.create(room=room, user=u)

        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def search(self, q):
        r = self.client.get("/api/v1/chat/rooms/search", {"q": q})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["results"]

    def titles(self, q):
        return {row["title"] for row in self.search(q)}

    def test_finds_by_room_name(self):
        self.assertEqual(self.titles("결제"), {"결제 모듈"})

    def test_finds_by_project_or_team_name(self):
        self.assertIn("결제 모듈", self.titles("AX Lions"))

    def test_finds_by_message_body(self):
        """방이 늘면 목록에 안 실린 방은 좁히기로는 못 찾습니다."""
        ChatMessage.objects.create(room=self.direct_room, sender=self.mate,
                                   sender_name="강다은", body="환불 정책 확인 부탁드려요")
        self.assertEqual(self.titles("환불"), {"강다은"})

    def test_tells_why_it_matched(self):
        """이름이 안 겹치는데 목록에 뜨면 화면이 이유를 설명할 방법이 없습니다."""
        ChatMessage.objects.create(room=self.direct_room, sender=self.mate,
                                   sender_name="강다은", body="정산 배치 얘기")
        self.assertEqual(self.search("정산")[0]["matched"], "MESSAGE")
        self.assertEqual(self.search("결제")[0]["matched"], "NAME")

    def test_finds_the_agent_room_by_its_displayed_name(self):
        """`{이름}의 Bordo` 는 조회할 때 조립합니다. 저장된 제목으로만 찾으면 안 걸립니다."""
        room = ChatRoom.objects.create(type=RoomType.AI, dedupe_key=f"ai:{self.me.id}",
                                       title="나의 AI 대리인", agent_owner=self.me,
                                       created_by=self.me)
        RoomMember.objects.create(room=room, user=self.me)
        AgentSettings.objects.get_or_create(user=self.me)
        self.assertIn("유수인의 Bordo", self.titles("Bordo"))

    def test_rooms_i_left_are_not_found(self):
        RoomMember.objects.filter(room=self.project_room, user=self.me).update(
            left_at=timezone.now())
        self.assertEqual(self.titles("결제"), set())

    def test_hidden_rooms_are_not_found(self):
        RoomMember.objects.filter(room=self.direct_room, user=self.me).update(
            hidden_at=timezone.now())
        ChatMessage.objects.create(room=self.direct_room, sender=self.mate,
                                   sender_name="강다은", body="숨긴 방의 말")
        self.assertEqual(self.titles("숨긴"), set())

    def test_messages_before_i_joined_are_not_searched(self):
        """검색 결과로 못 볼 대화가 새어 나가면 안 됩니다."""
        old = ChatMessage.objects.create(room=self.project_room, sender=self.mate,
                                         sender_name="강다은", body="옛날 대화")
        ChatMessage.objects.filter(pk=old.pk).update(
            sent_at=timezone.now() - timezone.timedelta(days=2))
        RoomMember.objects.filter(room=self.project_room, user=self.me).update(
            visible_from=timezone.now() - timezone.timedelta(days=1))
        self.assertEqual(self.titles("옛날"), set())

    def test_someone_elses_room_is_not_found(self):
        other = User.objects.create_user(email="x@bordo.dev", password="x" * 10,
                                         name="남")
        room = ChatRoom.objects.create(type=RoomType.DIRECT,
                                       dedupe_key=direct_key(other.id, self.mate.id),
                                       title="남의 방", created_by=other)
        RoomMember.objects.create(room=room, user=other)
        self.assertEqual(self.titles("남의"), set())

    def test_empty_query_is_400(self):
        r = self.client.get("/api/v1/chat/rooms/search", {"q": "  "})
        self.assertEqual(r.status_code, 400)
