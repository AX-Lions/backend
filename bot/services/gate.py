"""
서버(길드)·개인 연결 상태 확인.

길드가 Bordo 팀에 안 묶여 있거나(`GuildLink` 없음) 개인이 계정을 안
연결했으면(`User.discord_user_id` 없음) 명령을 막는다. 판정은 항상
Backend가 한다 — 여기서는 `GET /internal/v1/teams/current` 응답을
그대로 캐싱만 한다(봇은 판단하지 않는다).
"""
import asyncio
import collections
import logging
import time

import discord

log = logging.getLogger("bordo")

#: 연결된 상태는 잘 안 바뀐다. 매 상호작용마다 물을 필요는 없다.
_POSITIVE_TTL_SEC = 300  # 5분
#: 미연결 상태는 훨씬 짧게 캐싱한다. 방금 막힌 사람이 바로 /bordo-connect나
#: /bordo-team-connect로 고치러 갈 가능성이 높은데, 개인 연결은 웹에서
#: 끝나서 봇이 그 순간을 알 방법이 없다(길드 연결은 invalidate_guild로
#: 바로 지우지만, 개인 쪽은 이 짧은 TTL이 유일한 안전장치다).
_NEGATIVE_TTL_SEC = 30

#: 네트워크 실패·예상 못 한 에러로 "일단 통과"시킨 결과도 아주 짧게는
#: 캐싱한다. 안 그러면 Backend 장애 중에 메시지·명령마다 재시도를 처음부터
#: 새로 돌려, 이미 흔들리는 Backend를 더 두들긴다. 5초면 장애 복구 반영도
#: 충분히 빠르다.
_FAILOPEN_TTL_SEC = 5

#: 연결된 개인에게 부여하는 역할. 명령 숨기기는 Discord 관리자가 Integrations에서
#: 이 역할 기준으로 설정해야 한다(봇 코드로는 커맨드 권한을 못 건드림) — 여기서는
#: "누가 연결됐는지" 표시만 한다.
CONNECTED_ROLE_NAME = "Bordo 연결됨"


class GateService:
    def __init__(self, backend):
        self.backend = backend
        # guild_id -> (만료 시각, 연결 여부)
        self._guild_cache: dict[int, tuple[float, bool]] = {}
        # discord_user_id -> (만료 시각, 연결 여부)
        self._user_cache: dict[int, tuple[float, bool]] = {}
        # 길드마다 "Bordo 연결됨" 역할을 확인·생성하는 구간을 잠근다. 락이
        # 없으면 같은 길드에서 두 사람이 거의 동시에 처음 연결을 확인할 때
        # 둘 다 role is None을 보고 둘 다 create_role을 불러 역할이 두 개
        # 생길 수 있다. 길드별로 따로 둬서, 서로 무관한 길드의 첫 연결이
        # 같은 락을 놓고 기다리지 않게 한다.
        self._role_locks: dict[int, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

    async def _cached_check(self, cache: dict, key, params: dict, *, not_found_code: str) -> bool:
        now = time.monotonic()
        cached = cache.get(key)
        if cached and now < cached[0]:
            return cached[1]

        result = await self.backend.get("/internal/v1/teams/current", params=params)

        if result is None:
            # 네트워크 실패 등 판단 자체를 못 한 경우다. "안 됨"으로 캐싱하면
            # Backend가 잠깐 흔들렸을 뿐인데 최대 5분간 모든 서버·개인이
            # 막힌다 — Backend가 없어도 봇은 계속 동작해야 한다는 원칙과
            # 반대다. "통과"로 아주 짧게만 캐싱한다 — 장애 중 반복 호출이
            # 재시도를 새로 돌려 Backend를 더 두들기는 걸 막는다.
            cache[key] = (now + _FAILOPEN_TTL_SEC, True)
            return True

        error = result.get("error") if isinstance(result, dict) else None
        if error is not None:
            if error.get("code") == not_found_code:
                # 이것만 진짜 "미연결"이다 — 캐싱해도 된다. guild_id 조회는
                # 링크가 없으면 TEAM_NOT_FOUND, discord_user_id 조회는 계정
                # 자체가 없으면 USER_NOT_FOUND로 온다(계정은 있는데 소속
                # 팀만 없는 경우는 에러 없이 linked:false로 성공 응답이라
                # 여기 안 걸린다 — 그건 "팀 없음"이지 "계정 미연결"이 아니다).
                # 짧게 캐싱한다 — 막힌 사람이 바로 연결하러 갈 수 있다.
                cache[key] = (now + _NEGATIVE_TTL_SEC, False)
                return False
            # 그 외 에러(서비스 토큰 오류 등 예상 못 한 4xx)는 "미연결"이
            # 아니라 판단을 못 한 것이다. 길게 캐싱하면 설정 하나 잘못됐을 때
            # 오래 모든 서버·개인이 막힌다 — 위와 같은 이유로 짧게만 캐싱한다.
            cache[key] = (now + _FAILOPEN_TTL_SEC, True)
            return True

        cache[key] = (now + _POSITIVE_TTL_SEC, True)
        return True

    async def guild_linked(self, guild_id: int) -> bool:
        return await self._cached_check(
            self._guild_cache, guild_id, {"guild_id": str(guild_id)},
            not_found_code="TEAM_NOT_FOUND")

    def invalidate_guild(self, guild_id: int) -> None:
        """`/bordo-team-connect` 성공 직후처럼, 연결됐다는 걸 이미 아는
        순간에 부른다. 안 부르면 방금 연결한 서버가 캐시 TTL(5분) 동안
        계속 "미연결"로 막힌다."""
        self._guild_cache.pop(guild_id, None)

    async def user_linked(self, discord_user_id: int) -> bool:
        return await self._cached_check(
            self._user_cache, discord_user_id, {"discord_user_id": str(discord_user_id)},
            not_found_code="USER_NOT_FOUND")

    async def _grant_role(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            async with self._role_locks[guild.id]:
                role = discord.utils.get(guild.roles, name=CONNECTED_ROLE_NAME)
                if role is None:
                    role = await guild.create_role(
                        name=CONNECTED_ROLE_NAME,
                        reason="Bordo 계정 연결 표시용 역할 자동 생성",
                    )
            if role not in member.roles:
                await member.add_roles(role, reason="Bordo 계정 연결됨")
        except discord.Forbidden:
            # 역할 관리 권한이 없어도 게이트 판정 자체는 이미 끝났다 — 표시만
            # 못 하는 것이니 명령 실행을 막을 이유는 아니다.
            log.warning("길드(%s)에 역할 부여 권한이 없습니다.", guild.id)
        except Exception:                                          # noqa: BLE001
            # discord.HTTPException뿐 아니라 네트워크 타임아웃 등도 여기서
            # 잡는다. 넓게 잡는 이유 — 이건 "누가 연결됐는지 표시"용 부가
            # 기능이다. 여기서 난 예외가 새 나가면 delegate-on/off·ask-bordo
            # 같은 진짜 명령 실행 자체가 죽는다.
            log.exception("역할 부여 실패 guild=%s user=%s", guild.id, member.id)
