"""
읽기 스킬 테스트.

여기서 가장 중요한 것은 **남의 비공개 기록이 새지 않는가** 입니다.
한 번 새면 되돌릴 수 없습니다.
"""
from django.test import TestCase

from apps.accounts.models import User
from apps.agent.services.skills import (SearchMeetingSkill, SearchRecordsSkill,
                                        SkillContext, ThinkSkill)
from apps.documents.models import Document
from apps.meetings.models import Agenda, Meeting, MeetingSummary, Utterance
from apps.orgs.models import Project, Team
from apps.states.models import ThoughtItem, WorkItem


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="나")
        cls.other = User.objects.create_user(email="other@bordo.dev", password="x" * 10,
                                             name="남")
        team = Team.objects.create(name="팀", created_by=cls.me)
        cls.project = Project.objects.create(team=team, team_name="팀", name="프로젝트",
                                             created_by=cls.me)

        cls.mine = WorkItem.objects.create(
            project=cls.project, owner=cls.me, title="team_members 마이그레이션",
            summary="스키마 확인 대기", status="IN_PROGRESS", category="database")
        cls.theirs = WorkItem.objects.create(
            project=cls.project, owner=cls.other, title="마이그레이션 리뷰",
            summary="", status="IN_PROGRESS")
        cls.secret = ThoughtItem.objects.create(
            project=cls.project, owner=cls.me, topic="마이그레이션 우려",
            content="혼잣말", visibility="private")
        cls.their_secret = ThoughtItem.objects.create(
            project=cls.project, owner=cls.other, topic="마이그레이션 비밀",
            content="남의 혼잣말", visibility="private")

    def ctx(self, **kw):
        base = dict(principal_id=str(self.me.id), actor_id=str(self.other.id),
                    project_id=str(self.project.id))
        base.update(kw)
        return SkillContext(**base)


class ThinkTest(TestCase):

    def test_records_reasoning(self):
        r = ThinkSkill().run({"reasoning": "먼저 작업 기록을 본다"},
                             SkillContext(principal_id="u"))
        self.assertTrue(r.ok)
        self.assertIn("작업 기록", r.data["reasoning"])

    def test_empty_is_rejected(self):
        """steps 에 빈 줄이 남으면 화면에 그대로 뜹니다."""
        r = ThinkSkill().run({"reasoning": "  "}, SkillContext(principal_id="u"))
        self.assertFalse(r.ok)


