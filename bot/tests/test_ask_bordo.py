"""
/ask-bordo 목업 테스트.

봇 실행 환경(실제 Discord 토큰) 없이 discord.Interaction·BackendClient·GateService를
전부 mock으로 대체해 로직만 검증한다. 실행:

    cd bot && python -m unittest tests.test_ask_bordo -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from cogs.bordo import BordoCog, _MESSAGE_LIMIT


def make_interaction():
    interaction = MagicMock()
    interaction.guild_id = 999
    interaction.channel_id = 555
    interaction.user.id = 111
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    target = MagicMock()
    target.id = 222
    target.display_name = "유수인"

    return interaction, target


def make_cog(backend_post_return, *, gate_ok=True):
    backend = MagicMock()
    backend.post = AsyncMock(return_value=backend_post_return)
    gate = MagicMock()
    gate.require = AsyncMock(return_value=gate_ok)
    return BordoCog(MagicMock(), backend, gate), backend, gate


class AskBordoTests(unittest.IsolatedAsyncioTestCase):

    async def test_success_edits_same_response_twice_no_duplicate_message(self):
        # defer()가 이미 공개라 Discord 로딩 메시지 자체를 채워야 한다. 새
        # 메시지를 보내면(followup.send) 로딩 표시가 두 개 남는 것이 리뷰에서
        # 지적된 버그였다 — 여기서는 edit_original_response만 쓰는지 확인한다.
        interaction, target = make_interaction()
        cog, backend, _ = make_cog({"body": "결제 API 붙이는 중이에요."})

        await cog.ask_bordo.callback(cog, interaction, target, "진행 상황 어때?")

        interaction.followup.send.assert_not_called()
        self.assertEqual(interaction.edit_original_response.call_count, 2)
        interaction.edit_original_response.assert_any_call(
            content="🤔 **유수인의 Bordo**가 생각 중입니다...")
        interaction.edit_original_response.assert_any_call(
            content="🤖 **유수인의 Bordo**: 결제 API 붙이는 중이에요.")

    async def test_backend_none_finishes_with_failure_text(self):
        interaction, target = make_interaction()
        cog, backend, _ = make_cog(None)

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        interaction.edit_original_response.assert_called_with(
            content="⚠️ 답변을 받아오지 못했습니다. 잠시 후 다시 시도해주세요.")

    async def test_error_dict_finishes_with_error_message(self):
        interaction, target = make_interaction()
        cog, backend, _ = make_cog({
            "error": {"code": "AGENT_DISABLED", "message": "이 사람은 대리인을 꺼 두었습니다."}
        })

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        interaction.edit_original_response.assert_called_with(
            content="이 사람은 대리인을 꺼 두었습니다.")

    async def test_empty_body_falls_back_to_default_message(self):
        # react.run()이 내부 실패(_fail())로 끝나면 answered=False에 body=""가
        # 그대로 온다 — 조용히 빈 답을 보여주지 않고 기본 실패 문구로 대체돼야 한다.
        interaction, target = make_interaction()
        cog, backend, _ = make_cog({"run_id": "abc", "answered": False, "reason": "", "body": ""})

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        interaction.edit_original_response.assert_called_with(
            content="🤖 **유수인의 Bordo**: 답변을 받아오지 못했습니다.")

    async def test_gate_fail_no_placeholder_no_backend_call(self):
        interaction, target = make_interaction()
        cog, backend, gate = make_cog(None, gate_ok=False)

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        backend.post.assert_not_called()
        interaction.edit_original_response.assert_not_called()

    async def test_long_answer_is_truncated_to_message_limit(self):
        interaction, target = make_interaction()
        long_body = "가" * 2500
        cog, backend, _ = make_cog({"body": long_body})

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        final_call = interaction.edit_original_response.call_args_list[-1]
        content = final_call.kwargs["content"]
        self.assertLessEqual(len(content), _MESSAGE_LIMIT)
        self.assertTrue(content.endswith("…"))

    async def test_edit_failure_does_not_leave_thinking_message_stuck(self):
        # edit_original_response가 실패해도(길이 초과 외의 사유 포함) "생각
        # 중..."인 채로 방치되면 안 된다 — 마지막에 최소한 실패 안내로 덮여야 한다.
        interaction, target = make_interaction()
        fake_response = MagicMock()
        fake_response.status = 400
        fake_response.reason = "Bad Request"
        http_error = discord.HTTPException(fake_response, "예시 오류")

        # 첫 호출("생각 중..." placeholder)은 성공, 두 번째(최종 답)만 실패시킨다.
        interaction.edit_original_response = AsyncMock(side_effect=[None, http_error, None])
        cog, backend, _ = make_cog({"body": "정상 답변"})

        await cog.ask_bordo.callback(cog, interaction, target, "질문")

        self.assertEqual(interaction.edit_original_response.call_count, 3)
        last_content = interaction.edit_original_response.call_args_list[-1].kwargs["content"]
        self.assertNotIn("생각 중", last_content)


if __name__ == "__main__":
    unittest.main()
