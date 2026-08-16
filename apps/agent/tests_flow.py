"""
플로우 엣지 생성 테스트.

`FlowEdge` 는 **핵심 화면 두 개 중 하나의 유일한 데이터원**입니다. 지금까지 시드가
심은 하드코딩만 있어서, 새 회의를 열면 화면이 비었습니다.

여기서 보는 것은 두 가지입니다.

    1. 의미 있는 변화만 남는가 — 검색 호출까지 그리면 화살표에 묻혀 아무것도 안 보입니다
    2. 기록이 실패해도 대리인은 계속 도는가 — 화살표 하나 때문에 답변이 사라지면 안 됩니다
"""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.models import AgentSettings, OutboxEvent
from apps.agent.services import flow
from apps.agent.services.llm import LLMResponse, ToolCall
from apps.agent.tasks import run_agent_for_utterance
from apps.meetings.models import (Attendance, FlowContentType, FlowEdge, Meeting,
                                  MeetingParticipant, Surface, Utterance)
from apps.orgs.models import Project, Team
from apps.states.models import WorkItem

#: 서비스 토큰 테스트용. 실제 설정값을 쓰면 비어 있는 개발 환경에서 통과해 버립니다.
_TOKEN = "test-service-token-flow"


class FakeLLM:
    def __init__(self, *responses):
        self._q = list(responses)

    def chat(self, messages, tools=None, system=""):
        return self._q.pop(0) if self._q else LLMResponse(text="끝")


class Picker:
    def __init__(self, choice: str = "1"):
        self.choice = choice

    def chat(self, messages, tools=None, system=""):
        return LLMResponse(text=self.choice)


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.absent = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                              name="서재민")
        cls.speaker = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                               name="임수연")
        cls.present = User.objects.create_user(email="p@bordo.dev", password="x" * 10,
                                               name="최비성")
        cls.team = Team.objects.create(name="팀", created_by=cls.absent)
        cls.project = Project.objects.create(team=cls.team, team_name="팀", name="Bordo",
                                             created_by=cls.absent)
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="정기 회의",
            scheduled_at=timezone.now(), started_at=timezone.now(),
            created_by=cls.absent, discord_channel_id="ch-1")
        AgentSettings.objects.create(user=cls.absent)

        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.absent, user_name="서재민",
            attendance=Attendance.ABSENT, delegated=True,
            delegate_prompt="DB 질문 나오면 진행 상황 전달해줘")
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.speaker, user_name="임수연",
            attendance=Attendance.PRESENT)
        MeetingParticipant.objects.create(
            meeting=cls.meeting, user=cls.present, user_name="최비성",
            attendance=Attendance.PRESENT)

    def _utterance(self, body="DB 스키마 어디까지 됐어요?"):
        return Utterance.objects.create(
            meeting=self.meeting, participant=self.speaker,
            participant_name="임수연", body=body)

    def _run(self, llm, body="DB 스키마 어디까지 됐어요?", picker=None):
        u = self._utterance(body)
        with patch("apps.agent.services.react.default_client", llm), \
             patch("apps.agent.services.targeting.default_client", picker or Picker()):
            run_agent_for_utterance(str(u.id))
        return u

    def _answering(self):
        WorkItem.objects.create(project=self.project, owner=self.absent,
                                title="team_members 마이그레이션",
                                summary="진행 중", status="IN_PROGRESS")
        return FakeLLM(
            LLMResponse(text="STATUS"),
            LLMResponse(tool_calls=[ToolCall("c1", "search_records",
                                             {"query": "team_members"})]),
            LLMResponse(text="team_members 마이그레이션 진행 중입니다."),
        )

    def _labels(self):
        return list(FlowEdge.objects.order_by("occurred_at")
                    .values_list("label", flat=True))