class SearchRecordsTest(Base):

    def setUp(self):
        self.skill = SearchRecordsSkill()

    # ── 비공개 ─────────────────────────────────────────────
    def test_others_private_never_appears(self):
        """남의 비공개는 존재 여부조차 반환하지 않습니다."""
        r = self.skill.run({"query": "마이그레이션"}, self.ctx(allow_private=True))
        ids = [e["source_id"] for e in r.evidence]
        self.assertNotIn(str(self.their_secret.id), ids)

    def test_my_private_hidden_in_meeting(self):
        """회의 대리에서는 본인 비공개도 꺼내지 않습니다."""
        r = self.skill.run({"query": "마이그레이션"}, self.ctx(allow_private=False))
        ids = [e["source_id"] for e in r.evidence]
        self.assertNotIn(str(self.secret.id), ids)

    def test_my_private_visible_in_personal_chat(self):
        """본인이 자기 AI 와 대화할 때는 숨길 이유가 없습니다."""
        r = self.skill.run({"query": "마이그레이션"}, self.ctx(allow_private=True))
        ids = [e["source_id"] for e in r.evidence]
        self.assertIn(str(self.secret.id), ids)

    # ── 소유 판정 ──────────────────────────────────────────
    def test_owner_flag(self):
        """유보 규칙 R2 가 이 값 위에 섭니다."""
        r = self.skill.run({"query": "마이그레이션"}, self.ctx())
        by_id = {e["source_id"]: e for e in r.evidence}
        self.assertTrue(by_id[str(self.mine.id)]["owner_is_principal"])
        self.assertFalse(by_id[str(self.theirs.id)]["owner_is_principal"])

    # ── match 라벨 ─────────────────────────────────────────
    def test_title_hit_is_direct(self):
        r = self.skill.run({"query": "team_members"}, self.ctx())
        hit = [e for e in r.evidence if e["source_id"] == str(self.mine.id)][0]
        self.assertEqual(hit["match"], "direct")

    def test_body_only_hit_is_partial(self):
        r = self.skill.run({"query": "스키마 확인"}, self.ctx())
        hit = [e for e in r.evidence if e["source_id"] == str(self.mine.id)][0]
        self.assertEqual(hit["match"], "partial")

    def test_category_only_hit_is_inferred(self):
        r = self.skill.run({"query": "database"}, self.ctx())
        hit = [e for e in r.evidence if e["source_id"] == str(self.mine.id)][0]
        self.assertEqual(hit["match"], "inferred")

    def test_direct_comes_first(self):
        """LLM 이 앞쪽을 더 봅니다."""
        r = self.skill.run({"query": "마이그레이션"}, self.ctx())
        self.assertEqual(r.evidence[0]["match"], "direct")

    # ── 없음 / 오류 ────────────────────────────────────────
    def test_no_result_is_still_ok(self):
        """'찾았는데 없었다' 와 '검색이 터졌다' 는 다릅니다."""
        r = self.skill.run({"query": "존재하지않는단어zzz"}, self.ctx())
        self.assertTrue(r.ok)

    def test_no_match_falls_back_to_my_recent_records(self):
        """
        빈손으로 돌려주면 모델이 검색어만 바꿔 가며 헛돕니다.
        무엇이 있는지 보여주면 다음 호출에서 정확한 말로 다시 찾습니다.
        """
        r = self.skill.run({"query": "존재하지않는단어zzz"}, self.ctx())
        self.assertTrue(r.evidence)
        self.assertTrue(all(e["owner_is_principal"] for e in r.evidence))

    def test_fallback_is_inferred_so_it_cannot_be_answered_on(self):
        """길잡이는 근거가 아닙니다. 그대로 답으로 가면 R3 에 걸려야 합니다."""
        r = self.skill.run({"query": "존재하지않는단어zzz"}, self.ctx())
        self.assertTrue(all(e["match"] == "inferred" for e in r.evidence))

    def test_token_search_finds_across_wording(self):
        """
        모델은 'DB 스키마 작업 진행상황' 처럼 문장에 가까운 검색어를 만듭니다.
        저장된 제목은 'team_members 마이그레이션' 이라 통째로는 안 걸립니다.
        """
        r = self.skill.run({"query": "마이그레이션 진행 상황"}, self.ctx())
        ids = [e["source_id"] for e in r.evidence]
        self.assertIn(str(self.mine.id), ids)

    def test_empty_query_is_rejected(self):
        self.assertFalse(self.skill.run({"query": ""}, self.ctx()).ok)

    def test_missing_project_is_rejected(self):
        r = self.skill.run({"query": "x"}, SkillContext(principal_id="u"))
        self.assertFalse(r.ok)

    def test_kinds_filter(self):
        r = self.skill.run({"query": "마이그레이션", "kinds": ["document"]}, self.ctx())
        self.assertEqual(r.evidence, [])

    # ── 문서 ───────────────────────────────────────────────
    def test_document_uses_summary_as_excerpt(self):
        """본문 앞부분은 대개 머리말이라 무엇에 관한 문서인지 안 드러납니다."""
        Document.objects.create(project=self.project, owner=self.me,
                                title="DB 설계", content="# 머리말\n\n본문",
                                summary="테이블 구조 정리")
        r = self.skill.run({"query": "DB 설계"}, self.ctx())
        doc = [e for e in r.evidence if e["source_type"] == "document"][0]
        self.assertEqual(doc["excerpt"], "테이블 구조 정리")


class SearchMeetingTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.utils import timezone
        cls.meeting = Meeting.objects.create(
            project=cls.project, project_name="프로젝트", title="정기 회의",
            scheduled_at=timezone.now(), created_by=cls.me)
        Utterance.objects.create(meeting=cls.meeting, participant=cls.other,
                                 participant_name="남", body="DB 스키마 언제 끝나요")
        Agenda.objects.create(meeting=cls.meeting, title="DB 스키마",
                              content="마이그레이션 일정 확인")
        MeetingSummary.objects.create(meeting=cls.meeting, one_line="스키마 논의",
                                      discovered_issues=["인덱스 누락"],
                                      changes=["일정 하루 연기"], next_plans=["재측정"])

    def setUp(self):
        self.skill = SearchMeetingSkill()

    def test_finds_utterance_and_agenda(self):
        r = self.skill.run({"query": "스키마"},
                           self.ctx(meeting_id=str(self.meeting.id)))
        self.assertTrue(r.ok)
        self.assertGreaterEqual(len(r.evidence), 2)

    def test_summary_is_one_item(self):
        """세 갈래를 나눠 넣으면 근거 3건으로 세어져 판정이 착각합니다."""
        r = self.skill.run({"scope": "summary"},
                           self.ctx(meeting_id=str(self.meeting.id)))
        self.assertEqual(len(r.evidence), 1)

    def test_meeting_records_are_team_visible(self):
        """참석자가 이미 함께 들은 내용이라 공개 판정 대상이 아닙니다."""
        r = self.skill.run({}, self.ctx(meeting_id=str(self.meeting.id)))
        self.assertTrue(all(e["visibility"] == "team" for e in r.evidence))

    def test_missing_meeting_is_rejected(self):
        r = self.skill.run({"query": "x"}, self.ctx())
        self.assertFalse(r.ok)
