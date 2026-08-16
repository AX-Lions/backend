"""
유보 기록 테스트.

유보가 화면에 남지 않으면 **대리인이 침묵한 사실 자체가 사라집니다.**
사용자는 회의에서 자기 이름이 불린 줄도 모릅니다.
"""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentRun, AgentSettings, PendingQuestion
from apps.agent.services import deferral, react
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.meetings.models import Meeting
from apps.orgs.models import Project, Team
from apps.states.models import ThoughtItem, WorkItem


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)

    def chat(self, messages, tools=None, system=""):
        return self._q.pop(0) if self._q else LLMResponse(text="끝")


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민")
        cls.asker = User.objects.create_user(email="q@bordo.dev", password="x" * 10,
                                             name="임수연")
        team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=team, team_name="팀", name="Bordo",
                                             created_by=cls.me)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        AgentSettings.objects.create(user=cls.me)

    def _run_obj(self, meeting=True):
        return AgentRun.objects.create(
            user=self.me, meeting=self.meeting if meeting else None,
            status=AgentRun.Status.COMPLETED)


class RecordTest(Base):

    def test_creates_question(self):
        q = deferral.record(run=self._run_obj(), question="DB 스키마 어디까지 됐어요?",
                            reason_message="관련 기록을 찾지 못했습니다.",
                            asker=self.asker)
        self.assertIsNotNone(q)
        self.assertEqual(q.target_user, self.me)
        self.assertEqual(q.asker_name, "임수연")

    def test_body_holds_question_and_reason(self):
        """사유만 있으면 무슨 질문이었는지 되짚을 수 없습니다."""
        q = deferral.record(run=self._run_obj(), question="스키마 언제 끝나요?",
                            reason_message="본인이 논의가 필요하다고 표시했습니다.",
                            evidence=[{"title_snapshot": "DB 스키마 구조"}],
                            asker=self.asker)
        self.assertIn("스키마 언제 끝나요?", q.body)
        self.assertIn("논의가 필요", q.body)
        self.assertIn("DB 스키마 구조", q.body)

    def test_title_is_the_question_itself(self):
        """요약을 LLM 에게 맡기면 질문이 다르게 적혀 본인이 헷갈립니다."""
        q = deferral.record(run=self._run_obj(), question="스키마 진행 상황",
                            reason_message="x")
        self.assertEqual(q.title, "스키마 진행 상황")

    def test_long_title_is_cut(self):
        q = deferral.record(run=self._run_obj(), question="가" * 300,
                            reason_message="x")
        self.assertLessEqual(len(q.title), 200)
        self.assertTrue(q.title.endswith("…"))

    def test_same_run_records_once(self):
        """재시도에서 목록이 불어나면 같은 질문을 여러 번 답해야 합니다."""
        run = self._run_obj()
        a = deferral.record(run=run, question="q", reason_message="r")
        b = deferral.record(run=run, question="q", reason_message="r")
        self.assertEqual(a.id, b.id)
        self.assertEqual(PendingQuestion.objects.count(), 1)

    def test_no_meeting_records_nothing(self):
        """본인과의 대화에서 나온 유보는 되물을 상대가 자기 자신입니다."""
        q = deferral.record(run=self._run_obj(meeting=False), question="q",
                            reason_message="r")
        self.assertIsNone(q)
        self.assertEqual(PendingQuestion.objects.count(), 0)


class ThroughLoopTest(Base):

    def _run(self, llm, question="DB 스키마 어디까지 됐어요?"):
        return react.run(principal=self.me, question=question, meeting=self.meeting,
                         actor_id=self.asker.id, asker=self.asker, client=llm)

    def test_defer_leaves_a_question(self):
        out = self._run(FakeLLM(LLMResponse(text="STATUS"),
                                LLMResponse(text="아마 곧 끝날 겁니다")))
        self.assertFalse(out.answered)
        self.assertIsNotNone(out.pending_question)
        self.assertEqual(PendingQuestion.objects.count(), 1)

    def test_answer_leaves_nothing(self):
        WorkItem.objects.create(project=self.project, owner=self.me,
                                title="team_members 마이그레이션",
                                summary="진행 중", status="IN_PROGRESS")
        out = self._run(FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="진행 중입니다."),
        ))
        self.assertTrue(out.answered)
        self.assertIsNone(out.pending_question)
        self.assertEqual(PendingQuestion.objects.count(), 0)

    def test_policy_block_also_leaves_a_question(self):
        """정책으로 막힌 것도 본인은 알아야 합니다."""
        AgentSettings.objects.filter(user=self.me).update(
            disclose_work_plan_thought=False)
        out = self._run(FakeLLM(LLMResponse(text="STATUS")))
        self.assertIsNotNone(out.pending_question)
        self.assertIn("설정", out.pending_question.body)

    def test_question_is_visible_to_the_principal(self):
        """이 목록이 곧 '내가 없는 동안 무슨 일이 있었지' 의 답입니다."""
        ThoughtItem.objects.create(project=self.project, owner=self.me,
                                   topic="스키마 구조", content="고민 중",
                                   requires_discussion=True)
        self._run(FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "스키마 구조"})]),
            LLMResponse(text="이렇게 하기로 했습니다"),
        ))
        mine = PendingQuestion.objects.filter(target_user=self.me, answered_at=None)
        self.assertEqual(mine.count(), 1)
        self.assertEqual(mine.first().asker, self.asker)


