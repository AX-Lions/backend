"""
예상 논쟁점 생성.

여기서 막아야 하는 것 세 가지 —
1. 모델이 인용문을 고쳐 쓰는 것 (화면의 따옴표가 실제 발언과 달라짐)
2. 재예측이 사람이 적어 둔 입장을 지우는 것
3. 모델이 없을 때 화면이 통째로 비는 것
"""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.services import contention
from apps.agent.services.llm import LLMResponse
from apps.meetings.models import (Agenda, DebatePoint, DebateStance, Meeting,
                                  MeetingStatus, Utterance)
from apps.orgs.models import Project, ProjectMember, Team, TeamMember
from apps.states.models import ActivityEvent, WorkItem


class Fake:
    """`chat()` 하나만 흉내 냅니다."""

    def __init__(self, text="", error=""):
        self.text, self.error = text, error
        self.seen = None

    def chat(self, messages, tools=None, system=""):
        self.seen = {"messages": messages, "system": system}
        return LLMResponse(text=self.text, error=self.error)


FENCE = "```"
ANSWER = FENCE + """json
{"points": [
  {"title": "개발 범위를 축소할 것인가, 기존 범위를 유지할 것인가?",
   "options": [{"title": "핵심 기능만 구현", "description": "남은 기간을 고려해 축소"},
               {"title": "기존 기획 범위 유지", "description": "계획한 기능을 최대한 구현"}],
   "rationale": "이전 회의에서 범위를 축소하자는 의견이 있었어요.",
   "evidence": [1]}
]}
""" + FENCE

TITLE = "개발 범위를 축소할 것인가, 기존 범위를 유지할 것인가?"


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="최비성", timezone="Asia/Seoul")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        cls.project = Project.objects.create(team=cls.team, team_name=cls.team.name,
                                             name="해커톤", created_by=cls.me)
        ProjectMember.objects.create(project=cls.project, user=cls.me)

    def meeting(self, title="개발 방향 논의", status=MeetingStatus.SCHEDULED, ago_days=0):
        return Meeting.objects.create(
            project=self.project, project_name=self.project.name, title=title,
            status=status, created_by=self.me,
            scheduled_at=timezone.now() - timezone.timedelta(days=ago_days))

    def past_utterance(self, body="남은 기간을 생각하면 핵심 기능부터 구현해야 할 것 같아요."):
        past = self.meeting("기능 구현 범위 논의", MeetingStatus.ENDED, ago_days=3)
        return Utterance.objects.create(
            meeting=past, participant=self.me, participant_name="최비성", body=body,
            spoken_at=timezone.now() - timezone.timedelta(days=3))


class FactsTest(Base):

    def test_previous_meeting_utterances_become_facts(self):
        self.past_utterance()
        facts = contention.gather_facts(self.meeting())
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kind"], "meeting")
        self.assertEqual(facts[0]["who"], "최비성")
        self.assertIn("기능 구현 범위 논의", facts[0]["title"])
        self.assertEqual(facts[0]["link"]["label"], "회의에서 보기")

    def test_this_meeting_is_not_a_fact(self):
        """아직 열리지도 않은 회의의 발언을 근거로 쓸 수는 없습니다."""
        m = self.meeting()
        Utterance.objects.create(meeting=m, participant=self.me, participant_name="최비성",
                                 body="이번 회의에서 한 말입니다 길게 적습니다",
                                 spoken_at=timezone.now())
        self.assertEqual(contention.gather_facts(m), [])

    def test_short_chatter_is_dropped(self):
        self.past_utterance(body="넵")
        self.assertEqual(contention.gather_facts(self.meeting()), [])

    def test_work_change_becomes_a_sentence(self):
        w = WorkItem.objects.create(project=self.project, owner=self.me, title="개발")
        ActivityEvent.objects.create(
            project=self.project, actor=self.me, kind="work.updated", target_id=w.id,
            detail={"expected_end_at": {"from": "8월 17일", "to": "8월 18일"}})
        facts = contention.gather_facts(self.meeting())
        self.assertEqual(facts[0]["kind"], "work")
        self.assertEqual(facts[0]["who"], "최비성의 작업")
        self.assertIn("완료 예정일", facts[0]["body"])
        self.assertIn("8월 17일 → 8월 18일", facts[0]["body"])
        self.assertEqual(facts[0]["link"]["label"], "작업에서 보기")

    def test_unknown_field_change_is_ignored(self):
        w = WorkItem.objects.create(project=self.project, owner=self.me, title="개발")
        ActivityEvent.objects.create(project=self.project, actor=self.me,
                                     kind="work.updated", target_id=w.id,
                                     detail={"visibility": {"from": "team", "to": "private"}})
        self.assertEqual(contention.gather_facts(self.meeting()), [])