class AnswerFlowTest(Base):

    def test_answer_draws_three_arrows(self):
        """
        사전 지시 · 질문 · 답변. **그 이상은 그리지 않습니다.**

        검색 호출까지 그리면 회의 하나에 화살표가 수십 개 쌓이고, 정작 무슨 일이
        있었는지가 안 보입니다.
        """
        self._run(self._answering())
        self.assertEqual(self._labels(), ["사전 지시", "질문", "대리인 답변"])

    def test_answer_reaches_everyone_present(self):
        """
        질문자 한 명에게만 그리면 회의에서 모두가 들은 사실이 화면에 안 남습니다.
        """
        self._run(self._answering())
        e = FlowEdge.objects.get(label="대리인 답변")
        names = {n["name"] for n in e.to_nodes}
        self.assertEqual(names, {"임수연", "최비성"})
        # 대리인 본인은 청중이 아닙니다.
        self.assertEqual(e.from_node["name"], "서재민의 Bordo")
        self.assertNotIn("서재민", names)

    def test_audience_order_is_stable(self):
        """
        순서가 흔들리면 조회 API 가 화살표를 묶는 키도 흔들립니다. 같은 두 사람
        사이 화살표가 어떤 회의에서는 하나로, 어떤 회의에서는 둘로 쪼개집니다.
        """
        self._run(self._answering())
        first = [n["id"] for n in FlowEdge.objects.get(label="대리인 답변").to_nodes]

        FlowEdge.objects.all().delete()
        self._run(self._answering())
        second = [n["id"] for n in FlowEdge.objects.get(label="대리인 답변").to_nodes]

        self.assertEqual(first, second)
        self.assertEqual(second, sorted(second), "user_id 정렬이 안 걸렸습니다")

    def test_agent_node_is_one_node_across_surfaces(self):
        """
        서비스 대리인과 Discord 대리인을 나눠 그리면 사용자는 자기 대리인이
        둘인 줄 압니다. 출처는 `surface` 로만 남깁니다.
        """
        self._run(self._answering())
        e = FlowEdge.objects.get(label="대리인 답변")
        self.assertEqual(e.from_node["id"], f"{self.absent.id}:agent")
        self.assertEqual(e.surface, Surface.DISCORD)

    def test_participant_ids_are_extracted(self):
        """필터가 JSON 배열 안을 뒤지지 않도록 따로 뽑아 둡니다."""
        self._run(self._answering())
        e = FlowEdge.objects.get(label="대리인 답변")
        self.assertEqual(set(e.participant_ids),
                         {str(self.absent.id), str(self.speaker.id),
                          str(self.present.id)})


class DeferFlowTest(Base):
    """
    유보는 **플로우에 그리지 않습니다.**

    필터로 걸러 보는 것이 아니라 사용자가 반드시 답해야 하는 일이라, 필터 축이
    아니라 알림 축에 있습니다(2026-08-17 디자인 확인). `PendingQuestion` 과
    브리핑의 `답변이 필요해요` 로 이미 사용자에게 갑니다.
    """

    def _defer(self):
        return self._run(FakeLLM(LLMResponse(text="STATUS"),
                                 LLMResponse(text="아마 곧 끝날 겁니다")))

    def test_defer_draws_no_arrow(self):
        """
        한동안 `기타`(ETC) 로 그렸는데 잘못이었습니다. `기타` 체크를 끈 사람에게는
        안 보이고, 켠 사람에게도 다른 화살표와 같은 무게로 섞입니다.
        """
        self._defer()
        self.assertEqual(
            FlowEdge.objects.filter(label="본인 확인 필요").count(), 0)
        self.assertEqual(
            FlowEdge.objects.filter(content_type=FlowContentType.ETC).count(), 0)

    def test_the_question_arrow_still_remains(self):
        """
        답을 못 했더라도 **질문이 저 사람에게 갔다는 사실은 남아야 합니다.**
        답이 없는 것과 질문이 닿지도 않은 것은 전혀 다른 이야기입니다.
        """
        self._defer()
        self.assertEqual(self._labels(), ["사전 지시", "질문"])

    def test_the_deferral_itself_is_still_recorded(self):
        """화살표를 안 그릴 뿐, 유보가 사라지면 안 됩니다."""
        from apps.agent.models import PendingQuestion

        self._defer()
        self.assertEqual(PendingQuestion.objects.count(), 1)


