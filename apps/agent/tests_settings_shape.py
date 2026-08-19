"""
대리인 설정 GET · PATCH 의 응답 모양 (이슈 #73).

전에는 PATCH 만 `{settings, ...}` 로 감쌌습니다. 화면이 그 응답을 그대로
상태에 담으면 설정 키가 전부 `undefined` 가 되어 스위치 여섯 개가 한꺼번에
꺼진 것처럼 보였습니다. 서버 값은 멀쩡한데 화면만 거짓말하는 상태라
눈으로는 못 찾습니다.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent.models import AgentSettings

PATH = "/api/v1/me/agent/settings"

#: 화면 스위치와 1:1 인 키들. 하나라도 빠지면 그 스위치가 꺼진 채로 보입니다.
SWITCHES = ("mention_feasibility", "allow_schedule_change",
            "allow_midmeeting_question",
            "disclose_work", "disclose_plan", "disclose_thought")


class SettingsShapeTest(TestCase):

    def setUp(self):
        self.me = User.objects.create_user(email="s@bordo.dev", password="x" * 10,
                                           name="서재민")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def get(self):
        r = self.client.get(PATH)
        self.assertEqual(r.status_code, 200)
        return r.data

    def patch(self, body):
        r = self.client.patch(PATH, body, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def test_both_methods_return_the_same_keys(self):
        """모양이 갈리면 한쪽을 그대로 담은 화면이 조용히 비어 있습니다."""
        got = self.get()
        saved = self.patch({"tone": "FRIENDLY"})
        self.assertTrue(set(got) <= set(saved))

    def test_patch_puts_switches_at_the_top_level(self):
        saved = self.patch({"mention_feasibility": False})
        for key in SWITCHES:
            self.assertIn(key, saved, f"{key} 가 응답에 없습니다")
        self.assertFalse(saved["mention_feasibility"])

    def test_patch_is_not_wrapped_any_more(self):
        self.assertNotIn("settings", self.patch({"tone": "CONCISE"}))

    def test_history_material_is_kept(self):
        """설정 이력 화면이 무엇이 언제 바뀌었는지를 이 값으로 그립니다."""
        saved = self.patch({"tone": "FRIENDLY"})
        self.assertEqual(saved["changed"]["tone"], {"from": "FORMAL", "to": "FRIENDLY"})
        self.assertEqual(saved["previous_version"], 1)
        self.assertEqual(saved["active_version"], 2)

    def test_no_change_keeps_the_version(self):
        """바뀐 게 없는데 버전이 오르면 판정 이력이 의미 없이 불어납니다."""
        saved = self.patch({"tone": "FORMAL"})
        self.assertEqual(saved["changed"], {})
        self.assertEqual(saved["active_version"], 1)

    def test_name_and_tone_come_back(self):
        saved = self.patch({"agent_name": "제로", "tone": "CONCISE"})
        self.assertEqual(saved["agent_name"], "제로")
        self.assertEqual(saved["tone"], "CONCISE")
        self.assertEqual(AgentSettings.objects.get(user=self.me).agent_name, "제로")