class BuildTest(Base):

    def test_llm_points_are_saved_with_keys_and_evidence(self):
        self.past_utterance()
        m = self.meeting()
        self.assertEqual(contention.build_for(m, client=Fake(ANSWER)), 1)

        p = DebatePoint.objects.get(meeting=m)
        self.assertEqual(p.order, 1)
        self.assertTrue(p.created_by_agent)
        self.assertEqual([o["key"] for o in p.options], ["A", "B"])
        self.assertEqual(p.options[0]["title"], "핵심 기능만 구현")
        self.assertEqual(len(p.evidence), 1)

    def test_evidence_quote_comes_from_the_record_not_the_model(self):
        """모델이 인용문을 고쳐 쓰면 화면의 따옴표가 실제 발언과 달라집니다."""
        self.past_utterance()
        m = self.meeting()
        contention.build_for(m, client=Fake(ANSWER))
        card = DebatePoint.objects.get(meeting=m).evidence[0]
        self.assertEqual(card["body"],
                         "남은 기간을 생각하면 핵심 기능부터 구현해야 할 것 같아요.")
        self.assertEqual(card["who"], "최비성")

    def test_bad_evidence_index_is_dropped_not_crashed(self):
        self.past_utterance()
        m = self.meeting()
        bad = ANSWER.replace('"evidence": [1]', '"evidence": [9, "x", 1]')
        contention.build_for(m, client=Fake(bad))
        self.assertEqual(len(DebatePoint.objects.get(meeting=m).evidence), 1)

    def test_single_option_is_not_a_debate(self):
        self.past_utterance()
        m = self.meeting()
        one = '{"points": [{"title": "제목", "options": [{"title": "하나"}], "evidence": []}]}'
        contention.build_for(m, client=Fake(one))
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 0)

    def test_broken_json_falls_back_to_agendas(self):
        self.past_utterance()
        m = self.meeting()
        Agenda.objects.create(meeting=m, title="QA 일정", sort_order=1)
        contention.build_for(m, client=Fake("이건 JSON 이 아닙니다"))
        p = DebatePoint.objects.get(meeting=m)
        self.assertIn("QA 일정", p.title)
        self.assertFalse(p.created_by_agent)

    def test_no_api_key_still_fills_the_screen(self):
        m = self.meeting()
        Agenda.objects.create(meeting=m, title="발표 준비", sort_order=1)
        self.assertEqual(
            contention.build_for(m, client=Fake(error="OPENAI_API_KEY 가 없습니다")), 1)

    def test_nothing_at_all_is_not_an_error(self):
        self.assertEqual(contention.build_for(self.meeting(), client=Fake(error="x")), 0)


class RebuildTest(Base):

    def _build(self, m, answer=ANSWER):
        return contention.build_for(m, client=Fake(answer))

    def test_same_title_keeps_the_stance(self):
        """재예측이 적어 둔 입장을 지우면 준비 화면을 두 번 채우게 됩니다."""
        self.past_utterance()
        m = self.meeting()
        self._build(m)
        p = DebatePoint.objects.get(meeting=m)
        DebateStance.objects.create(point=p, user=self.me, body="축소가 맞아요")

        self._build(m)
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 1)
        self.assertTrue(DebateStance.objects.filter(point=p).exists())

    def test_answered_point_survives_even_if_prediction_changes(self):
        self.past_utterance()
        m = self.meeting()
        self._build(m)
        old = DebatePoint.objects.get(meeting=m)
        DebateStance.objects.create(point=old, user=self.me, body="내 입장")

        self._build(m, ANSWER.replace(TITLE, "QA 일정을 연기할 것인가, 유지할 것인가?"))
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 2)
        self.assertTrue(DebateStance.objects.filter(point=old).exists())

    def test_unanswered_point_is_replaced(self):
        self.past_utterance()
        m = self.meeting()
        self._build(m)
        self._build(m, ANSWER.replace(TITLE, "QA 일정을 연기할 것인가, 유지할 것인가?"))
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 1)

    def test_source_key_follows_the_title_not_the_order(self):
        """순서로 열쇠를 만들면 2번이 다른 쟁점이 되면서 1번 입장이 엉뚱한 곳에 붙습니다."""
        a = contention._source_key("개발 범위를  축소할 것인가?")
        b = contention._source_key("개발 범위를 축소할 것인가?")
        self.assertEqual(a, b)
        self.assertNotEqual(a, contention._source_key("QA 일정을 연기할 것인가?"))


