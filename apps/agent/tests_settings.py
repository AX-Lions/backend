"""
개인 설정 화면이 저장하는 것.

말투와 대리인 이름은 화면에 칸이 있는데 서버에 담을 자리가 없어서, PATCH 가
모르는 키를 조용히 버리고 200 을 돌려주고 있었습니다. 사용자는 바꿨다고 믿고
회의에 들어가는데 대리인은 옛 이름·옛 말투로 나옵니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orgs.models import Team, TeamMember, TeamRole

from .models import AgentSettings, AgentSettingsVersion, AgentTone
from .services import flow, prompts


class AgentSettingsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="유수인")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _patch(self, **body):
        return self.client.patch("/api/v1/me/agent/settings", body, format="json")

    def test_tone_is_saved(self):
        r = self._patch(tone="CONCISE")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["settings"]["tone"], "CONCISE")
        self.assertEqual(self.client.get("/api/v1/me/agent/settings").data["tone"],
                         "CONCISE")

    def test_unknown_tone_is_rejected(self):
        """
        조용히 버리면 화면은 성공 문구를 띄우는데 값은 안 바뀝니다. 사용자는
        자기가 고른 말투로 대리인이 말한다고 믿게 됩니다.
        """
        r = self._patch(tone="SHOUTY")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["error"]["code"], "VALIDATION_ERROR")

    def test_agent_name_round_trips(self):
        """
        화면이 **보낸 값과 돌려받은 값이 같은지**까지 확인합니다. 키가 없거나
        다르면 실패로 알립니다 — 그러라고 만든 검증이라 정확히 되돌려 줘야 합니다.
        """
        r = self._patch(agent_name="제로")
        self.assertEqual(r.data["settings"]["agent_name"], "제로")
        self.assertEqual(r.data["settings"]["agent_display_name"], "제로")

    def test_blank_agent_name_falls_back_to_the_default(self):
        """
        비우면 `{이름}의 Bordo`. 조립은 **서버 한 곳에서만** 합니다 — 화면마다
        붙이면 사람 이름을 바꿨을 때 어떤 화면은 옛 이름으로 남습니다.
        """
        self._patch(agent_name="제로")
        r = self._patch(agent_name="")
        self.assertEqual(r.data["settings"]["agent_name"], "")
        self.assertEqual(r.data["settings"]["agent_display_name"], "유수인의 Bordo")

    def test_tone_and_name_do_not_bump_the_judgement_version(self):
        """
        말투·호칭은 판정이 아니라 표현입니다. 버전을 올리면 `그때 그 기준으로
        판정했다` 를 되짚을 때 아무 기준도 안 바뀐 버전이 사이사이 끼어듭니다.
        """
        # 설정 행은 처음 읽을 때 생깁니다.
        before = self.client.get("/api/v1/me/agent/settings").data["active_version"]
        r = self._patch(tone="FRIENDLY", agent_name="제로")
        self.assertEqual(r.data["settings"]["active_version"], before)
        self.assertFalse(AgentSettingsVersion.objects.filter(user=self.me).exists())

    def test_policy_switch_still_bumps(self):
        before = self.client.get("/api/v1/me/agent/settings").data["active_version"]
        r = self._patch(mention_feasibility=False)
        self.assertEqual(r.data["settings"]["active_version"], before + 1)
        self.assertTrue(AgentSettingsVersion.objects.filter(user=self.me).exists())

    def test_snapshot_carries_the_tone(self):
        """실행 스냅샷에 남아야 나중에 그 발언이 왜 그 투였는지 되짚습니다."""
        self._patch(tone="CONCISE")
        snap = AgentSettings.objects.get(user=self.me).as_snapshot()
        self.assertEqual(snap["tone"], AgentTone.CONCISE)

    def test_tone_reaches_the_prompt(self):
        """저장만 되고 프롬프트에 안 실리면 말투 칸은 장식입니다."""
        system = prompts.build_system("유수인", tone="CONCISE")
        self.assertIn("짧게", system)
        self.assertNotIn("짧게", prompts.build_system("유수인", tone="FORMAL"))


class AgentNamingTest(TestCase):
    """호칭이 화면 여러 곳에 같은 값으로 나가는지."""

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="n1@bordo.dev", password="x" * 10,
                                          name="유수인")
        cls.mate = User.objects.create_user(email="n2@bordo.dev", password="x" * 10,
                                            name="서재민")
        team = Team.objects.create(name="팀", created_by=cls.me)
        TeamMember.objects.create(team=team, user=cls.me, team_role=TeamRole.OWNER)
        TeamMember.objects.create(team=team, user=cls.mate, team_role=TeamRole.MEMBER)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def test_flow_node_uses_the_chosen_name(self):
        """
        플로우의 대리인 노드입니다. 정해 두고도 기본 호칭이 뜨면 사용자는
        이름이 저장되지 않은 줄 압니다.
        """
        self.assertEqual(flow.agent_node(self.me)["name"], "유수인의 Bordo")
        AgentSettings.objects.update_or_create(user=self.me,
                                               defaults={"agent_name": "제로"})
        self.assertEqual(flow.agent_node(self.me)["name"], "제로")

    def test_my_agent_room_is_named_after_the_agent(self):
        """
        `AI 대리인` 은 화면 어디에도 없는 낱말입니다. 저장된 제목을 그대로 쓰면
        목록에만 그 낱말이 남습니다.
        """
        sidebar = self.client.get("/api/v1/chat/sidebar").data
        self.assertEqual(sidebar["my_agent_room"]["title"], "유수인의 Bordo")

    def test_renaming_the_agent_renames_the_room(self):
        """
        방 제목은 저장된 값이 아니라 조회 시점에 맞춥니다. 이름을 바꿨는데
        방만 옛 이름으로 남으면 저장이 안 된 줄 압니다.
        """
        AgentSettings.objects.update_or_create(user=self.me,
                                               defaults={"agent_name": "제로"})
        sidebar = self.client.get("/api/v1/chat/sidebar").data
        self.assertEqual(sidebar["my_agent_room"]["title"], "제로")

    def test_peer_agent_room_is_named_after_its_owner(self):
        AgentSettings.objects.create(user=self.mate)
        r = self.client.post("/api/v1/chat/rooms",
                             {"type": "PEER_AGENT", "member_ids": [str(self.mate.id)]},
                             format="json")
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(r.data["title"], "서재민의 Bordo")