class NoiseTest(Base):

    def test_non_question_draws_nothing(self):
        """
        회의 발언 대부분은 질문입니다. 전부 그리면 플로우가 대화 로그가 됩니다.
        """
        self._run(FakeLLM(), body="아 넵 감사합니다", picker=Picker("0"))
        self.assertEqual(FlowEdge.objects.count(), 0)

    def test_delegate_prompt_is_drawn_only_once(self):
        """
        발언마다 그리면 질문 열 개짜리 회의에서 같은 화살표가 열 번 겹칩니다.
        """
        self._run(self._answering())
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(text="아마 곧 끝날 겁니다")))
        self.assertEqual(FlowEdge.objects.filter(label="사전 지시").count(), 1)

    def test_no_delegate_prompt_no_arrow(self):
        """지시를 안 남긴 사람에게 없는 지시를 그리면 안 됩니다."""
        MeetingParticipant.objects.filter(user=self.absent).update(delegate_prompt="")
        self._run(self._answering())
        self.assertEqual(FlowEdge.objects.filter(label="사전 지시").count(), 0)


class FailureTest(Base):

    def test_llm_failure_keeps_the_question_arrow(self):
        """
        답이 없는 것과 질문이 닿지도 않은 것은 사용자에게 전혀 다른 이야기입니다.
        대리인이 실패해도 "이 질문이 저 사람에게 갔다" 는 남아야 합니다.
        """
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(error="503", error_kind="retryable")))
        self.assertEqual(self._labels(), ["사전 지시", "질문"])

    def test_recording_failure_does_not_cancel_the_answer(self):
        """
        화살표 하나를 못 그렸다고 대리인의 답변이 취소되면 안 됩니다.
        화면이 덜 그려질 뿐입니다.
        """
        llm = self._answering()
        with patch("apps.meetings.models.FlowEdge.save",
                   side_effect=RuntimeError("디스크 꽉 참")):
            self._run(llm)
        self.assertEqual(FlowEdge.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 1, "답변이 회의에 안 나갔습니다")

    def test_lookup_failure_outside_record_also_spared(self):
        """
        `record()` 안쪽만 보호하면 부족합니다.

        청중을 구하는 참석자 조회는 그 바깥에서 돕니다. 거기서 터지면 예외가
        위로 올라가 **대리인이 회의에서 입을 다뭅니다.** 화살표를 못 그리는 것과
        답변이 통째로 사라지는 것은 전혀 다른 이야기입니다.
        """
        llm = self._answering()
        with patch("apps.agent.services.flow.answered",
                   side_effect=RuntimeError("DB 끊김")):
            self._run(llm)
        self.assertEqual(OutboxEvent.objects.count(), 1, "답변이 회의에 안 나갔습니다")

    def test_recording_failure_inside_a_transaction_keeps_the_briefing(self):
        """
        브리핑 생성은 `@transaction.atomic` 안에서 돕니다. 화살표를 못 그렸다고
        브리핑이 통째로 날아가면 안 됩니다.

        **이 테스트가 세이브포인트의 필요성까지 증명하지는 못합니다.**
        진짜 이유는 PostgreSQL 이 오류 난 트랜잭션을 abort 상태로 만들어 이후
        쿼리를 전부 거부한다는 것인데, 테스트 DB(SQLite)와 모킹한 예외로는
        그 상태를 만들 수 없습니다. `flow.record()` 의 세이브포인트는 운영
        DB 기준의 방어이고, 여기서는 브리핑이 살아남는 것까지만 봅니다.
        """
        from apps.agent.services import briefing

        self._run(self._answering())
        with patch("apps.meetings.models.FlowEdge.save",
                   side_effect=DatabaseError("제약 위반")):
            b = briefing.build_for_user(
                self.meeting, self.absent,
                client=FakeLLM(LLMResponse(text="정리했습니다")))

        self.assertIsNotNone(b, "브리핑이 통째로 날아갔습니다")
        self.assertEqual(FlowEdge.objects.filter(label="부재중 브리핑").count(), 0)


