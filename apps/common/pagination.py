"""커서 페이징. 오프셋은 새 행이 들어오면 경계가 밀려 중복·누락이 생깁니다."""
from django.conf import settings


def cursor_page(queryset, before=None, limit=None, order_field="-created_at",
                cursor_field="id"):
    limit = int(limit or settings.BORDO["DEFAULT_PAGE_SIZE"])
    limit = max(1, min(limit, 200))
    qs = queryset.order_by(order_field)
    if before:
        anchor = queryset.model.objects.filter(pk=before).first()
        if anchor:
            field = order_field.lstrip("-")
            value = getattr(anchor, field)
            op = "lt" if order_field.startswith("-") else "gt"
            qs = qs.filter(**{f"{field}__{op}": value})
    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, (str(getattr(rows[-1], cursor_field)) if has_more and rows else None)
