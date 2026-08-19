"""
팀 만들기 · 초대 코드 — 화면이 보내는 값을 서버가 버리지 않는지.

둘 다 400 도 안 나고 조용히 다른 값이 되던 자리입니다. 조용한 어긋남은
"저장이 안 됐다" 가 아니라 "저장된 줄 안다" 라 눈으로 못 찾습니다.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orgs.models import InviteCode, Team, TeamMember, TeamRole


class CreateTeamTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def post(self, **kw):
        body = {"name": "AX Lions"}
        body.update(kw)
        return self.client.post("/api/v1/teams", body, format="json")

    def test_timezone_is_kept(self):
        r = self.post(timezone="America/New_York")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["timezone"], "America/New_York")
        self.assertEqual(Team.objects.get().timezone, "America/New_York")

    def test_unknown_timezone_is_400(self):
        """조용히 기본값으로 바꾸면 고른 것과 다른 값이 들어갑니다."""
        r = self.post(timezone="Asia/없는도시")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Team.objects.exists())

    def test_timezone_is_optional(self):
        r = self.post()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Team.objects.get().timezone, "")


class InviteCodeTtlTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="o@bordo.dev", password="x" * 10,
                                           name="최비성")
        self.team = Team.objects.create(name="AX Lions", created_by=self.me)
        TeamMember.objects.create(team=self.team, user=self.me,
                                  team_role=TeamRole.OWNER)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def post(self, **kw):
        return self.client.post(f"/api/v1/teams/{self.team.id}/invite-codes",
                                kw, format="json")

    def test_expires_in_hours_is_honoured(self):
        """72시간으로 만든 코드가 조용히 7일짜리가 됐습니다."""
        before = timezone.now()
        r = self.post(expires_in_hours=72, max_uses=10, default_role="MEMBER")
        self.assertEqual(r.status_code, 201)
        gap = InviteCode.objects.get().expires_at - before
        self.assertLess(abs(gap - timezone.timedelta(hours=72)),
                        timezone.timedelta(minutes=1))

    def test_valid_days_still_works(self):
        before = timezone.now()
        self.post(valid_days=3)
        gap = InviteCode.objects.get().expires_at - before
        self.assertLess(abs(gap - timezone.timedelta(days=3)),
                        timezone.timedelta(minutes=1))

    def test_default_is_seven_days(self):
        before = timezone.now()
        self.post()
        gap = InviteCode.objects.get().expires_at - before
        self.assertLess(abs(gap - timezone.timedelta(days=7)),
                        timezone.timedelta(minutes=1))

    def test_zero_hours_is_400(self):
        r = self.post(expires_in_hours=0)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(InviteCode.objects.exists())
