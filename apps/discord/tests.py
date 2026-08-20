"""
`/internal/v1` 테스트.

봇과의 계약 지점입니다. 여기가 어긋나면 **양쪽 다 멀쩡한데 회의가 아예 안 돌아갑니다.**

가장 중요한 것은 원본 보존입니다 — 봇이 보낸 발언이 요약되거나 걸러지지 않고
그대로 남아야, 나중에 대리인이 사람마다 다르게 정리해 줄 수 있습니다.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from django.conf import settings

from apps.accounts.models import User
from apps.agent.models import AgentSettings
from apps.discord.models import GuildLink, LinkCode
from apps.meetings.models import (Attendance, Meeting, MeetingParticipant,
                                  MeetingStatus, Utterance)
from apps.orgs.models import Project, Team, TeamMember

#: 테스트 전용 토큰. 설정에 기본값을 두지 않으므로 여기서 주입합니다 —
#: 저장소에 실제로 쓰이는 값이 남지 않게 하기 위해서입니다.
TOKEN = "test-only-token"
GUILD = "guild-1"
THREAD = "thread-1"

_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS)
class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(email="me@bordo.dev", password="x" * 10,
                                          name="서재민", discord_user_id="dc-me")
        cls.mate = User.objects.create_user(email="mate@bordo.dev", password="x" * 10,
                                            name="임수연", discord_user_id="dc-mate")
        cls.team = Team.objects.create(name="AX Lions", created_by=cls.me)
        TeamMember.objects.create(team=cls.team, user=cls.me, team_role="OWNER")
        cls.project = Project.objects.create(team=cls.team, team_name="AX Lions",
                                             name="Bordo", created_by=cls.me)
        GuildLink.objects.create(guild_id=GUILD, team=cls.team)
        AgentSettings.objects.create(user=cls.me)

    def post(self, path, payload=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token is not None else {}
        return self.client.post(f"/internal/v1{path}", payload or {},
                                content_type="application/json", **headers)

    def get(self, path, params=None, token=TOKEN):
        headers = {"HTTP_X_SERVICE_TOKEN": token} if token is not None else {}
        return self.client.get(f"/internal/v1{path}", params or {}, **headers)

    def _start(self, participants=None):
        return self.post("/meetings/start", {
            "guild_id": GUILD, "thread_id": THREAD, "agenda": "DB 스키마 리뷰",
            "participants": participants or [],
        })


class AuthTest(Base):

    def test_missing_token_is_rejected(self):
        # GET 전용 경로에 POST 하면 토큰 검사 전에 405 가 납니다. POST 경로로 봅니다.
        r = self.post("/delegate/on", {"discord_user_id": "dc-me"}, token=None)
        self.assertEqual(r.status_code, 401)

    def test_wrong_token_is_rejected(self):
        r = self.post("/delegate/on", {"discord_user_id": "dc-me"}, token="틀림")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "AUTH_SERVICE_TOKEN_INVALID")

    def test_jwt_is_not_required(self):
        """봇은 특정 사용자로 로그인하지 않습니다."""
        self.assertEqual(self.get("/teams/current", {"guild_id": GUILD}).status_code, 200)


class ConnectTest(Base):

    def test_issues_a_code(self):
        r = self.post("/discord/connect/code", {"discord_user_id": "dc-new"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(r.json()["code"]), 6)

    def test_previous_code_is_invalidated(self):
        """여러 개가 살아 있으면 어느 것이 최신인지 사용자가 알 수 없습니다."""
        self.post("/discord/connect/code", {"discord_user_id": "dc-new"})
        self.post("/discord/connect/code", {"discord_user_id": "dc-new"})
        alive = [c for c in LinkCode.objects.filter(discord_user_id="dc-new") if c.alive]
        self.assertEqual(len(alive), 1)

    def test_code_expires(self):
        self.post("/discord/connect/code", {"discord_user_id": "dc-new"})
        row = LinkCode.objects.get(discord_user_id="dc-new")
        self.assertGreater(row.expires_at, timezone.now())
        self.assertLessEqual(
            (row.expires_at - timezone.now()).total_seconds(), 601)


class TeamTest(Base):

    def test_by_guild(self):
        r = self.get("/teams/current", {"guild_id": GUILD})
        self.assertEqual(r.json()["team_id"], str(self.team.id))

    def test_unlinked_guild(self):
        r = self.get("/teams/current", {"guild_id": "없는서버"})
        self.assertEqual(r.status_code, 404)

    def test_by_user_in_dm(self):
        """봇이 DM 에서 부르면 guild_id 가 없습니다."""
        r = self.get("/teams/current", {"discord_user_id": "dc-me"})
        self.assertTrue(r.json()["linked"])
        self.assertEqual(len(r.json()["teams"]), 1)


class TeamLinkTest(Base):
    """
    서버-팀 연결. 코드를 따로 두지 않고 이미 이어진 계정의 권한으로 판정합니다.
    """

    def setUp(self):
        GuildLink.objects.all().delete()

    def _link(self, **kw):
        payload = {"guild_id": "새서버", "discord_user_id": "dc-me"}
        payload.update(kw)
        return self.post("/teams/link", payload)

    def test_owner_can_link(self):
        r = self._link()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(GuildLink.objects.get(guild_id="새서버").team_id, self.team.id)

    def test_relink_overwrites(self):
        self._link()
        r = self._link()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])

    def test_plain_member_is_rejected(self):
        """일반 멤버가 붙이면 팀 전체의 회의가 그 서버로 흘러갑니다."""
        TeamMember.objects.filter(user=self.me).update(team_role="MEMBER")
        self.assertEqual(self._link().status_code, 403)

    def test_unlinked_account_is_rejected(self):
        """계정 연결이 먼저입니다."""
        self.assertEqual(self._link(discord_user_id="모르는사람").status_code, 404)

    def test_multiple_teams_needs_a_choice(self):
        """잘못 고르면 남의 팀 회의가 이 서버로 흘러갑니다."""
        other = Team.objects.create(name="다른 팀", created_by=self.me)
        TeamMember.objects.create(team=other, user=self.me, team_role="OWNER")
        r = self._link()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(r.json()["error"]["details"]["teams"]), 2)

    def test_explicit_team_id(self):
        other = Team.objects.create(name="다른 팀", created_by=self.me)
        TeamMember.objects.create(team=other, user=self.me, team_role="OWNER")
        r = self._link(team_id=str(other.id))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(GuildLink.objects.get(guild_id="새서버").team_id, other.id)

    def test_team_i_do_not_own(self):
        stranger = User.objects.create_user(email="x@bordo.dev", password="x" * 10,
                                            name="남", discord_user_id="dc-x")
        theirs = Team.objects.create(name="남의 팀", created_by=stranger)
        r = self._link(team_id=str(theirs.id))
        self.assertEqual(r.status_code, 403)


class DelegateTest(Base):

    def setUp(self):
        self._start()
        self.meeting = Meeting.objects.get()
        MeetingParticipant.objects.create(meeting=self.meeting, user=self.me,
                                          user_name="서재민")

    def test_on_and_off(self):
        self.post("/delegate/on", {"discord_user_id": "dc-me", "scope": "전체"})
        p = MeetingParticipant.objects.get(user=self.me)
        self.assertTrue(p.delegated)
        # 웹 경로와 같은 값이어야 화면 뱃지가 갈리지 않습니다.
        self.assertEqual(p.attendance, Attendance.DELEGATED)

        self.post("/delegate/off", {"discord_user_id": "dc-me"})
        p.refresh_from_db()
        self.assertFalse(p.delegated)

    def test_ended_meeting_is_untouched(self):
        """끝난 회의의 참석 상태를 뒤늦게 바꾸면 그때의 기록과 어긋납니다."""
        self.meeting.status = MeetingStatus.ENDED
        self.meeting.save()
        self.post("/delegate/on", {"discord_user_id": "dc-me"})
        self.assertFalse(MeetingParticipant.objects.get(user=self.me).delegated)

    def test_unlinked_user(self):
        self.assertEqual(
            self.post("/delegate/on", {"discord_user_id": "모르는사람"}).status_code, 404)


class MeetingTest(Base):

    def test_start_creates_meeting(self):
        r = self._start([{"discord_user_id": "dc-me", "status": "delegated"}])
        self.assertEqual(r.status_code, 201)
        m = Meeting.objects.get()
        self.assertEqual(m.status, MeetingStatus.ACTIVE)
        self.assertEqual(m.discord_channel_id, THREAD)
        self.assertTrue(MeetingParticipant.objects.get(user=self.me).delegated)

    def test_same_thread_returns_existing(self):
        """회의가 두 개 생기면 발언이 갈라져 어느 쪽도 온전하지 않습니다."""
        self._start()
        r = self._start()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["duplicate"])
        self.assertEqual(Meeting.objects.count(), 1)

    def test_unlinked_participant_is_skipped(self):
        """계정을 아직 안 이은 사람 때문에 회의가 막히면 안 됩니다."""
        r = self._start([{"discord_user_id": "모르는사람", "status": "present"}])
        self.assertEqual(r.status_code, 201)

    def test_legacy_path_is_gone(self):
        """봇이 /meetings/start 로 옮겨 왔으므로 옛 경로는 없습니다."""
        r = self.post("/meetings", {"guild_id": GUILD, "thread_id": "t-2",
                                    "agenda": "x"})
        self.assertEqual(r.status_code, 404)

    def test_end_generates_summary_in_backend(self):
        """봇이 요약해서 보내지 않습니다. 원본만 받고 여기서 만듭니다."""
        self._start()
        m = Meeting.objects.get()
        Utterance.objects.create(meeting=m, participant=self.mate,
                                 participant_name="임수연", body="인덱스가 빠졌어요")

        with patch("apps.agent.services.briefing.build_all", return_value=2) as spy:
            r = self.post("/meetings/end", {"guild_id": GUILD, "thread_id": THREAD})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["briefings"], 2)
        self.assertEqual(r.json()["title"], "DB 스키마 리뷰")
        spy.assert_called_once()
        m.refresh_from_db()
        self.assertEqual(m.status, MeetingStatus.ENDED)

    def test_end_twice(self):
        """duplicate 응답에도 title이 있어야 한다 — 봇이 요약 임베드 제목에 쓴다."""
        self._start()
        self.post("/meetings/end", {"thread_id": THREAD})
        r = self.post("/meetings/end", {"thread_id": THREAD})
        self.assertTrue(r.json()["duplicate"])
        self.assertEqual(r.json()["title"], "DB 스키마 리뷰")

    def test_summary_failure_does_not_block_ending(self):
        """종료가 안 되면 봇이 계속 발언을 넘깁니다."""
        self._start()
        with patch("apps.agent.services.briefing.build_all",
                   side_effect=RuntimeError("펑")):
            r = self.post("/meetings/end", {"thread_id": THREAD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Meeting.objects.get().status, MeetingStatus.ENDED)


class MessageTest(Base):

    def setUp(self):
        self._start()
        self.meeting = Meeting.objects.get()

    def _send(self, **kw):
        payload = {"thread_id": THREAD, "content": "DB 스키마 언제 끝나요?",
                   "author_discord_id": "dc-mate", "author": "임수연"}
        payload.update(kw)
        with patch("apps.agent.tasks.run_agent_for_utterance.delay") as spy:
            r = self.post("/discord/messages", payload)
        return r, spy

    def test_stores_the_original_text(self):
        """요약하지 않고 원문 그대로 남깁니다."""
        r, _ = self._send()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Utterance.objects.get().body, "DB 스키마 언제 끝나요?")

    def test_wakes_the_agent(self):
        _, spy = self._send()
        spy.assert_called_once()

    def test_outside_a_meeting_is_not_stored(self):
        """회의록이 아닌 잡담이 섞이면 대리인이 엉뚱한 맥락을 근거로 삼습니다."""
        r, spy = self._send(thread_id="다른스레드")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(Utterance.objects.count(), 0)
        spy.assert_not_called()

    def test_empty_body_is_skipped(self):
        r, _ = self._send(content="   ")
        self.assertEqual(r.status_code, 202)

    def test_unlinked_speaker_is_still_recorded(self):
        """말한 사람을 몰라도 회의록에서 빠지면 안 됩니다."""
        r, _ = self._send(author_discord_id="모르는사람")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Utterance.objects.get().participant_name, "임수연")

    def test_broker_failure_keeps_the_record(self):
        """브로커가 없어도 회의록이 남는 것이 먼저입니다."""
        with patch("apps.agent.tasks.run_agent_for_utterance.delay",
                   side_effect=RuntimeError("no broker")):
            r = self.post("/discord/messages",
                          {"thread_id": THREAD, "content": "x",
                           "author_discord_id": "dc-mate"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Utterance.objects.count(), 1)


class PresenceTest(Base):

    def setUp(self):
        self._start()
        MeetingParticipant.objects.create(meeting=Meeting.objects.get(), user=self.me,
                                          user_name="서재민")

    def test_offline_marks_absent(self):
        self.post("/discord/presence", {"discord_user_id": "dc-me",
                                        "status": "offline"})
        self.assertEqual(MeetingParticipant.objects.get(user=self.me).attendance,
                         Attendance.ABSENT)

    def test_delegated_user_is_untouched(self):
        """본인이 명시적으로 정한 것을 접속 상태 같은 약한 신호로 뒤집으면 안 됩니다."""
        MeetingParticipant.objects.filter(user=self.me).update(
            delegated=True, attendance=Attendance.ABSENT)
        self.post("/discord/presence", {"discord_user_id": "dc-me",
                                        "status": "online"})
        self.assertEqual(MeetingParticipant.objects.get(user=self.me).attendance,
                         Attendance.ABSENT)

    def test_unlinked_user_is_quiet(self):
        r = self.post("/discord/presence", {"discord_user_id": "모르는사람",
                                            "status": "online"})
        self.assertEqual(r.status_code, 202)


class DeputyAskTest(Base):

    def test_asks_a_specific_deputy(self):
        """회의 발언과 달리 대상이 이미 정해져 있어 대상 판정을 건너뜁니다."""
        from apps.agent.services.react import RunOutcome
        from apps.agent.models import AgentRun

        run = AgentRun.objects.create(user=self.me)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True,
                                           text="진행 중입니다")) as spy:
            r = self.post("/deputy/ask", {"target_discord_id": "dc-me",
                                          "asker_discord_id": "dc-mate",
                                          "question": "DB 어디까지 됐어요?"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["answered"])
        self.assertEqual(spy.call_args.kwargs["principal"], self.me)

    def test_defer_returns_202(self):
        """유보는 오류가 아닙니다. 봇이 그대로 회의에 전할 수 있어야 합니다."""
        from apps.agent.services.react import RunOutcome
        from apps.agent.models import AgentRun

        run = AgentRun.objects.create(user=self.me)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=False,
                                           reason="NO_EVIDENCE",
                                           text="본인 확인이 필요합니다")):
            r = self.post("/deputy/ask", {"target_discord_id": "dc-me",
                                          "question": "x"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["reason"], "NO_EVIDENCE")

    def test_unknown_target(self):
        self.assertEqual(
            self.post("/deputy/ask", {"target_discord_id": "없음",
                                      "question": "x"}).status_code, 404)

    def test_evidence_is_included_with_korean_labels(self):
        """
        무엇을 보고 답했는지가 응답에 있어야 Discord에서 출처를 보여줄 수
        있다. source_type을 그대로 주지 않고 한국어 라벨로 바꿔서 준다 —
        화면 문자열은 서버가 완성해 내려준다는 원칙이다.
        """
        from apps.agent.services.react import RunOutcome
        from apps.agent.models import AgentRun

        run = AgentRun.objects.create(user=self.me)
        evidence = [
            {"source_type": "work", "source_id": "1", "title_snapshot": "로그인 API 구현"},
            {"source_type": "thought", "source_id": "2", "title_snapshot": "일정은 다음 주로"},
        ]
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True, text="ok",
                                           evidence=evidence)):
            r = self.post("/deputy/ask", {"target_discord_id": "dc-me",
                                          "question": "x"})
        self.assertEqual(r.json()["evidence"], [
            {"label": "작업", "title": "로그인 API 구현"},
            {"label": "생각", "title": "일정은 다음 주로"},
        ])

    def test_evidence_is_empty_list_when_no_evidence(self):
        """유보든 답변이든 근거가 없으면 빈 목록이지 필드 자체가 빠지면 안
        된다 — 봇이 매번 키 존재 여부를 따로 확인하지 않아도 되게 한다."""
        from apps.agent.services.react import RunOutcome
        from apps.agent.models import AgentRun

        run = AgentRun.objects.create(user=self.me)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=False,
                                           reason="NO_EVIDENCE", text="본인 확인이 필요합니다")):
            r = self.post("/deputy/ask", {"target_discord_id": "dc-me", "question": "x"})
        self.assertEqual(r.json()["evidence"], [])

    def test_bot_field_names_also_work(self):
        """봇이 쓰는 이름과 명세의 이름이 다릅니다. 둘 다 받습니다."""
        from apps.agent.services.react import RunOutcome
        from apps.agent.models import AgentRun

        run = AgentRun.objects.create(user=self.me)
        with patch("apps.agent.services.react.run",
                   return_value=RunOutcome(run=run, answered=True, text="ok")) as spy:
            r = self.post("/deputy/ask", {"target": "dc-me",
                                          "requester_discord_id": "dc-mate",
                                          "question": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(spy.call_args.kwargs["principal"], self.me)
        self.assertEqual(spy.call_args.kwargs["asker"], self.mate)


@override_settings(BORDO=_SETTINGS)
class EmptyValueTest(Base):
    """
    리뷰(#38)에서 나온 것. 빈 값이 "못 찾음" 이 아니라 **아무 행이나** 잡았습니다.

    미연동 사용자는 `discord_user_id=""`, 웹에서 만든 회의는
    `discord_channel_id=""` 로 저장됩니다. 여기가 뚫리면 남의 대리인이 답하고
    남의 회의가 종료됩니다.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # 계정을 연결하지 않은 사람들. 빈 문자열로 저장됩니다.
        cls.unlinked = User.objects.create_user(email="u1@bordo.dev",
                                                password="x" * 10, name="미연동1")
        User.objects.create_user(email="u2@bordo.dev", password="x" * 10,
                                 name="미연동2")
        # 웹에서 만든 회의. discord_channel_id 가 비어 있습니다.
        cls.web_meeting = Meeting.objects.create(
            project=cls.project, project_name="Bordo", title="웹 회의",
            scheduled_at=timezone.now(), created_by=cls.me,
            status=MeetingStatus.ACTIVE)

    def test_empty_discord_id_does_not_match_anyone(self):
        """비워서 부르면 아무 미연동 사용자의 대리인이 답했습니다."""
        for path in ("/delegate/on", "/delegate/off"):
            self.assertEqual(self.post(path, {"discord_user_id": ""}).status_code,
                             400, path)

    def test_deputy_ask_rejects_empty_target(self):
        r = self.post("/deputy/ask", {"target_discord_id": "", "question": "x"})
        self.assertEqual(r.status_code, 400)

    def test_meeting_end_cannot_kill_a_web_meeting(self):
        """빈 thread_id 로 다른 팀 회의가 강제 종료됐습니다."""
        r = self.post("/meetings/end", {"thread_id": ""})
        self.assertEqual(r.status_code, 400)
        self.web_meeting.refresh_from_db()
        self.assertEqual(self.web_meeting.status, MeetingStatus.ACTIVE)

    def test_messages_cannot_leak_into_a_web_meeting(self):
        """
        빈 thread_id 로 웹 회의 스레드에 발언이 새어 들어갔습니다.

        막는 방식은 400 에서 **202 로 바뀌었습니다.** 봇이 스레드가 아닌 채널의
        메시지에도 `thread_id: null` 을 보내기 때문에, 400 으로 세우면 평소 잡담
        한 줄마다 오류가 쌓입니다. 새어 들어가지 않는다는 것이 지켜야 할 것이고
        상태 코드는 그 수단입니다.
        """
        r = self.post("/discord/messages",
                      {"thread_id": "", "content": "새어 들어간 발언",
                       "author_discord_id": "dc-mate"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.data["skipped"], "not_a_thread")
        self.assertEqual(Utterance.objects.filter(meeting=self.web_meeting).count(), 0)

    def test_meeting_start_skips_blank_participants(self):
        """빈 id 를 그대로 조회해 미연동 사용자가 남의 회의에 등록됐습니다."""
        self.post("/meetings/start", {
            "guild_id": GUILD, "thread_id": "t-blank", "agenda": "x",
            "participants": [{"discord_user_id": "", "status": "delegated"},
                             {"discord_user_id": "dc-me", "status": "present"}],
        })
        m = Meeting.objects.get(discord_channel_id="t-blank")
        self.assertEqual(list(m.participants.values_list("user_id", flat=True)),
                         [self.me.id])

    def test_teams_link_rejects_empty(self):
        self.assertEqual(
            self.post("/teams/link", {"guild_id": "g", "discord_user_id": ""}
                      ).status_code, 400)


@override_settings(BORDO=_SETTINGS)
class PresenceStatusTest(Base):
    """`idle`·`dnd` 는 자리에 있는 상태입니다."""

    def setUp(self):
        self.post("/meetings/start", {"guild_id": GUILD, "thread_id": THREAD,
                                      "agenda": "x"})
        MeetingParticipant.objects.create(meeting=Meeting.objects.get(), user=self.me,
                                          user_name="서재민")

    def _status(self, s):
        self.post("/discord/presence", {"discord_user_id": "dc-me", "status": s})
        return MeetingParticipant.objects.get(user=self.me).attendance

    def test_present_states(self):
        for s in ("online", "idle", "dnd"):
            self.assertEqual(self._status(s), Attendance.PRESENT, s)

    def test_absent_states(self):
        for s in ("offline", "invisible"):
            self.assertEqual(self._status(s), Attendance.ABSENT, s)


class TokenDefaultTest(TestCase):
    """
    설정에 기본값이 살아 있으면, 배포에서 환경변수를 빠뜨렸을 때 저장소를 볼 수
    있는 사람은 누구나 봇 전용 API 를 부를 수 있습니다.
    """

    @override_settings(BORDO={**settings.BORDO, "SERVICE_TOKEN": ""})
    def test_empty_token_closes_everything(self):
        r = self.client.post("/internal/v1/delegate/on", {}, content_type="application/json",
                             HTTP_X_SERVICE_TOKEN="아무거나")
        self.assertEqual(r.status_code, 401)

    def test_no_hardcoded_default_in_settings(self):
        import os
        self.assertEqual(settings.BORDO["SERVICE_TOKEN"],
                         os.environ.get("BORDO_SERVICE_TOKEN", ""))


class OutboxTest(Base):
    """
    발송함 — 서버가 쓰고 봇이 가져갑니다.

    이 세 엔드포인트가 없어서 대리인 답변이 Discord 에 한 글자도 안 나갔습니다(#52).
    여기서 보는 것은 **같은 것을 두 번 내보내지 않는가** 하나입니다. 회의에 같은
    말이 두 번 올라가면 대리인을 믿을 수 없게 됩니다.
    """

    def _event(self, key="k1", **kw):
        from apps.agent.models import OutboxEvent
        return OutboxEvent.objects.create(
            team=self.team, idempotency_key=key,
            kind=OutboxEvent.Kind.MESSAGE, channel_id="ch-1",
            payload={"body": "진행 중입니다.", "is_agent": True}, **kw)

    def test_pending_events_are_handed_out(self):
        self._event()
        r = self.get("/outbox-events")
        self.assertEqual(r.status_code, 200)
        rows = r.json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel_id"], "ch-1")
        self.assertEqual(rows[0]["payload"]["body"], "진행 중입니다.")

    def test_the_next_poll_does_not_see_what_was_just_handed_out(self):
        """
        가져간 것은 잠시 안 보여야 합니다. 봇이 게시하는 동안(ACK 전) 다음 폴링이
        같은 행을 또 집어 가면 회의에 같은 말이 두 번 올라갑니다.

        **여기서 보는 것은 가시성 타임아웃뿐입니다.** 같은 순간 겹치는 요청을
        막는 `select_for_update` 는 SQLite 에서 조용히 무시되고 테스트 클라이언트도
        순차 실행이라, 이 테스트로는 증명되지 않습니다.
        """
        self._event()
        first = self.get("/outbox-events").json()["results"]
        second = self.get("/outbox-events").json()["results"]
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "같은 이벤트가 두 번 나갔습니다")

    def test_it_comes_back_if_the_bot_dies(self):
        """
        가시성 타임아웃이 지나면 다시 보여야 합니다. 봇이 게시 도중 죽었을 때
        영영 안 나가면 사용자는 대리인이 말한 줄 알고 기다립니다.
        """
        from datetime import timedelta

        from apps.agent.models import OutboxEvent
        from apps.discord.views import VISIBILITY_TIMEOUT_SEC

        e = self._event()
        self.get("/outbox-events")

        # 타임아웃이 지난 상황을 만듭니다.
        OutboxEvent.objects.filter(pk=e.pk).update(
            available_at=timezone.now() - timedelta(seconds=VISIBILITY_TIMEOUT_SEC + 1))
        self.assertEqual(len(self.get("/outbox-events").json()["results"]), 1)

    def test_ack_marks_it_sent(self):
        e = self._event()
        r = self.post(f"/outbox-events/{e.id}/ack")
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.status, e.Status.SENT)
        self.assertIsNotNone(e.sent_at)

    def test_ack_twice_is_safe(self):
        """
        봇이 게시하고 ACK 에서 네트워크가 끊기면 다시 시도합니다. 그때 오류를
        돌려주면 봇은 실패로 알고 **또 게시합니다.**
        """
        e = self._event()
        self.post(f"/outbox-events/{e.id}/ack")
        sent_at = e.__class__.objects.get(pk=e.pk).sent_at
        r = self.post(f"/outbox-events/{e.id}/ack")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(e.__class__.objects.get(pk=e.pk).sent_at, sent_at)

    def test_fail_backs_off_and_returns_to_pending(self):
        e = self._event()
        self.get("/outbox-events")          # 봇이 가져감 = 시도 1회
        r = self.post(f"/outbox-events/{e.id}/fail", {"error": "채널 없음"})
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.status, e.Status.PENDING)
        self.assertEqual(e.attempts, 1)
        self.assertGreater(e.available_at, timezone.now())
        self.assertIn("채널 없음", e.last_error)

    def _drain_once(self, e):
        """봇 한 바퀴: 가져가고 → 실패 보고 → 백오프를 지난 것으로 만든다."""
        from apps.agent.models import OutboxEvent
        self.get("/outbox-events")
        self.post(f"/outbox-events/{e.id}/fail", {"error": "x"})
        OutboxEvent.objects.filter(pk=e.pk).update(available_at=timezone.now())

    def test_repeated_failure_ends_in_dead(self):
        """
        무한 재시도는 같은 오류를 반복하고, 그냥 버리면 사용자는 대리인이 말한
        줄 알고 있습니다. 상한을 넘기면 사람이 보도록 DEAD 로 둡니다.
        """
        e = self._event(max_attempts=2)
        self._drain_once(e)
        self._drain_once(e)
        e.refresh_from_db()
        self.assertEqual(e.status, e.Status.DEAD)

    def test_a_bot_that_keeps_crashing_still_reaches_dead(self):
        """
        가져가기만 하고 아무 보고도 없이 죽는 봇이 가장 위험합니다.

        실패했을 때만 횟수를 세면 이 경우를 영영 못 셉니다 — 타임아웃이 지나
        다시 보이고, 또 가져가고, 또 죽고를 **무한히 반복하면서 DEAD 에는 절대
        도달하지 않습니다.** 하필 그 상황이 사람이 봐야 하는 상황입니다.
        """
        from apps.agent.models import OutboxEvent

        e = self._event(max_attempts=2)
        for _ in range(4):
            self.get("/outbox-events")      # 가져가고 아무 보고 없이 죽음
            OutboxEvent.objects.filter(pk=e.pk).update(available_at=timezone.now())

        e.refresh_from_db()
        self.assertEqual(e.status, e.Status.DEAD, "영원히 재시도되고 있습니다")
        self.assertEqual(self.get("/outbox-events").json()["results"], [])

    def test_dead_events_are_not_handed_out(self):
        from apps.agent.models import OutboxEvent
        self._event(status=OutboxEvent.Status.DEAD)
        self.assertEqual(self.get("/outbox-events").json()["results"], [])

    def test_fail_after_ack_does_not_resurrect(self):
        """
        봇이 ACK 한 뒤 늦게 도착한 fail 이 발송된 것을 되살리면, 이미 회의에
        올라간 말을 한 번 더 올립니다.
        """
        e = self._event()
        self.post(f"/outbox-events/{e.id}/ack")
        self.post(f"/outbox-events/{e.id}/fail", {"error": "늦게 온 실패"})
        e.refresh_from_db()
        self.assertEqual(e.status, e.Status.SENT)

    def test_limit_is_bounded(self):
        for i in range(5):
            self._event(key=f"k{i}")
        self.assertEqual(len(self.get("/outbox-events", {"limit": 2}).json()["results"]), 2)

    def test_service_token_is_required(self):
        self._event()
        r = self.get("/outbox-events", token=None)
        self.assertEqual(r.status_code, 401)

    def test_unknown_event_is_404_shaped(self):
        import uuid
        r = self.post(f"/outbox-events/{uuid.uuid4()}/ack")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "STATE_NOT_FOUND")