class TaskTest(Base):

    def test_absence_fills_the_prep_screen(self):
        """화면에서 따로 부르게 하면 열 때마다 모델이 돌고 두 번째 사람은 다른 예측을 봅니다."""
        from unittest.mock import patch

        from apps.agent.tasks import build_debate_points

        self.past_utterance()
        m = self.meeting()
        Agenda.objects.create(meeting=m, title="QA 일정", sort_order=1)

        with patch("apps.agent.services.llm.client", Fake(ANSWER)):
            build_debate_points(str(m.id))
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 1)

    def test_second_person_does_not_re_predict(self):
        from unittest.mock import patch

        from apps.agent.tasks import build_debate_points

        self.past_utterance()
        m = self.meeting()
        with patch("apps.agent.services.llm.client", Fake(ANSWER)):
            build_debate_points(str(m.id))
            spy = Fake(ANSWER)
            with patch("apps.agent.services.llm.client", spy):
                build_debate_points(str(m.id))
            self.assertIsNone(spy.seen, "이미 만들어져 있으면 모델을 다시 부르지 않습니다")

    def test_failure_does_not_bubble_up(self):
        from unittest.mock import patch

        from apps.agent.tasks import build_debate_points

        m = self.meeting()
        with patch("apps.agent.services.contention.build_for",
                   side_effect=RuntimeError("boom")):
            build_debate_points(str(m.id))     # 불참 등록은 이미 끝나 있습니다

    def test_unknown_meeting_is_ignored(self):
        import uuid

        from apps.agent.tasks import build_debate_points
        build_debate_points(str(uuid.uuid4()))


class EvidenceSafetyTest(Base):
    """근거 카드에 나가면 안 되는 것."""

    def _event(self, work, kind="work.updated", detail=None):
        return ActivityEvent.objects.create(
            project=self.project, actor=self.me, kind=kind, target_id=work.id,
            detail=detail or {"progress": {"from": 40, "to": 80}})

    def test_private_work_is_not_evidence(self):
        """회의 참석자 전원이 보는 화면입니다. 대리인 발언 판정보다 앞입니다."""
        from apps.states.models import Visibility

        w = WorkItem.objects.create(project=self.project, owner=self.me,
                                    title="비공개 작업", visibility=Visibility.PRIVATE)
        self._event(w)
        self.assertEqual(contention.gather_facts(self.meeting()), [])

    def test_deleted_work_is_not_evidence(self):
        """지워진 것을 놓고 입장을 정하게 하면 화면에서 찾을 수가 없습니다."""
        w = WorkItem.objects.create(project=self.project, owner=self.me, title="지운 작업")
        self._event(w, kind="work.deleted", detail={"title": "지운 작업"})
        w.delete()
        self.assertEqual(contention.gather_facts(self.meeting()), [])

    def test_created_log_still_reads_as_created(self):
        w = WorkItem.objects.create(project=self.project, owner=self.me, title="새 작업")
        self._event(w, kind="work.created", detail={"title": "새 작업"})
        self.assertIn("새로 생겼어요", contention.gather_facts(self.meeting())[0]["body"])

    def test_evidence_index_zero_does_not_wrap_around(self):
        """0 을 그대로 빼면 음수 인덱스가 되어 맨 뒤 사실이 엉뚱하게 붙습니다."""
        self.past_utterance()
        m = self.meeting()
        contention.build_for(m, client=Fake(ANSWER.replace('"evidence": [1]',
                                                           '"evidence": [0, -1]')))
        self.assertEqual(DebatePoint.objects.get(meeting=m).evidence, [])

    def test_option_keys_start_at_a_even_if_one_is_dropped(self):
        self.past_utterance()
        m = self.meeting()
        holed = ANSWER.replace('{"title": "핵심 기능만 구현", "description": "남은 기간을 고려해 축소"}',
                               '{"description": "제목이 없어 걸러짐"}')
        contention.build_for(m, client=Fake(holed))
        # 선택지가 하나만 남아 쟁점이 아니게 됩니다
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 0)

    def test_option_keys_are_sequential(self):
        self.past_utterance()
        m = self.meeting()
        three = ANSWER.replace('{"title": "기존 기획 범위 유지", "description": "계획한 기능을 최대한 구현"}',
                               '{"title": "유지"}, {"title": "절충"}')
        contention.build_for(m, client=Fake(three))
        self.assertEqual([o["key"] for o in DebatePoint.objects.get(meeting=m).options],
                         ["A", "B", "C"])


class RebuildSafetyTest(Base):

    def test_failed_rebuild_keeps_what_was_showing(self):
        """모델이 한 번 실패했다고 화면을 열어 둔 사람의 목록이 빈 칸이 되면 안 됩니다."""
        self.past_utterance()
        m = self.meeting()
        contention.build_for(m, client=Fake(ANSWER))
        self.assertEqual(contention.build_for(m, client=Fake(error="503")), 0)
        self.assertEqual(DebatePoint.objects.filter(meeting=m).count(), 1)

    def test_surviving_answered_point_gets_a_new_number(self):
        """번호를 다시 안 매기면 `논쟁점 01` 이 둘이 되어 어느 것을 말하는지 알 수 없습니다."""
        self.past_utterance()
        m = self.meeting()
        contention.build_for(m, client=Fake(ANSWER))
        old = DebatePoint.objects.get(meeting=m)
        DebateStance.objects.create(point=old, user=self.me, body="내 입장")

        contention.build_for(m, client=Fake(
            ANSWER.replace(TITLE, "QA 일정을 연기할 것인가, 유지할 것인가?")))
        orders = sorted(DebatePoint.objects.filter(meeting=m).values_list("order", flat=True))
        self.assertEqual(orders, [1, 2])
