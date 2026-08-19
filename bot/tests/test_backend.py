"""
BackendClient.request()의 max_retries 오버라이드 목업 테스트 (#132).

실제 aiohttp 세션을 열지 않고 session.request()가 항상 실패하도록 흉내 내
재시도 횟수만 센다. 실행:

    cd bot && python -m unittest tests.test_backend -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

from services.backend import BackendClient


class _RaisingRequestCtx:
    """session.request(...)가 돌려주는 async context manager 흉내 - 실제
    aiohttp는 연결·응답 단계에서 실패하면 __aenter__에서 예외를 던진다."""

    async def __aenter__(self):
        raise aiohttp.ClientError("연결 실패(mock)")

    async def __aexit__(self, *exc_info):
        return False


def _client_with_failing_session(max_retries=2):
    client = BackendClient("http://backend.test", "tok", max_retries=max_retries)
    session = MagicMock()
    session.request = MagicMock(return_value=_RaisingRequestCtx())
    client._get_session = AsyncMock(return_value=session)
    return client, session


class RequestRetryTests(unittest.IsolatedAsyncioTestCase):

    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_default_uses_instance_max_retries(self, _sleep):
        client, session = _client_with_failing_session(max_retries=2)

        result = await client.post("/x")

        self.assertIsNone(result)
        self.assertEqual(session.request.call_count, 3)  # max_retries(2) + 1

    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_max_retries_override_skips_retry_loop(self, _sleep):
        # LLM을 부르는 엔드포인트는 재시도가 오히려 해롭다 - 실패해서가 아니라
        # 응답이 느려서 다시 보내는 거라, 이미 진행 중인 실행을 그대로 중복
        # 실행시킨다. max_retries=0이면 실패해도 딱 한 번만 시도해야 한다.
        client, session = _client_with_failing_session(max_retries=2)

        result = await client.post("/deputy/ask", max_retries=0)

        self.assertIsNone(result)
        self.assertEqual(session.request.call_count, 1)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_max_retries_override_does_not_change_instance_default(self, _sleep):
        # 이 호출 하나만 재시도를 끄는 것이지, 인스턴스 기본값(다른 모든
        # post()/get() 호출)이 함께 바뀌면 안 된다.
        client, session = _client_with_failing_session(max_retries=2)

        await client.post("/deputy/ask", max_retries=0)
        session.request.reset_mock()
        await client.post("/other")

        self.assertEqual(session.request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
