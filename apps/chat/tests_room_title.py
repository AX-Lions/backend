"""
대리인 방 이름.

`AI 대리인` 은 화면 어디에도 없는 낱말입니다(`CLAUDE.md` 의 호칭 규칙). 저장된
제목을 그대로 쓰면 목록에만 그 낱말이 남고, 개인 설정에서 이름을 바꿔도 방
제목은 옛 이름으로 남아 사용자는 저장이 안 된 줄 압니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentSettings
from apps.orgs.models import Team, TeamMember, TeamRole


class AgentRoomTitleTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="t1@bordo.dev", password="x" * 10,
                                          name="유수인")
        cls.mate = User.objects.create_user(email="t2@bordo.dev", password="x" * 10,
                                            name="서재민")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.mate, team_role=TeamRole.MEMBER)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _my_room(self):
        return self.client.get("/api/v1/chat/sidebar").data["my_agent_room"]

    def test_my_agent_room_uses_the_default_name(self):
        self.assertEqual(self._my_room()["title"], "유수인의 Bordo")

    def test_renaming_the_agent_renames_the_room(self):
        """
        저장값이 아니라 조회 시점에 맞춥니다. 어차피 이름을 못 바꾸는 방이라
        저장된 제목을 지킬 이유가 없습니다.
        """
        AgentSettings.objects.update_or_create(user=self.me,
                                               defaults={"agent_name": "제로"})
        self.assertEqual(self._my_room()["title"], "제로")

        AgentSettings.objects.filter(user=self.me).update(agent_name="")
        self.assertEqual(self._my_room()["title"], "유수인의 Bordo")

    def test_peer_agent_room_is_named_after_its_owner(self):
        AgentSettings.objects.create(user=self.mate)
        r = self.client.post("/api/v1/chat/rooms",
                             {"type": "PEER_AGENT", "member_ids": [str(self.mate.id)]},
                             format="json")
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(r.data["title"], "서재민의 Bordo")

    def test_plain_rooms_keep_their_stored_title(self):
        """대리인 방이 아니면 저장된 제목 그대로입니다."""
        r = self.client.post("/api/v1/chat/rooms",
                             {"type": "DIRECT", "member_ids": [str(self.mate.id)]},
                             format="json")
        self.assertEqual(r.data["title"], self.mate.display_name)
