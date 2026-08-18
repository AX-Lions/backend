"""
오류 코드 카탈로그.

클라이언트는 HTTP 상태가 아니라 `error.code` 로 분기합니다.
`retryable: False` 인 코드는 재시도해도 같은 결과입니다.
"""

# code -> (http_status, 기본 메시지, retryable)
ERROR_CODES = {
    # 공통
    "VALIDATION_ERROR":              (400, "요청 본문이 올바르지 않습니다.", False),
    "DUPLICATE_EVENT":               (409, "이미 처리된 요청입니다.", False),
    # 인증
    "AUTH_INVALID_TOKEN":            (401, "유효하지 않은 토큰입니다.", False),
    "AUTH_TOKEN_EXPIRED":            (401, "액세스 토큰이 만료되었습니다.", True),
    "AUTH_INVALID_CREDENTIALS":      (401, "이메일 또는 비밀번호가 올바르지 않습니다.", False),
    "AUTH_EMAIL_DUPLICATED":         (409, "이미 사용 중인 이메일입니다.", False),
    "AUTH_ACCOUNT_INACTIVE":         (401, "비활성화된 계정입니다.", False),
    "AUTH_SERVICE_TOKEN_INVALID":    (401, "서비스 토큰이 올바르지 않습니다.", False),
    # 팀 / 프로젝트
    "TEAM_ACCESS_DENIED":            (403, "해당 팀의 멤버가 아닙니다.", False),
    "TEAM_INVITE_INVALID":           (400, "유효하지 않은 초대 코드입니다.", False),
    "TEAM_INVITE_EXPIRED":           (410, "만료된 초대 코드입니다.", False),
    "TEAM_ALREADY_MEMBER":           (409, "이미 팀에 속해 있습니다.", False),
    "PROJECT_ACCESS_DENIED":         (403, "해당 프로젝트의 참여자가 아닙니다.", False),
    "PROJECT_NOT_FOUND":             (404, "프로젝트를 찾을 수 없습니다.", False),
    # Discord 연동 — 봇이 discord_user_id·guild_id 로 물어보는데 이어진 것이 없을 때
    "USER_NOT_FOUND":                (404, "연결된 계정을 찾을 수 없습니다.", False),
    "TEAM_NOT_FOUND":                (404, "연결된 팀을 찾을 수 없습니다.", False),
    "TEAM_AMBIGUOUS":                (409, "연결할 팀을 골라 주십시오.", False),
    # Discord 연동 (웹) — 연결 코드 입력
    "DISCORD_CODE_INVALID":          (400, "연결 코드가 올바르지 않습니다. DM 으로 받은 6자리를 확인하십시오.", False),
    "DISCORD_CODE_EXPIRED":          (410, "연결 코드가 만료됐습니다. Discord 에서 /bordo-connect 를 다시 실행하십시오.", False),
    "DISCORD_CODE_ALREADY_USED":     (409, "이미 사용된 연결 코드입니다.", False),
    "DISCORD_NOT_LINKED":            (404, "연결된 Discord 서버가 없습니다.", False),
    # 문서 / 상태
    "DOCUMENT_NOT_FOUND":            (404, "문서를 찾을 수 없습니다.", False),
    "DOCUMENT_ACCESS_DENIED":        (403, "문서에 접근할 수 없습니다.", False),
    "STATE_NOT_FOUND":               (404, "대상을 찾을 수 없습니다.", False),
    # 정책 / 대리인
    "POLICY_DENIED":                 (403, "정책에 의해 거부되었습니다.", False),
    "POLICY_HUMAN_APPROVAL_REQUIRED":(409, "사용자 승인이 필요합니다.", False),
    "AGENT_MAX_HOPS_EXCEEDED":       (409, "대리인 간 전달 한도를 초과했습니다.", False),
    "AGENT_RUN_FAILED":              (500, "대리인 실행에 실패했습니다.", True),
    # 회의
    "MEETING_NOT_FOUND":             (404, "회의를 찾을 수 없습니다.", False),
    "MEETING_NOT_ACTIVE":            (409, "진행 중인 회의가 아닙니다.", False),
    "MEETING_ALREADY_STARTED":       (409, "이미 시작된 회의입니다.", False),
    "MEETING_ALREADY_ENDED":         (409, "이미 종료된 회의입니다.", False),
    "MEETING_LOCKED":                (409, "진행 중이거나 종료된 회의는 수정할 수 없습니다.", False),
    # 태스크 / 승인
    "APPROVAL_REQUIRED":             (409, "먼저 처리해야 할 것이 있습니다.", False),
    "REFERENCED_BY_OTHERS":          (409, "다른 자원이 참조하고 있어 삭제할 수 없습니다.", False),
    # 채팅
    "CHAT_ROOM_NOT_FOUND":           (404, "채팅방을 찾을 수 없습니다.", False),
    "CHAT_EDIT_WINDOW_EXPIRED":      (409, "수정 가능 시간이 지났습니다.", False),
    "CHAT_ROOM_TYPE_NOT_ALLOWED":    (409, "이 채팅방 종류에서는 할 수 없는 동작입니다.", False),
    # Discord / 연동
    "DISCORD_DELIVERY_FAILED":       (502, "Discord 전달에 실패했습니다.", True),
    "CHANNEL_ACCESS_DENIED":         (403, "채널 권한이 없습니다.", False),
    "SKILL_NOT_FOUND":               (404, "스킬을 찾을 수 없습니다.", False),
    # 인프라
    "MODEL_RATE_LIMIT":              (429, "모델 호출 한도를 초과했습니다.", True),
    "EVENT_BROKER_FAILED":           (500, "이벤트 브로커에 연결하지 못했습니다.", True),
}


class BordoError(Exception):
    """서비스 전역 예외. 뷰에서 이걸 raise 하면 공통 오류 형식으로 나갑니다."""

    def __init__(self, code, message=None, details=None, status=None):
        if code not in ERROR_CODES:
            raise KeyError(f"정의되지 않은 오류 코드: {code}")
        self.code = code
        default_status, default_message, retryable = ERROR_CODES[code]
        self.status = status or default_status
        self.message = message or default_message
        self.retryable = retryable
        self.details = details
        super().__init__(self.message)
