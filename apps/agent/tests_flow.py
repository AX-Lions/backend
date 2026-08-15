"""
플로우 엣지 생성 테스트.

`FlowEdge` 는 **핵심 화면 두 개 중 하나의 유일한 데이터원**입니다. 지금까지 시드가
심은 하드코딩만 있어서, 새 회의를 열면 화면이 비었습니다.

여기서 보는 것은 두 가지입니다.

    1. 의미 있는 변화만 남는가 — 검색 호출까지 그리면 화살표에 묻혀 아무것도 안 보입니다
    2. 기록이 실패해도 대리인은 계속 도는가 — 화살표 하나 때문에 답변이 사라지면 안 됩니다
"""
from unittest.mock import patch

from django.test import TestCase
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
        self.assertEqual(e.from_node["name"], "서재민의 AI")
        self.assertNotIn("서재민", names)

    def test_agent_node_is_one_node_across_surfaces(self):
        """
        서비스 대리인과 Discord 대리인을 나눠 그리면 사용자는 자기 대리인이
        둘인 줄 압니다. 출처는 `surface` 로만 남깁니다.
        """
        self._run(self._answering())
        e = FlowEdge.objects.get(label="대리인 답변")
        self.assertEqual(e.from_node["id"], f"agent:{self.absent.id}")
        self.assertEqual(e.surface, Surface.DISCORD)

    def test_participant_ids_are_extracted(self):
        """필터가 JSON 배열 안을 뒤지지 않도록 따로 뽑아 둡니다."""
        self._run(self._answering())
        e = FlowEdge.objects.get(label="대리인 답변")
        self.assertEqual(set(e.participant_ids),
                         {str(self.absent.id), str(self.speaker.id),
                          str(self.present.id)})


class DeferFlowTest(Base):

    def test_defer_is_not_the_same_kind_as_an_answer(self):
        """
        화면에서 "답했다" 와 "확인이 필요하다" 가 같은 색으로 보이면,
        유보를 보여 주는 의미가 사라집니다.
        """
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(text="아마 곧 끝날 겁니다")))
        e = FlowEdge.objects.get(label="본인 확인 필요")
        self.assertEqual(e.content_type, FlowContentType.ETC)
        self.assertNotEqual(e.content_type, FlowContentType.OPINION)

    def test_defer_still_draws_the_question(self):
        self._run(FakeLLM(LLMResponse(text="STATUS"),
                          LLMResponse(text="아마 곧 끝날 겁니다")))
        self.assertEqual(self._labels(), ["사전 지시", "질문", "본인 확인 필요"])


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


class OpacityTest(Base):

    def test_opacity_is_computed_from_the_meeting_span(self):
        """
        조회 시각 기준으로 하면 같은 회의를 내일 열었을 때 그림이 달라집니다.
        """
        self._run(self._answering())
        for e in FlowEdge.objects.all():
            self.assertGreaterEqual(e.opacity, 0.25)
            self.assertLessEqual(e.opacity, 1.0)


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
        self.assertIn("서재민의 AI", {n["name"] for n in data["nodes"]})
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
    """노드 모양은 프론트가 그대로 읽습니다. 키가 빠지면 화면이 깨집니다."""

    def test_server_node_has_no_user(self):
        n = flow.server_node()
        self.assertEqual(set(n), {"id", "kind", "user_id", "name"})
        self.assertIsNone(n["user_id"])

    def test_agent_name_follows_the_owner(self):
        u = User.objects.create_user(email="n@bordo.dev", password="x" * 10, name="유수인")
        self.assertEqual(flow.agent_node(u)["name"], "유수인의 AI")
        self.assertEqual(flow.user_node(u)["kind"], "USER")