class OpacityTest(Base):
    """
    진하기는 **회의가 끝난 뒤에 채워집니다.**

    만들 때는 회의가 진행 중이라 `ended_at` 이 없고, `compute_opacity()` 의
    `newest` 가 `now` 라 방금 만든 엣지는 언제나 비율 1.0 입니다. 다시 계산하지
    않으면 화면이 통째로 균일해집니다 — 범위(0.25~1.0)만 보는 테스트로는
    이걸 못 잡습니다. 값이 실제로 갈리는지를 봐야 합니다.
    """

    def _spread_edges(self):
        """
        두 시간짜리 회의에 화살표 세 개 — 시작·중간·끝.

        시작을 과거로 당깁니다. `setUpTestData` 의 회의는 방금 시작한 것이라
        구간이 0 이고, 그러면 `compute_opacity()` 가 전부 1.0 을 돌려주어
        재계산이 됐는지 안 됐는지를 구별할 수 없습니다.
        """
        start = timezone.now() - timedelta(hours=2)
        self.meeting.started_at = start
        self.meeting.scheduled_at = start
        self.meeting.save(update_fields=["started_at", "scheduled_at"])
        for i in range(3):
            flow.record(self.meeting,
                        from_node=flow.user_node(self.speaker),
                        to_nodes=[flow.agent_node(self.absent)],
                        label=f"질문{i}", content_type=FlowContentType.REQUEST,
                        occurred_at=start + timedelta(hours=i))

    def test_in_meeting_edges_are_all_flat(self):
        """
        진행 중인 회의에서 만든 엣지는 전부 1.0 입니다.

        `occurred_at` 이 `now` 이고 `ended_at` 이 없어 `newest` 도 `now` 라,
        방금 만든 엣지는 언제나 자기가 가장 최근입니다. **이게 정상 동작이고,
        그래서 회의가 끝날 때 다시 계산해야 합니다.** 이 사실을 눌러 두지 않으면
        아래 재계산이 왜 필요한지가 테스트만 읽어서는 안 드러납니다.
        """
        # 회의 시작을 과거로 당깁니다.
        #
        # `setUpTestData` 의 회의는 방금 시작한 것이라 구간이 사실상 0 입니다.
        # 그러면 엣지를 만드는 데 걸린 몇 밀리초가 비율을 눈에 띄게 끌어내려
        # 0.999 같은 값이 나오고, **테스트가 장비가 느린 날에만 실패합니다.**
        # 진행 중인 회의라는 조건(ended_at 없음)은 그대로입니다.
        self.meeting.started_at = timezone.now() - timedelta(hours=2)
        self.meeting.scheduled_at = self.meeting.started_at
        self.meeting.save(update_fields=["started_at", "scheduled_at"])

        self._run(self._answering())
        # 사전 지시는 회의 시작 시각으로 찍히므로 예외입니다. 회의 중에 생긴
        # 것들만 봅니다 — 실제 회의에서는 이쪽이 거의 전부입니다.
        vals = set(FlowEdge.objects.exclude(label="사전 지시")
                   .values_list("opacity", flat=True))
        self.assertEqual(vals, {1.0}, f"실제 경로에서 나온 값: {vals}")

    def test_recompute_at_meeting_end_actually_spreads(self):
        self._spread_edges()
        self.meeting.ended_at = self.meeting.started_at + timedelta(hours=2)
        self.meeting.save(update_fields=["ended_at"])

        self.assertEqual(flow.recompute_for_meeting(self.meeting), 3)

        vals = list(FlowEdge.objects.order_by("occurred_at")
                    .values_list("opacity", flat=True))
        self.assertEqual(vals, sorted(vals), "최근일수록 진해야 합니다")
        self.assertEqual(len(set(vals)), 3, f"값이 안 갈렸습니다: {vals}")
        self.assertGreaterEqual(min(vals), 0.25)
        self.assertLessEqual(max(vals), 1.0)

    def test_recompute_is_stable_across_days(self):
        """
        조회 시각으로 계산하면 같은 회의를 내일 열었을 때 그림이 달라집니다.
        회의 구간은 고정이므로 몇 번을 돌려도 같은 값이어야 합니다.
        """
        self._spread_edges()
        self.meeting.ended_at = self.meeting.started_at + timedelta(hours=2)
        self.meeting.save(update_fields=["ended_at"])

        flow.recompute_for_meeting(self.meeting)
        first = list(FlowEdge.objects.order_by("occurred_at")
                     .values_list("opacity", flat=True))
        flow.recompute_for_meeting(self.meeting)
        second = list(FlowEdge.objects.order_by("occurred_at")
                      .values_list("opacity", flat=True))
        self.assertEqual(first, second)

    @override_settings(BORDO={**settings.BORDO, "SERVICE_TOKEN": _TOKEN})
    def test_meeting_end_triggers_recompute(self):
        """
        재계산 함수가 있어도 회의 종료에서 안 부르면 화면은 그대로 균일합니다.

        회의 중 상태를 그대로 만들어 둡니다 — 시각은 구간에 퍼져 있지만 저장된
        값은 전부 1.0. 이렇게 해 두지 않으면 종료가 재계산을 안 불러도
        테스트가 통과해 버립니다.
        """
        self._spread_edges()
        FlowEdge.objects.update(opacity=1.0)

        self.meeting.discord_channel_id = "th-1"
        self.meeting.save(update_fields=["discord_channel_id"])

        r = self.client.post(
            "/internal/v1/meetings/end",
            {"thread_id": "th-1"},
            content_type="application/json",
            HTTP_X_SERVICE_TOKEN=_TOKEN)
        self.assertEqual(r.status_code, 200, r.content[:300])

        vals = set(FlowEdge.objects.values_list("opacity", flat=True))
        self.assertNotEqual(vals, {1.0}, "회의 종료가 진하기를 다시 계산하지 않았습니다")
        self.assertEqual(len(vals), 3)


