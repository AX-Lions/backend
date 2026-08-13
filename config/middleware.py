import uuid


class RequestIdMiddleware:
    """모든 응답에 추적용 request_id 를 실어 보냅니다."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-Id"] = request.request_id
        return response
