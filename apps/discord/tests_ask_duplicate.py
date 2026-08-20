"""
같은 질문이 겹쳐 들어올 때 (이슈 #132).

`react.run()` 은 LLM 을 최대 여섯 번 부릅니다. 한 번이 20초까지 걸리므로 이
요청은 길면 2분을 씁니다. 그동안 사용자가 답이 안 온다고 한 번 더 치면 같은
실행이 둘 겹치고, 토큰도 `AgentRun` 기록도 두 배가 됩니다. 그중 하나는 아무도
안 읽습니다.
"""
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.agent.models import AgentRun

TOKEN = "test-only-token"
_SETTINGS = {**settings.BORDO, "SERVICE_TOKEN": TOKEN}


@override_settings(BORDO=_SETTINGS)
class AskDuplicateTest(TestCase):

    def setUp(self):
        cache.clear()
        self.target = User.objects.create_user(email="t@bordo.dev", password="x" * 10,
                                               name="유수인", discord_user_id="111")
        self.asker = User.objects.create_user(email="a@bordo.dev", password="x" * 10,
                                              name="최비성", discord_user_id="222")

    def ask(self, question="배포 언제 되나요?"):
        return self.client.post(
            "/internal/v1/deputy/ask",
            {"requester_discord_id": "222", "target_discord_id": "111",
             "question": question},
            content_type="application/json", HTTP_X_SERVICE_TOKEN=TOKEN)

    def fake_run(self, **kwargs):
        run = AgentRun.objects.create(user=self.target)

        class Outcome:
            def __init__(self, run):
                self.run, self.answered, self.reason, self.text = run, True, "", "네"
                self.evidence = []
        return Outcome(run)

    def test_the_second_one_is_refused_while_the_first_runs(self):
        """두 번째가 통과하면 같은 실행이 둘 겹치고 아무도 안 읽는 답이 하나 남습니다."""
        started = []

        def slow(**kwargs):
            started.append(1)
            # 첫 실행이 도는 중에 두 번째가 들어온 상황을 그대로 만듭니다.
            second = self.ask()
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["error"]["code"], "DUPLICATE_EVENT")
            return self.fake_run(**kwargs)

        with patch("apps.agent.services.react.run", side_effect=slow):
            r = self.ask()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(started), 1)
        self.assertEqual(AgentRun.objects.count(), 1)

    def test_asking_again_after_it_finished_works(self):
        """끝나면 바로 풉니다. TTL 만 믿으면 답을 받은 사람이 이어서 못 묻습니다."""
        with patch("apps.agent.services.react.run", side_effect=self.fake_run):
            self.assertEqual(self.ask().status_code, 200)
            self.assertEqual(self.ask().status_code, 200)

    def test_a_different_question_is_not_blocked(self):
        """
        같은 사람에게 **다른** 것을 잇달아 묻는 것은 막을 이유가 없습니다.

        질문 본문을 자물쇠에서 빼면 대화가 이어지는 자리에서 두 번째 질문이
        통째로 거절됩니다.
        """
        from apps.discord.views import _ask_lock_key

        # 첫 질문이 도는 중인 상태를 자물쇠만으로 만듭니다. 실행을 겹쳐
        # 돌리면 그 안에서 또 요청이 나가 무엇이 무엇을 막았는지 흐려집니다.
        cache.add(_ask_lock_key(self.target, "222", "배포 언제 되나요?"), "1", 60)

        with patch("apps.agent.services.react.run", side_effect=self.fake_run):
            self.assertEqual(self.ask(question="회의는 몇 시인가요?").status_code, 200)
            self.assertEqual(self.ask().status_code, 409)

    def test_the_lock_is_released_even_if_the_run_blows_up(self):
        """터진 뒤에도 잠겨 있으면 그 질문은 자물쇠가 풀릴 때까지 영영 막힙니다."""
        with patch("apps.agent.services.react.run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.ask()
        with patch("apps.agent.services.react.run", side_effect=self.fake_run):
            self.assertEqual(self.ask().status_code, 200)
