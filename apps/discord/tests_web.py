"""
Discord 연동의 웹 쪽 — 연결 코드 입력 · 서버 연결 해제 · 상태 진단.

막아야 하는 것: 만료·재사용 코드가 통하는 것, 남의 계정을 가로채는 것,
일반 멤버가 팀 서버를 붙이거나 떼는 것.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import OutboxEvent
from apps.discord.models import GuildLink, LinkCode
from apps.discord.web_views import BOT_PRESENCE_KEY
from apps.orgs.models import Team, TeamMember

from django.conf import settings

TOKEN = "test-only-token"
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email="o@bordo.dev", password="x" * 10, name="재민")
        cls.member = User.objects.create_user(email="m@bordo.dev", password="x" * 10, name="수연")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.owner)
        TeamMember.objects.create(team=cls.team, user=cls.owner, team_role="OWNER")
        TeamMember.objects.create(team=cls.team, user=cls.member, team_role="MEMBER")

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def code(self, discord_user_id="dc-1", *, minutes=10, used=False, guild=""):
        return LinkCode.objects.create(
            code="ABC123", discord_user_id=discord_user_id, guild_id=guild,
            expires_at=timezone.now() + timedelta(minutes=minutes),
            used_at=timezone.now() if used else None)


class AccountLinkTest(Base):

    def test_code_links_account_and_marks_used(self):
        row = self.code()
        res = self.api.post("/api/v1/me/discord/link", {"connect_code": "abc123"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()["linked"])
        self.owner.refresh_from_db(); row.refresh_from_db()
        self.assertEqual(self.owner.discord_user_id, "dc-1")
        self.assertIsNotNone(row.used_at)
        self.assertEqual(row.user, self.owner)
        me = self.api.get("/api/v1/users/me").json()
        self.assertTrue(me["discord_linked"])

    def test_invalid_expired_used(self):
        res = self.api.post("/api/v1/me/discord/link", {"connect_code": "NOPE"}, format="json")
        self.assertEqual(res.json()["error"]["code"], "DISCORD_CODE_INVALID")

        self.code(minutes=-1)
        res = self.api.post("/api/v1/me/discord/link", {"connect_code": "ABC123"}, format="json")
        self.assertEqual(res.status_code, 410)
        self.assertEqual(res.json()["error"]["code"], "DISCORD_CODE_EXPIRED")

        LinkCode.objects.all().delete()
        self.code(used=True)
        res = self.api.post("/api/v1/me/discord/link", {"connect_code": "ABC123"}, format="json")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "DISCORD_CODE_ALREADY_USED")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.discord_user_id, "")

    def test_relink_moves_discord_id_from_previous_account(self):
        """한 Discord 계정이 두 Bordo 계정에 이어지면 봇의 발언이 누구 것인지 알 수 없습니다."""
        self.member.discord_user_id = "dc-1"; self.member.save()
        self.code("dc-1")
        self.api.post("/api/v1/me/discord/link", {"connect_code": "ABC123"}, format="json")
        self.member.refresh_from_db(); self.owner.refresh_from_db()
        self.assertEqual(self.owner.discord_user_id, "dc-1")
        self.assertEqual(self.member.discord_user_id, "")

    def test_unlink(self):
        self.owner.discord_user_id = "dc-1"; self.owner.save()
        res = self.api.delete("/api/v1/me/discord/link")
        self.assertFalse(res.json()["linked"])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.discord_user_id, "")

    def test_requires_login(self):
        self.assertEqual(APIClient().post("/api/v1/me/discord/link", {}).status_code, 401)


class TeamLinkTest(Base):

    def url(self, tail):
        return f"/api/v1/teams/{self.team.id}/discord/{tail}"

    def test_owner_links_guild_with_code(self):
        self.code(guild="g-1")
        res = self.api.post(self.url("link"), {"connect_code": "ABC123", "guild_id": "g-1"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body["linked"])
        self.assertEqual(body["guild_id"], "g-1")
        self.assertEqual(GuildLink.objects.get(guild_id="g-1").team, self.team)

    def test_member_can_link_account_but_not_guild(self):
        self.api.force_authenticate(self.member)
        self.code()
        res = self.api.post(self.url("link"), {"connect_code": "ABC123", "guild_id": "g-1"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.discord_user_id, "dc-1")     # 계정 연결은 됨
        self.assertFalse(GuildLink.objects.filter(guild_id="g-1").exists())

    def test_non_member_is_403(self):
        stranger = User.objects.create_user(email="s@bordo.dev", password="x" * 10, name="남")
        self.api.force_authenticate(stranger)
        res = self.api.post(self.url("link"), {"connect_code": "x"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_unlink_drops_pending_outbox(self):
        GuildLink.objects.create(guild_id="g-1", team=self.team)
        OutboxEvent.objects.create(team=self.team, idempotency_key="k1",
                                   kind=OutboxEvent.Kind.MESSAGE, payload={})
        sent = OutboxEvent.objects.create(team=self.team, idempotency_key="k2",
                                          kind=OutboxEvent.Kind.MESSAGE, payload={},
                                          status=OutboxEvent.Status.SENT)
        res = self.api.delete(self.url("link"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["dropped_outbox_count"], 1)
        self.assertFalse(GuildLink.objects.filter(team=self.team).exists())
        self.assertEqual(OutboxEvent.objects.get(idempotency_key="k1").status, "DEAD")
        sent.refresh_from_db(); self.assertEqual(sent.status, "SENT")

    def test_unlink_requires_admin_and_existing_link(self):
        self.api.force_authenticate(self.member)
        self.assertEqual(self.api.delete(self.url("link")).status_code, 403)
        self.api.force_authenticate(self.owner)
        res = self.api.delete(self.url("link"))
        self.assertEqual(res.json()["error"]["code"], "DISCORD_NOT_LINKED")


@override_settings(BORDO=_SETTINGS)
class StatusTest(Base):

    def url(self):
        return f"/api/v1/teams/{self.team.id}/discord/status"

    def test_warnings_when_nothing_is_set_up(self):
        body = self.api.get(self.url()).json()
        self.assertFalse(body["connected"])
        self.assertEqual(body["bot_status"], "UNKNOWN")
        codes = {w["code"] for w in body["warnings"]}
        self.assertEqual(codes, {"GUILD_NOT_LINKED", "BOT_NOT_SEEN", "NO_LINKED_MEMBERS"})
        self.assertEqual(body["members"]["total"], 2)

    def test_bot_heartbeat_via_internal_presence(self):
        """봇 on_ready 가 보내는 {status, at} 은 400 이 아니라 생존 신호입니다."""
        res = self.client.post("/internal/v1/discord/presence",
                               {"status": "online", "at": "2026-08-18T10:00:00+09:00"},
                               content_type="application/json", HTTP_X_SERVICE_TOKEN=TOKEN)
        self.assertEqual(res.status_code, 202)
        self.assertEqual(cache.get(BOT_PRESENCE_KEY)["status"], "online")

        GuildLink.objects.create(guild_id="g-1", team=self.team)
        self.owner.discord_user_id = "dc-1"; self.owner.save()
        body = self.api.get(self.url()).json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["bot_status"], "READY")
        self.assertEqual(body["gateway"]["at"] if "at" in body["gateway"] else body["gateway"]["last_seen_at"],
                         "2026-08-18T10:00:00+09:00")
        self.assertEqual(body["members"]["linked"], 1)
        self.assertEqual(body["members"]["unlinked_names"], ["수연"])
        self.assertEqual(body["warnings"], [])
