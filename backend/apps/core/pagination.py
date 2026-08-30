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

InquiryPageNumberPagination
    The same numbered-page envelope, sized for the staff lead inbox. A subclass
    rather than a second page_size on the one above, because those two page sizes
    answer to different things: 7 is pinned to a Figma table, and the lead list
    should be re-sized without moving it.

UserPageNumberPagination
    The same envelope again, sized for the staff user directory. Same reasoning
    as the inquiry inbox: a different table, so a different page size, without
    dragging the Figma-pinned 7 around behind it.

None is registered as the global DEFAULT_PAGINATION_CLASS, so a list response
keeps its current shape until it is migrated deliberately.

Which lists paginate UNCONDITIONALLY, and why those
---------------------------------------------------
``GET /inquiries/``, ``GET /users/``, ``GET /event/all`` and
``GET /event/event_day/all``.

The rule is not "big lists": it is **any list a staff token can point at the
whole table with**. Rate limiting bounds how many requests a caller makes, not
how much each one hands over, so an unbounded list needs exactly one request to
give up everything it can see. Where an endpoint is scoped to one portal or one
engagement, the scope is already the bound and opt-in pagination is fine — which
is why the portal-shaped lists (contacts, meetings, reminders, documents,
conversations) are deliberately NOT in that set.
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


class InquiryPageNumberPagination(StandardPageNumberPagination):
    """
    The staff lead inbox (``GET /inquiries/``).

    Same envelope and the same query params as the rest of the portal — it
    subclasses the standard page-number paginator rather than introducing a
    third strategy — so a caller reading ``{count, next, previous, results}``
    handles this list exactly like Payment History.

    Only ``page_size`` differs. It is a subclass instead of an argument because
    the parent's 7 is pinned to a Figma spec ("Showing 1 to 7 of 14 payments")
    and the lead inbox needs to be re-sized without dragging that table with it.

    This list is paginated unconditionally, unlike most in the project: a lead
    row carries a name, email, phone number and budget, and a rate limit bounds
    how many requests a caller makes rather than how much each one returns — so
    the page is what bounds the exposure.
    """

    page_size = 10


class UserPageNumberPagination(StandardPageNumberPagination):
    """
    The staff user directory (``GET /users/``).

    Paginated unconditionally, for the same reason as the lead inbox: one
    request used to return every account on the platform — email, name, role,
    active state, last login and portal id. That is the response size a rate
    limit cannot help with, because a compromised staff token needs a single
    request. ``?page=`` still walks the whole directory; it just costs one
    request per page, which is the point.

    25 rather than the parent's 7 because a directory is scanned, not read — 7
    would turn an ordinary "find this client" into four round trips. Still well
    under ``max_page_size`` (50), so the ceiling on a single response holds.
    """

    page_size = 25
