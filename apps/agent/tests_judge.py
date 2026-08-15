"""
유보 판정 테스트.

여기가 서비스의 차별점이라 규칙 하나하나를 눌러 봅니다.
특히 **답할 수 있는 경우에 유보로 막히지 않는지**를 함께 봐야 합니다 —
과하게 유보하면 대리인이 쓸모없어집니다.
"""
from django.test import SimpleTestCase

from apps.agent.services import judge
from apps.agent.services.judge import Reason
from apps.agent.services.policy import Intent

ALL_ON = {
    "mention_feasibility": True,
    "allow_schedule_change": True,
    "allow_midmeeting_question": True,
    "disclose_work_plan_thought": True,
}


def ev(**kw):
    """기본은 '답할 수 있는' 근거. 테스트마다 한 조건씩만 무너뜨립니다."""
    base = {
        "source_type": "work",
        "source_id": "s-1",
        "title_snapshot": "team_members 마이그레이션",
        "owner_is_principal": True,
        "match": judge.MATCH_DIRECT,
        "staleness_days": 1,
        "status": "IN_PROGRESS",
        "requires_discussion": False,
        "visibility": "team",
    }
    base.update(kw)
    return base


class AnswerTest(SimpleTestCase):
    """막지 말아야 할 것."""

    def test_direct_own_fresh_evidence_answers(self):
        v = judge.judge(Intent.STATUS, [ev()], ALL_ON)
        self.assertTrue(v.answer)
        self.assertEqual(v.reason, "")
        self.assertEqual(len(v.evidence), 1)

    def test_partial_alongside_direct_still_answers(self):
        v = judge.judge(Intent.STATUS,
                        [ev(), ev(source_id="s-2", match=judge.MATCH_PARTIAL)],
                        ALL_ON)
        self.assertTrue(v.answer)

    def test_low_confidence_thought_with_solid_work_answers(self):
        """확실한 작업 기록이 있으면 흐린 생각 하나 때문에 막히면 안 됩니다."""
        v = judge.judge(Intent.STATUS,
                        [ev(), ev(source_id="s-2", source_type="thought",
                                  confidence=0.2)],
                        ALL_ON)
        self.assertTrue(v.answer)

    def test_stale_but_not_in_progress_answers(self):
        """끝난 작업은 오래돼도 사실이 안 바뀝니다."""
        v = judge.judge(Intent.STATUS,
                        [ev(staleness_days=400, status="DONE")], ALL_ON)
        self.assertTrue(v.answer)

    def test_schedule_constraint_is_carried_through(self):
        v = judge.judge(Intent.SCHEDULE, [ev()], ALL_ON)
        self.assertTrue(v.answer)
        self.assertIn("propose_only", v.constraints)


class DeferTest(SimpleTestCase):
    """규칙별로 유보되는가."""

    def test_r1_no_evidence(self):
        v = judge.judge(Intent.STATUS, [], ALL_ON)
        self.assertFalse(v.answer)
        self.assertEqual(v.reason, Reason.NO_EVIDENCE)

    def test_r1_none_evidence(self):
        self.assertEqual(judge.judge(Intent.STATUS, None, ALL_ON).reason,
                         Reason.NO_EVIDENCE)

    def test_r2_only_someone_elses_record(self):
        """대리인은 본인을 대리합니다. 남의 기록으로 답하면 대리가 아닙니다."""
        v = judge.judge(Intent.STATUS, [ev(owner_is_principal=False)], ALL_ON)
        self.assertEqual(v.reason, Reason.NOT_MY_RECORD)

    def test_r3_inferred_only(self):
        v = judge.judge(Intent.STATUS, [ev(match=judge.MATCH_INFERRED)], ALL_ON)
        self.assertEqual(v.reason, Reason.INFERRED_ONLY)

    def test_r3_partial_only(self):
        v = judge.judge(Intent.STATUS, [ev(match=judge.MATCH_PARTIAL)], ALL_ON)
        self.assertEqual(v.reason, Reason.INFERRED_ONLY)

    def test_r4_requires_discussion(self):
        """본인이 직접 표시한 신호라 가장 강합니다."""
        v = judge.judge(Intent.STATUS, [ev(requires_discussion=True)], ALL_ON)
        self.assertEqual(v.reason, Reason.NEEDS_DISCUSSION)

    def test_r4_wins_over_later_rules(self):
        """사유는 하나만 보여줍니다. 먼저 걸린 규칙이 이겨야 합니다."""
        v = judge.judge(Intent.STATUS,
                        [ev(requires_discussion=True, staleness_days=999)], ALL_ON)
        self.assertEqual(v.reason, Reason.NEEDS_DISCUSSION)

    def test_r5_only_low_confidence_thoughts(self):
        v = judge.judge(Intent.STATUS,
                        [ev(source_type="thought", confidence=0.3)], ALL_ON)
        self.assertEqual(v.reason, Reason.LOW_CONFIDENCE)

    def test_r6_stale_in_progress(self):
        v = judge.judge(Intent.STATUS,
                        [ev(staleness_days=400, status="IN_PROGRESS")], ALL_ON)
        self.assertEqual(v.reason, Reason.STALE)

    def test_r7_conflicting(self):
        v = judge.judge(Intent.STATUS,
                        [ev(status="BLOCKED"),
                         ev(source_id="s-2", source_type="plan", status="DONE")],
                        ALL_ON)
        self.assertEqual(v.reason, Reason.CONFLICTING)


class PolicyGateTest(SimpleTestCase):

    def test_policy_denial_short_circuits(self):
        """POLICY 로 막히면 근거가 아무리 좋아도 답하지 않습니다."""
        v = judge.judge(Intent.STATUS, [ev()],
                        {**ALL_ON, "disclose_work_plan_thought": False})
        self.assertFalse(v.answer)
        self.assertEqual(v.reason, "POLICY_DISCLOSURE")

    def test_private_evidence_is_dropped_before_judging(self):
        """
        인용할 수 없는 근거를 남겨 두면 '근거가 있다'고 판단해 답변으로 가고,
        정작 못 쓰니 빈 답이 됩니다.
        """
        v = judge.judge(Intent.STATUS, [ev(visibility="private")], ALL_ON)
        self.assertEqual(v.reason, Reason.NO_EVIDENCE)


class MessageTest(SimpleTestCase):

    def test_every_reason_has_a_message(self):
        for name in dir(Reason):
            if name.startswith("_"):
                continue
            code = getattr(Reason, name)
            self.assertIn(code, judge.MESSAGES, f"{code} 에 문구가 없습니다")

    def test_defer_carries_message_and_evidence(self):
        """사용자는 사유와 함께 무엇을 보고 그렇게 판단했는지 봐야 합니다."""
        v = judge.judge(Intent.STATUS, [ev(requires_discussion=True)], ALL_ON)
        self.assertTrue(v.message)
        self.assertEqual(len(v.evidence), 1)

    def test_verdict_is_falsy_when_deferred(self):
        self.assertFalse(bool(judge.judge(Intent.STATUS, [], ALL_ON)))
        self.assertTrue(bool(judge.judge(Intent.STATUS, [ev()], ALL_ON)))