class ArtifactTest(Base):

    def test_proposed_task_shows_up_in_the_flow(self):
        """
        "AI 가 뭘 만들어 뒀다" 가 안 보이면, 승인 대기 목록에 항목이 갑자기
        생긴 것처럼 보입니다.
        """
        from apps.agent.services.skills import SkillContext
        from apps.agent.services.skills.act import ProposeTaskSkill

        ctx = SkillContext(principal_id=str(self.absent.id),
                           actor_id=str(self.speaker.id),
                           meeting_id=str(self.meeting.id),
                           project_id=str(self.project.id))
        ProposeTaskSkill().run({"title": "인덱스 추가"}, ctx)

        e = FlowEdge.objects.get(label="인덱스 추가")
        self.assertEqual(e.content_type, FlowContentType.PLAN)
        self.assertEqual(e.to_nodes[0]["kind"], "SERVER")
        # 서버 노드에는 사람이 없습니다 — participant_ids 에 들어가면 필터가 틀립니다.
        self.assertEqual(e.participant_ids, [str(self.absent.id)])

    def test_proposed_schedule_is_a_schedule(self):
        """화살표에 `일정` 뱃지가 붙는 자리입니다."""
        from apps.agent.services.skills import SkillContext
        from apps.agent.services.skills.act import ProposeScheduleSkill

        ctx = SkillContext(principal_id=str(self.absent.id),
                           actor_id=str(self.speaker.id),
                           meeting_id=str(self.meeting.id),
                           project_id=str(self.project.id))
        ProposeScheduleSkill().run(
            {"title": "스프린트 회고", "start_at": "2026-09-07T10:00:00+09:00"}, ctx)
        self.assertEqual(FlowEdge.objects.get(label="스프린트 회고").content_type,
                         FlowContentType.SCHEDULE)

    def test_no_meeting_no_arrow(self):
        """웹 대화에서 만든 후보는 그릴 회의가 없습니다."""
        from apps.agent.services.skills import SkillContext
        from apps.agent.services.skills.act import ProposeTaskSkill

        ctx = SkillContext(principal_id=str(self.absent.id),
                           actor_id=str(self.absent.id),
                           project_id=str(self.project.id))
        r = ProposeTaskSkill().run({"title": "인덱스 추가"}, ctx)
        self.assertTrue(r.ok, "회의가 없다고 태스크 생성까지 실패하면 안 됩니다")
        self.assertEqual(FlowEdge.objects.count(), 0)


class BriefingFlowTest(Base):

    def test_briefing_closes_the_loop(self):
        """
        "내가 없는 동안 무슨 일이 있었지" 의 마지막 칸입니다.
        """
        from apps.agent.services import briefing

        self._run(self._answering())
        FlowEdge.objects.all().delete()          # 회의 중 화살표는 따로 봤습니다

        briefing.build_for_user(self.meeting, self.absent,
                                client=FakeLLM(LLMResponse(text="정리했습니다")))
        e = FlowEdge.objects.get(label="부재중 브리핑")
        self.assertEqual(e.content_type, FlowContentType.CONCLUSION)
        self.assertEqual(e.surface, Surface.SERVICE)
        self.assertEqual(e.to_nodes[0]["name"], "서재민")


