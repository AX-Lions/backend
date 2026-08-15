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
        self.assertEqual(p.attendance, Attendance.ABSENT)

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

    def test_legacy_path_still_works(self):
        """봇이 옮겨 오기 전까지 옛 경로도 받습니다."""
        r = self.post("/meetings", {"guild_id": GUILD, "thread_id": "t-2",
                                    "agenda": "x"})
        self.assertEqual(r.status_code, 201)

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
        spy.assert_called_once()
        m.refresh_from_db()
        self.assertEqual(m.status, MeetingStatus.ENDED)

    def test_end_twice(self):
        self._start()
        self.post("/meetings/end", {"thread_id": THREAD})
        r = self.post("/meetings/end", {"thread_id": THREAD})
        self.assertTrue(r.json()["duplicate"])

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
        """빈 thread_id 로 웹 회의 스레드에 발언이 새어 들어갔습니다."""
        r = self.post("/discord/messages",
                      {"thread_id": "", "content": "새어 들어간 발언",
                       "author_discord_id": "dc-mate"})
        self.assertEqual(r.status_code, 400)
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
