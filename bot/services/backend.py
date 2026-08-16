import asyncio
import logging
import aiohttp

log = logging.getLogger("bordo")


class BackendClient:
    def __init__(self, base_url: str, service_token: str, timeout: float = 5.0, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Service-Token": service_token}
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        last_exc = None

        for attempt in range(self.max_retries + 1):
            try:
                session = await self._get_session()
                async with session.request(method, url, **kwargs) as resp:
                    if resp.status == 409:
                        # DUPLICATE_EVENT는 성공 처리하고 재게시하지 않는다 (12장)
                        return {"status": "duplicate"}

                    if 400 <= resp.status < 500:
                        # 클라이언트 오류는 몇 번을 다시 보내도 같은 결과다. 재시도 대신
                        # 본문(error.code)을 그대로 돌려줘서 호출부가 분기할 수 있게 한다.
                        if resp.content_type == "application/json":
                            return await resp.json()
                        return {"error": {"code": "UNKNOWN", "message": await resp.text()}}

                    resp.raise_for_status()

                    if resp.content_type == "application/json":
                        return await resp.json()

                    return await resp.text()

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                log.warning("Backend 호출 실패(%s/%s): %s %s", attempt + 1, self.max_retries + 1, path, exc)
                await asyncio.sleep(0.5 * (attempt + 1))

        log.error("Backend 호출 최종 실패: %s (%s)", path, last_exc)
        return None

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


def get_error(result) -> dict | None:
    """result가 4xx 오류 응답(error.code 포함)이면 그 error 딕셔너리를,
    아니면 None을 돌려준다.

    result가 dict가 아닌 경우(2xx인데 JSON이 아닌 응답이 오면 request()가
    문자열을 그대로 돌려준다)도 안전하게 처리한다 — 그런 문자열에
    .get()을 부르면 AttributeError로 죽는다.
    """
    if isinstance(result, dict):
        return result.get("error")
    return None