class FlowScreenTest(Base):
    """
    행이 생기는 것과 **화면이 그려지는 것**은 다릅니다.

    지금까지 이 화면은 시드가 심은 하드코딩으로만 그려졌습니다. 실제 회의에서
    나온 엣지가 조회 API 를 통과하는지는 아무도 확인한 적이 없습니다.
    """

    def setUp(self):
        from rest_framework.test import APIClient

        from apps.orgs.models import ProjectMember, TeamMember, TeamRole
        TeamMember.objects.get_or_create(
            team=self.team, user=self.speaker,
            defaults={"team_role": TeamRole.MEMBER})
        ProjectMember.objects.get_or_create(project=self.project, user=self.speaker)

        self.api = APIClient()
        self.api.force_authenticate(user=self.speaker)

    def test_generated_edges_render(self):
        self._run(self._answering())
        r = self.api.get(f"/api/v1/meetings/{self.meeting.id}/flow")
        self.assertEqual(r.status_code, 200, r.content[:300])

        data = r.json()
        self.assertTrue(data["arrows"], "회의를 했는데 화살표가 하나도 없습니다")
        # 사람 쌍마다 하나로 묶입니다 — 사전 지시 · 질문 · 답변은 쌍이 다 다릅니다.
        self.assertEqual(len(data["arrows"]), 3)
        self.assertIn("서재민의 Bordo", {n["name"] for n in data["nodes"]})
        self.assertIn(Surface.DISCORD, data["filter_options"]["surfaces"])

    def test_content_filter_reaches_generated_edges(self):
        """
        필터 칸을 눌렀을 때 걸리는지까지 봐야 합니다. 종류를 잘못 넣으면
        행은 있는데 화면에서는 안 보입니다.
        """
        self._run(self._answering())
        r = self.api.get(f"/api/v1/meetings/{self.meeting.id}/flow",
                            {"content_types": FlowContentType.OPINION})
        self.assertEqual(r.status_code, 200, r.content[:300])
        arrows = r.json()["arrows"]
        self.assertEqual(len(arrows), 1)
        self.assertEqual(arrows[0]["counts"][0]["content_type"],
                         FlowContentType.OPINION)


class NodeShapeTest(TestCase):
    """
    노드 모양은 프론트가 그대로 읽습니다. 키가 빠지면 화면이 깨집니다.

    **`seed_demo.py` 와 글자 하나까지 같아야 합니다.** 프론트는 `id` 로 노드를
    합치므로, 표기가 갈리면 시드 회의와 실제 회의에서 같은 사람의 대리인이
    화면에 두 개로 그려집니다.
    """

    def setUp(self):
        self.u = User.objects.create_user(email="n@bordo.dev", password="x" * 10,
                                          name="유수인")

    def test_node_shape_matches_seed(self):
        # seed_demo.py 의 node() 가 만드는 모양 그대로입니다.
        #   USER   {u.id}          / {name}
        #   AGENT  {u.id}:agent    / {name}의 Bordo
        self.assertEqual(flow.user_node(self.u)["id"], str(self.u.id))
        self.assertEqual(flow.agent_node(self.u)["id"], f"{self.u.id}:agent")
        self.assertEqual(flow.agent_node(self.u)["name"], "유수인의 Bordo")
        self.assertEqual(flow.user_node(self.u)["kind"], "USER")

    def test_agent_is_not_called_ai(self):
        """
        `AI 대리인` 은 화면 어디에도 없는 낱말입니다. CLAUDE.md 의
        `디자인이 계약을 이겼던 곳` 에 호칭이 못박혀 있습니다.
        """
        self.assertNotIn("AI", flow.agent_node(self.u)["name"])

    def test_avatar_is_carried(self):
        """
        조회 API 가 화살표의 아바타 목록을 `to_nodes[].avatar_url` 에서 모읍니다.
        키가 없으면 아바타가 영원히 빈 배열입니다.
        """
        self.assertIn("avatar_url", flow.user_node(self.u))
        self.assertIn("avatar_url", flow.agent_node(self.u))

    def test_server_node_has_no_user(self):
        n = flow.server_node()
        self.assertEqual(set(n), {"id", "kind", "user_id", "name", "avatar_url"})
        self.assertIsNone(n["user_id"])


