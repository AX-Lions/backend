"""
발언 분류 — 화면 필터 여섯 칸 중 어디로 들어가는가.

실제 시연 회의에서 나온 문장을 그대로 씁니다. 여기서 갈리면 플로우 화면이
**회의에서 없었던 일을 말합니다** — 답을 못 받은 질문이 `결론` 으로 서고,
개회 인사가 `일정` 으로 섭니다.
"""
from django.test import SimpleTestCase

from apps.agent.services.flow import classify_speech
from apps.meetings.models import FlowContentType


class ClassifySpeechTest(SimpleTestCase):

    def kind(self, body):
        got = classify_speech(body)
        return got[0] if got else None

    # ── 묻는 말은 무엇보다 먼저다

    def test_question_with_confirm_word_is_not_a_conclusion(self):
        """`확정` 이 들어 있어도 물음표로 끝나면 아직 안 정해진 것입니다."""
        self.assertEqual(
            self.kind("디자인이 밀리면 개발도 1주 미뤄야 할 것 같은데, "
                      "에밀리님 쪽에서 1주 연장으로 확정해도 될까요?"),
            FlowContentType.REQUEST)

    def test_question_without_question_mark(self):
        """물음표를 안 찍는 사람이 많습니다. 어미로도 봅니다."""
        self.assertEqual(self.kind("이번 주에 마감으로 정리하면 될까요"),
                         FlowContentType.REQUEST)

    def test_plain_question_stays_a_request(self):
        self.assertEqual(self.kind("민님 플로우 화면 연결 작업은 어디까지 됐나요?"),
                         FlowContentType.REQUEST)

    # ── `일정` 은 때가 같이 있어야 일정이다

    def test_opening_remark_is_not_a_schedule(self):
        """`남은 일정을 정리하겠습니다` 는 개회 인사지 일정 변경이 아닙니다."""
        self.assertEqual(
            self.kind("시작하겠습니다. 오늘은 API 명세를 훑고 남은 일정을 정리하겠습니다."),
            FlowContentType.OPINION)

    def test_real_schedule_still_lands_in_schedule(self):
        self.assertEqual(self.kind("시안 마감은 8월 18일로 잡겠습니다"),
                         FlowContentType.SCHEDULE)

    def test_deadline_word_alone_is_enough(self):
        self.assertEqual(self.kind("이 작업 데드라인을 뒤로 미루겠습니다"),
                         FlowContentType.SCHEDULE)

    # ── 나머지 규칙은 그대로

    def test_decision_is_a_conclusion(self):
        self.assertEqual(self.kind("그럼 범위를 축소하는 걸로 확정하겠습니다"),
                         FlowContentType.CONCLUSION)

    def test_change_is_a_change(self):
        self.assertEqual(self.kind("응답 구조를 배열에서 객체로 변경하겠습니다"),
                         FlowContentType.CHANGE)

    def test_short_chatter_is_dropped(self):
        self.assertIsNone(classify_speech("넵"))
        self.assertIsNone(classify_speech("ㅇㅋ"))