class AnswerRoomTest(Base):
    """
    답변 방 준비가 유보 기록을 막지 않아야 합니다.

    `record()` 는 `@transaction.atomic` 이라, 방을 만들다 터지면 그 예외가
    트랜잭션을 통째로 깨고 `PendingQuestion` 까지 사라집니다. 답변 창이 한 번 덜
    열리는 것과 "그 질문이 있었다" 가 통째로 사라지는 것은 비교가 안 됩니다.
    """

    def test_room_is_prepared_up_front(self):
        """
        누른 뒤에 만들면 왕복이 한 번 더 생기고, 만들다 실패하면 사용자는
        `답변하기가 안 눌린다` 만 보게 됩니다.
        """
        q = deferral.record(run=self._run_obj(), question="언제 끝나요?",
                            reason_message="본인 확인이 필요합니다.", asker=self.asker)
        self.assertIsNotNone(q.chat_room_id)

    def test_both_sides_are_in_the_room(self):
        """질문한 사람만 넣으면 정작 답할 사람의 목록에 방이 안 뜹니다."""
        from apps.chat.models import RoomMember

        q = deferral.record(run=self._run_obj(), question="언제 끝나요?",
                            reason_message="본인 확인이 필요합니다.", asker=self.asker)
        members = set(RoomMember.objects.filter(room_id=q.chat_room_id)
                      .values_list("user_id", flat=True))
        self.assertEqual(members, {self.me.id, self.asker.id})

    def test_same_pair_reuses_the_room(self):
        """유보할 때마다 방이 새로 생기면 대화가 조각납니다."""
        first = deferral.record(run=self._run_obj(), question="언제 끝나요?",
                                reason_message="확인이 필요합니다.", asker=self.asker)
        second = deferral.record(run=self._run_obj(), question="다른 질문",
                                 reason_message="확인이 필요합니다.", asker=self.asker)
        self.assertEqual(first.chat_room_id, second.chat_room_id)

    def test_room_collision_does_not_lose_the_question(self):
        """
        같은 두 사람 사이 유보 두 건이 동시에 처리되면 방 생성이 유니크 제약에
        걸립니다. **그때 유보가 사라지면 안 됩니다.**

        경합을 그대로 재현할 수 없어, 생성이 IntegrityError 를 내는 상황을 만들어
        결과만 봅니다 — 다른 워커가 이미 만들어 둔 것과 같은 상황입니다.
        """
        from unittest.mock import patch

        from django.db import IntegrityError

        from apps.chat.models import ChatRoom, RoomType
        from apps.chat.services import peer_agent_key

        # 다른 워커가 먼저 만들어 둔 방.
        key = peer_agent_key(self.asker.id, self.me.id)
        winner = ChatRoom.objects.create(type=RoomType.PEER_AGENT, dedupe_key=key,
                                         agent_owner=self.me, created_by=self.me)

        real = ChatRoom.all_objects.filter
        calls = {"n": 0}

        def blind_first_lookup(*a, **kw):
            # 첫 조회만 "없다" 로 속입니다 — 경합에서 진 쪽이 보는 화면입니다.
            calls["n"] += 1
            if calls["n"] == 1:
                return ChatRoom.all_objects.none()
            return real(*a, **kw)

        with patch("apps.chat.models.ChatRoom.all_objects.filter",
                   side_effect=blind_first_lookup), \
             patch("apps.chat.models.ChatRoom.objects.create",
                   side_effect=IntegrityError("uq_chat_room_dedupe")):
            q = deferral.record(run=self._run_obj(), question="언제 끝나요?",
                                reason_message="본인 확인이 필요합니다.",
                                asker=self.asker)

        self.assertIsNotNone(q, "충돌 때문에 유보가 통째로 사라졌습니다")
        self.assertEqual(PendingQuestion.objects.count(), 1)
        self.assertEqual(q.chat_room_id, winner.id, "먼저 만들어진 방을 되읽어야 합니다")

    def test_question_survives_even_if_the_room_cannot_be_made(self):
        """
        방을 끝내 못 잡아도 유보는 남습니다. 화면은 방 없이 뜨고 사용자는
        답변 창을 한 번 더 열면 됩니다.
        """
        from unittest.mock import patch

        # 방 만들기 안쪽에서 터뜨립니다. `_answer_room_id` 자체를 모킹하면
        # 정작 그 함수의 방어를 한 줄도 안 타서 테스트가 아무것도 안 봅니다.
        with patch("apps.chat.models.RoomMember.objects.get_or_create",
                   side_effect=RuntimeError("채팅 앱 장애")):
            q = deferral.record(run=self._run_obj(), question="언제 끝나요?",
                                reason_message="본인 확인이 필요합니다.",
                                asker=self.asker)
        self.assertIsNotNone(q, "채팅 쪽 장애로 유보가 사라졌습니다")
        self.assertIsNone(q.chat_room_id)
