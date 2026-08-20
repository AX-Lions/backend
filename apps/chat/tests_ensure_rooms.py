"""
단체방·대리인방을 찾고 만드는 자리 (이슈 #50).

세 함수가 각자 `try/IntegrityError` 를 손으로 적고 있었습니다. 모양이 갈리면
**어떤 방은 소프트 삭제된 행을 되살리고 어떤 방은 제약 위반으로 터집니다.**
`apps/common/db.py` 의 `ensure_row()` 하나로 모았습니다.
"""
from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import ChatRoom, RoomType
from apps.chat.services import ensure_ai_room, ensure_project_room, ensure_team_room
from apps.orgs.models import Project, ProjectMember, Team, TeamMember, TeamRole


class EnsureRoomTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="e@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="f@bordo.dev", password="x" * 10,
                                             name="최비성")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=self.team, user=self.mate,
                                  team_role=TeamRole.MEMBER)
        self.project = Project.objects.create(team=self.team, team_name=self.team.name,
                                              name="Bordo", created_by=self.me)
        for u in (self.me, self.mate):
            ProjectMember.objects.create(project=self.project, user=u)

    def test_team_room_is_made_once(self):
        first = ensure_team_room(self.team)
        self.assertEqual(ensure_team_room(self.team).id, first.id)
        self.assertEqual(ChatRoom.all_objects.filter(type=RoomType.TEAM).count(), 1)

    def test_team_room_pulls_in_everyone(self):
        room = ensure_team_room(self.team)
        self.assertEqual(room.memberships.count(), 2)

    def test_project_room_is_made_once(self):
        first = ensure_project_room(self.project)
        self.assertEqual(ensure_project_room(self.project).id, first.id)

    def test_project_room_follows_a_renamed_project(self):
        """이름이 바뀌었는데 방 제목만 옛 이름이면 저장이 안 된 줄 압니다."""
        ensure_project_room(self.project)
        Project.objects.filter(pk=self.project.pk).update(name="새 이름")
        self.project.refresh_from_db()
        self.assertEqual(ensure_project_room(self.project).project_name, "새 이름")

    def test_ai_room_is_made_once_per_person(self):
        first = ensure_ai_room(self.me)
        self.assertEqual(ensure_ai_room(self.me).id, first.id)
        self.assertNotEqual(ensure_ai_room(self.mate).id, first.id)

    def test_a_soft_deleted_room_comes_back_instead_of_blowing_up(self):
        """
        지운 방이 유니크 제약을 그대로 차지합니다. 새로 만들려 들면 터지고,
        억지로 우회하면 같은 대화가 방 두 개로 갈라집니다.
        """
        room = ensure_team_room(self.team)
        room.soft_delete()
        self.assertFalse(ChatRoom.objects.filter(pk=room.id).exists())
        self.assertTrue(ChatRoom.all_objects.filter(pk=room.id).exists())
        self.assertEqual(ensure_team_room(self.team).id, room.id)