class AgendaLinkTest(Base):
    """
    화살표를 안건에 잇습니다.

    좌측 `인덱스` 에서 안건을 누르면 관련 화살표로 점프하는데, 그 연결이 비어
    있어서 인덱스가 아무 데도 못 갔습니다.

    **짐작입니다.** 발언과 안건을 잇는 명시적 신호가 없어(Discord 에서 "지금부터
    2번 안건" 이라고 선언하지 않습니다) 낱말 겹침으로 고릅니다. 그래서
    **애매하면 비우는 쪽**을 택했는지가 이 테스트의 핵심입니다.
    """

    def _agenda(self, title):
        from apps.meetings.models import Agenda
        return Agenda.objects.create(meeting=self.meeting, title=title)

    def test_a_clear_match_is_linked(self):
        a = self._agenda("team_members 마이그레이션 설계")
        self._run(self._answering(), body="team_members 마이그레이션 어디까지 됐어요?")
        self.assertEqual(FlowEdge.objects.get(label="질문").agenda_id, a.id)

    def test_a_single_shared_word_is_not_enough(self):
        """
        겹치는 낱말이 하나뿐이면 우연일 가능성이 큽니다. 틀린 안건에 붙으면
        인덱스를 눌렀을 때 엉뚱한 곳으로 갑니다 — 비어 있는 것보다 나쁩니다.
        """
        self._agenda("마이그레이션 일정 조율")
        self._run(self._answering(), body="마이그레이션 어디까지 됐어요?")
        self.assertIsNone(FlowEdge.objects.get(label="질문").agenda_id)

    def test_a_tie_links_nothing(self):
        """두 안건이 똑같이 맞으면 아무것도 고르지 않습니다."""
        self._agenda("team_members 마이그레이션 설계")
        self._agenda("team_members 마이그레이션 검토")
        self._run(self._answering(), body="team_members 마이그레이션 어디까지 됐어요?")
        self.assertIsNone(FlowEdge.objects.get(label="질문").agenda_id)

    def test_no_agenda_is_fine(self):
        """안건을 안 적은 회의도 있습니다. 그때도 화살표는 그려져야 합니다."""
        self._run(self._answering())
        self.assertIsNotNone(FlowEdge.objects.filter(label="질문").first())

    def test_the_answer_lands_on_the_same_agenda(self):
        """질문과 답이 다른 안건에 흩어지면 인덱스가 둘로 갈립니다."""
        a = self._agenda("team_members 마이그레이션 설계")
        self._run(self._answering(), body="team_members 마이그레이션 어디까지 됐어요?")
        self.assertEqual(FlowEdge.objects.get(label="대리인 답변").agenda_id, a.id)

    def test_the_index_can_now_jump(self):
        """
        이 연결이 있어야 `GET /meetings/{id}/indexes` 의 `related_edge_ids` 가
        찹니다. 비어 있으면 인덱스를 눌러도 아무 일이 안 일어납니다.
        """
        from apps.orgs.models import ProjectMember, TeamMember, TeamRole
        from rest_framework.test import APIClient

        a = self._agenda("team_members 마이그레이션 설계")
        self._run(self._answering(), body="team_members 마이그레이션 어디까지 됐어요?")

        TeamMember.objects.get_or_create(team=self.team, user=self.speaker,
                                         defaults={"team_role": TeamRole.MEMBER})
        ProjectMember.objects.get_or_create(project=self.project, user=self.speaker)
        api = APIClient()
        api.force_authenticate(user=self.speaker)

        r = api.get(f"/api/v1/meetings/{self.meeting.id}/indexes")
        self.assertEqual(r.status_code, 200, r.content[:200])
        rows = [x for x in r.json()["results"] if x["id"] == str(a.id)]
        self.assertTrue(rows and rows[0]["related_edge_ids"],
                        "인덱스가 화살표로 못 갑니다")
