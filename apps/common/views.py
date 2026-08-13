"""응답 헬퍼."""
from rest_framework.response import Response


def ok(data, status=200, headers=None):
    return Response(data, status=status, headers=headers)


def listing(results, count=None, extra=None):
    body = {"count": count if count is not None else len(results), "results": results}
    if extra:
        body.update(extra)
    return body
