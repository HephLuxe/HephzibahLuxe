"""
apps/core/pagination.py

Two pagination strategies, used deliberately for different shapes of data:

StandardCursorPagination
    Cursor-based. Offset pagination is unstable under concurrent inserts (a row
    added mid-scroll shifts every subsequent page); cursor pagination is
    deterministic. The two-field ordering ("-created_at", "-id") gives a stable
    tie-breaker when several rows share a created_at timestamp, which a
    single-field cursor would raise InvalidCursor on. Default choice for
    unbounded/streaming lists.

StandardPageNumberPagination
    Classic numbered pages with a total count. Used where the UI itself is
    numbered-page shaped (e.g. the Budget Payment History table: "Showing 1 to
    7 of 14 payments" + page 1/2 controls) — cursor pagination cannot answer
    "how many pages / which page am I on" without a total count, so it is the
    wrong tool for a small, bounded, per-event list like this one.

Both are opt-in per view (``pagination_class = ...`` for class-based views, or
manual ``paginate_queryset``/``get_paginated_response`` in function views).
Neither is registered as the global DEFAULT_PAGINATION_CLASS, so existing
un-paginated list responses keep their current shape until migrated deliberately.
"""

from rest_framework.pagination import CursorPagination, PageNumberPagination


class StandardCursorPagination(CursorPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-created_at", "-id")


class StandardPageNumberPagination(PageNumberPagination):
    page_size = 7  # matches the Figma Payment History spec ("1 to 7 of 14")
    page_size_query_param = "page_size"
    max_page_size = 50
    page_query_param = "page"