@override_settings(BORDO=_SETTINGS)
class DelegateAttendanceTest(Base):
    """
    같은 행위가 경로에 따라 다른 값이 되면 화면 뱃지가 갈립니다.

    웹은 `DELEGATED`, 봇은 `ABSENT` 를 쓰고 있었습니다. 홈 카드가 뱃지를
    `불참한 회의` 로 그리느냐 `Bordo 대리 참석 예정` 으로 그리느냐가
    어디서 눌렀느냐에 달려 있었습니다.
    """

    def setUp(self):
        self._start()
        self.meeting = Meeting.objects.get()
        MeetingParticipant.objects.create(meeting=self.meeting, user=self.me,
                                          user_name="서재민")

    def _p(self):
        return MeetingParticipant.objects.get(user=self.me, meeting=self.meeting)

    def test_confirmed_meeting_is_covered(self):
        """일정이 확정된 회의가 대부분인데 그 회의에는 아무 일도 안 하고 있었습니다."""
        Meeting.objects.filter(pk=self.meeting.pk).update(
            status=MeetingStatus.CONFIRMED)
        r = self.post("/delegate/on", {"discord_user_id": "dc-me", "scope": "전체"})
        self.assertEqual(r.json()["meetings_updated"], 1)
        self.assertEqual(self._p().attendance, Attendance.DELEGATED)

    def test_off_before_the_meeting_opens_is_pending(self):
        """열리지도 않은 회의를 PRESENT 로 두면 이미 와 있는 사람으로 그려집니다."""
        Meeting.objects.filter(pk=self.meeting.pk).update(
            status=MeetingStatus.SCHEDULED)
        self.post("/delegate/on", {"discord_user_id": "dc-me", "scope": "전체"})
        self.post("/delegate/off", {"discord_user_id": "dc-me"})
        self.assertEqual(self._p().attendance, Attendance.PENDING)

    def test_off_during_the_meeting_is_present(self):
        Meeting.objects.filter(pk=self.meeting.pk).update(status=MeetingStatus.ACTIVE)
        self.post("/delegate/on", {"discord_user_id": "dc-me", "scope": "전체"})
        self.post("/delegate/off", {"discord_user_id": "dc-me"})
        self.assertEqual(self._p().attendance, Attendance.PRESENT)

    def test_meeting_start_uses_the_same_value(self):
        r = self.post("/meetings/start", {
            "guild_id": GUILD, "thread_id": "thread-delegated", "agenda": "x",
            "participants": [{"discord_user_id": "dc-me", "status": "delegated"}]})
        self.assertEqual(r.status_code, 201, r.content)
        p = MeetingParticipant.objects.get(user=self.me,
                                           meeting__discord_channel_id="thread-delegated")
        self.assertTrue(p.delegated)
        self.assertEqual(p.attendance, Attendance.DELEGATED)
