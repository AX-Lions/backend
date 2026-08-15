"""
Outbox 발송 큐 테스트.

여기서 막아야 하는 것은 **중복 게시**와 **조용한 실종**입니다.
같은 발언이 두 번 나가면 회의가 어지러워지고, 실패가 아무도 모르게 사라지면
사용자는 대리인이 말한 줄 알고 있습니다.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import OutboxEvent
from apps.orgs.models import Team


class OutboxTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                            name="테스터")
        cls.team = Team.objects.create(name="팀", created_by=cls.user)
        cls.other = Team.objects.create(name="다른 팀", created_by=cls.user)

    def _event(self, team=None, key="k-1", **kw):
        return OutboxEvent.objects.create(
            team=team or self.team, idempotency_key=key,
            kind=OutboxEvent.Kind.MESSAGE, payload={"body": "안녕하세요"}, **kw)

    # ── 멱등 ───────────────────────────────────────────────
    def test_same_key_in_same_team_is_rejected(self):
        """재시도로 같은 발언이 두 번 큐에 들어가도 한 번만 나가야 합니다."""
        self._event()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._event()

    def test_same_key_in_another_team_is_fine(self):
        """멱등은 팀 안에서만입니다. 팀이 다르면 무관한 발언입니다."""
        self._event()
        self._event(team=self.other)
        self.assertEqual(OutboxEvent.objects.count(), 2)

    # ── 기본 상태 ──────────────────────────────────────────
    def test_starts_pending_and_available_now(self):
        e = self._event()
        self.assertEqual(e.status, OutboxEvent.Status.PENDING)
        self.assertLessEqual(e.available_at, timezone.now())

    # ── 발송 성공 ──────────────────────────────────────────
    def test_mark_sent(self):
        e = self._event()
        e.mark_sent()
        e.refresh_from_db()
        self.assertEqual(e.status, OutboxEvent.Status.SENT)
        self.assertIsNotNone(e.sent_at)

    def test_mark_sent_clears_previous_error(self):
        """한 번 실패했다가 성공하면 옛 오류가 화면에 남으면 안 됩니다."""
        e = self._event()
        e.mark_failed("일시 오류")
        e.mark_sent()
        e.refresh_from_db()
        self.assertEqual(e.last_error, "")

    # ── 실패와 재시도 ──────────────────────────────────────
    def test_failure_pushes_availability_back(self):
        """같은 간격으로 재시도하면 Discord 가 불안정할 때 몰려서 더 나빠집니다."""
        e = self._event()
        before = timezone.now()
        e.mark_failed("500")
        e.refresh_from_db()
        self.assertEqual(e.status, OutboxEvent.Status.PENDING)
        self.assertEqual(e.attempts, 1)
        self.assertGreater(e.available_at, before)

    def test_backoff_grows(self):
        e = self._event()
        e.mark_failed("1")
        first = e.available_at
        e.mark_failed("2")
        self.assertGreater(e.available_at - first, timezone.timedelta(seconds=0))

    def test_dead_after_max_attempts(self):
        """무한 재시도는 같은 오류를 반복하고, 그냥 버리면 아무도 모릅니다."""
        e = self._event(max_attempts=2)
        e.mark_failed("1")
        self.assertEqual(e.status, OutboxEvent.Status.PENDING)
        e.mark_failed("2")
        e.refresh_from_db()
        self.assertEqual(e.status, OutboxEvent.Status.DEAD)

    def test_error_is_truncated(self):
        """스택트레이스가 통째로 들어오면 목록 화면이 못 쓰게 됩니다."""
        e = self._event()
        e.mark_failed("x" * 5000)
        e.refresh_from_db()
        self.assertLessEqual(len(e.last_error), 2000)

    # ── 봇의 폴링 쿼리 ─────────────────────────────────────
    def test_polling_query_skips_future_and_done(self):
        now = timezone.now()
        ready = self._event(key="ready")
        self._event(key="later", available_at=now + timezone.timedelta(minutes=5))
        sent = self._event(key="sent")
        sent.mark_sent()

        rows = OutboxEvent.objects.filter(
            status=OutboxEvent.Status.PENDING, available_at__lte=now
        ).order_by("available_at")

        self.assertEqual([r.id for r in rows], [ready.id])
