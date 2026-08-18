"""
마이그레이션이 **데이터를 옳게 옮기는가**.

스키마는 `makemigrations --check` 가 봐 주지만 `RunPython` 안의 조건식은
아무도 안 봅니다. 공개 설정을 셋으로 쪼갤 때 조건을 **새 칸**으로 걸어 두어
백필이 통째로 무동작이 된 적이 있습니다 — 공개를 꺼 뒀던 사람이 전부 켜진 채로
남고, 다음 줄의 `RemoveField` 가 되돌릴 근거까지 지웠습니다.

프라이버시가 조용히 열리는 방향으로 뒤집히는 사고라, 이 한 건만은 실제로
마이그레이션을 돌려서 확인합니다.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = ("agent", "0006_agentsettings_agent_name")
AFTER = ("agent", "0007_agentsettings_disclose_split")


class DiscloseSplitMigrationTest(TransactionTestCase):
    """`TransactionTestCase` 인 이유 — 마이그레이션이 스키마를 바꿉니다."""

    available_apps = None

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    def tearDown(self):
        # 다음 테스트가 최신 스키마를 보게 되돌립니다.
        self._migrate(AFTER)

    def _make_user(self, apps, email):
        User = apps.get_model("accounts", "User")
        return User.objects.create(email=email, name="테스터", password="x")

    def test_old_value_is_copied_into_the_three(self):
        old_apps = self._migrate(BEFORE)
        Settings = old_apps.get_model("agent", "AgentSettings")
        off = self._make_user(old_apps, "off@bordo.dev")
        on = self._make_user(old_apps, "on@bordo.dev")
        Settings.objects.create(user_id=off.pk, disclose_work_plan_thought=False)
        Settings.objects.create(user_id=on.pk, disclose_work_plan_thought=True)

        new_apps = self._migrate(AFTER)
        Settings = new_apps.get_model("agent", "AgentSettings")

        closed = Settings.objects.get(user_id=off.pk)
        self.assertEqual(
            (closed.disclose_work, closed.disclose_plan, closed.disclose_thought),
            (False, False, False),
            "공개를 꺼 뒀던 사람이 마이그레이션 한 번으로 전부 열립니다")

        opened = Settings.objects.get(user_id=on.pk)
        self.assertEqual(
            (opened.disclose_work, opened.disclose_plan, opened.disclose_thought),
            (True, True, True))

    def test_reverse_merges_back(self):
        self._migrate(BEFORE)
        new_apps = self._migrate(AFTER)
        Settings = new_apps.get_model("agent", "AgentSettings")
        user = self._make_user(new_apps, "back@bordo.dev")
        Settings.objects.create(user_id=user.pk, disclose_work=False,
                                disclose_plan=True, disclose_thought=False)

        old_apps = self._migrate(BEFORE)
        Settings = old_apps.get_model("agent", "AgentSettings")
        # 하나라도 켜져 있었으면 옛 한 칸은 켜짐입니다.
        self.assertTrue(Settings.objects.get(user_id=user.pk).disclose_work_plan_thought)
