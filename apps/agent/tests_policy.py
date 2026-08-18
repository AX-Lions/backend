"""
POLICY 판정 테스트.

스위치 하나를 끄면 그 의도만 막히고 나머지는 그대로여야 합니다.
한 스위치가 다른 의도까지 막으면 사용자는 왜 대리인이 침묵하는지 알 수 없습니다.
"""
from django.test import SimpleTestCase

from apps.agent.services import policy
from apps.agent.services.policy import Intent, Reason

ALL_OFF = {
    "mention_feasibility": False,
    "allow_schedule_change": False,
    "allow_midmeeting_question": False,
    "disclose_work_plan_thought": False,
}
ALL_ON = {k: True for k in ALL_OFF}


class CheckTest(SimpleTestCase):

    # ── 정상 경로 ──────────────────────────────────────────
    def test_all_on_allows_everything(self):
        for intent in Intent.ALL:
            self.assertTrue(policy.check(intent, ALL_ON), intent)

    # ── 스위치별로 그 의도만 막히는가 ──────────────────────
    def test_each_switch_blocks_only_its_intent(self):
        cases = [
            ("mention_feasibility", Intent.FEASIBILITY, Reason.FEASIBILITY),
            ("allow_schedule_change", Intent.SCHEDULE, Reason.SCHEDULE),
            ("disclose_work_plan_thought", Intent.STATUS, Reason.DISCLOSURE),
            ("allow_midmeeting_question", Intent.CLARIFY, Reason.CLARIFY),
        ]
        for key, blocked_intent, reason in cases:
            snap = {**ALL_ON, key: False}
            d = policy.check(blocked_intent, snap)
            self.assertFalse(d.allowed, f"{key} 를 껐는데 {blocked_intent} 가 통과")
            self.assertEqual(d.reason, reason)

            for other in Intent.ALL:
                if other == blocked_intent:
                    continue
                self.assertTrue(policy.check(other, snap),
                                f"{key} 를 껐는데 {other} 까지 막힘")

    # ── 거부 메시지 ────────────────────────────────────────
    def test_denial_message_is_actionable(self):
        """'권한 없음' 이 아니라 무엇을 켜면 되는지 알려줘야 합니다."""
        d = policy.check(Intent.FEASIBILITY, ALL_OFF)
        self.assertIn("설정", d.message)
        self.assertTrue(d.message.endswith("."))

    # ── 기본값 ─────────────────────────────────────────────
    def test_missing_snapshot_uses_defaults(self):
        """설정 화면에 한 번도 안 들어간 사용자도 대리인이 돌아야 합니다."""
        self.assertTrue(policy.check(Intent.STATUS, None))
        self.assertTrue(policy.check(Intent.FEASIBILITY, {}))

    def test_clarify_is_off_by_default(self):
        """회의 중 되묻기는 모델 기본값이 False 입니다."""
        self.assertFalse(policy.check(Intent.CLARIFY, None))

    def test_partial_snapshot_fills_the_rest(self):
        d = policy.check(Intent.FEASIBILITY, {"disclose_work_plan_thought": False})
        self.assertTrue(d.allowed)

    # ── 미분류 ─────────────────────────────────────────────
    def test_unknown_intent_passes_to_judge(self):
        """POLICY 가 모르는 의도는 통과시키고 근거 충분성에 맡깁니다."""
        self.assertTrue(policy.check("SOMETHING_NEW", ALL_OFF))
        self.assertTrue(policy.check(Intent.OTHER, ALL_OFF))

    # ── 일정은 허용돼도 제약이 붙는다 ──────────────────────
    def test_schedule_is_propose_only(self):
        """1원칙 — 대리인은 후보만 만들고 확정은 사람이 합니다."""
        d = policy.check(Intent.SCHEDULE, ALL_ON)
        self.assertTrue(d.allowed)
        self.assertIn("propose_only", d.constraints)

    def test_non_schedule_has_no_constraint(self):
        self.assertEqual(policy.check(Intent.STATUS, ALL_ON).constraints, [])


class DiscloseTest(SimpleTestCase):

    def test_private_is_blocked_regardless_of_settings(self):
        """본인이 비공개로 표시한 것은 어떤 스위치로도 열리지 않습니다."""
        item = {"source_type": "document", "visibility": "private"}
        self.assertFalse(policy.can_disclose(item, ALL_ON))

    def test_state_records_follow_the_switch(self):
        for kind in ("work", "plan", "thought"):
            item = {"source_type": kind, "visibility": "team"}
            self.assertTrue(policy.can_disclose(item, ALL_ON), kind)
            self.assertFalse(policy.can_disclose(item, ALL_OFF), kind)

    def test_meeting_record_is_not_gated_by_disclosure(self):
        """회의록은 이미 참석자가 함께 들은 내용이라 공개 스위치 대상이 아닙니다."""
        item = {"source_type": "meeting", "visibility": "team"}
        self.assertTrue(policy.can_disclose(item, ALL_OFF))


class MeetingScopedSourcesTest(SimpleTestCase):
    """
    불참 팝업에서 고른 자료 범위.

    개인 설정이 평소 기준이라면 이것은 **이 회의에서만** 쓰는 범위입니다.
    저장만 하고 판정에 안 쓰면, 사용자는 껐다고 믿는 기록이 그대로 회의에
    나갑니다 — 없는 것보다 나쁩니다.
    """

    def _snap(self, sources):
        return {**ALL_ON, "delegate_sources": sources}

    def test_only_the_picked_kinds_go_out(self):
        snap = self._snap(["work"])
        self.assertTrue(policy.can_disclose({"source_type": "work"}, snap))
        self.assertFalse(policy.can_disclose({"source_type": "thought"}, snap))
        self.assertFalse(policy.can_disclose({"source_type": "document"}, snap))

    def test_never_picked_means_no_limit(self):
        """
        `None` 은 고른 적 없음입니다. `[]` 과 같게 다루면 불참 등록을 안 한
        회의에서 대리인이 아무것도 못 씁니다.
        """
        self.assertTrue(policy.can_disclose({"source_type": "thought"}, ALL_ON))

    def test_empty_means_nothing(self):
        """"대리인은 보내되 내 기록은 쓰지 마라" 는 성립하는 선택입니다."""
        snap = self._snap([])
        self.assertFalse(policy.can_disclose({"source_type": "work"}, snap))
        # 회의에서 오간 말은 그 자리에 있던 사람이 이미 다 들었습니다.
        self.assertTrue(policy.can_disclose({"source_type": "meeting"}, snap))

    def test_both_gates_must_pass(self):
        """
        회의에서 켠다고 개인 설정을 뚫지 못합니다. 뚫리면 개인 설정 화면의
        스위치가 뜻을 잃습니다.
        """
        snap = {**ALL_OFF, "delegate_sources": ["work"]}
        self.assertFalse(policy.can_disclose({"source_type": "work"}, snap))

    def test_private_still_wins(self):
        snap = self._snap(["document"])
        self.assertFalse(policy.can_disclose(
            {"source_type": "document", "visibility": "private"}, snap))
