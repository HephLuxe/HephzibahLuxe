"""
apps/core/urls.py

Cross-app routes that belong to no single app. Mounted under /api/v1/ in
config/urls.py.

Right now that is just the private-file endpoint, which spans six models across
four apps (see apps/core/filelinks.py) and so has no natural home in any of
them.

Why there is no bespoke rate limit here
---------------------------------------
There was one, briefly — a per-IP burst and daily pair, on the reasoning that an
endpoint minting signed URLs deserves its own ceiling. Removed, because none of
that reasoning survives contact with the specifics:

* **The burst is already bounded.** ``THROTTLE_RATES["user_burst"]`` is 120/m
  **per account** and is the one limit covering the whole authenticated surface.
  A stolen token cannot exceed it here any more than anywhere else.
* **A daily cap would not stop the threat it was aimed at.** The worry was a
  compromised token harvesting every file on a portal. A portal holds tens of
  files, not thousands, so that harvest finishes inside any cap worth setting —
  the ceiling changes how it looks in a graph, not whether it succeeds.
* **Ids are not enumerable.** Every model behind this endpoint has a UUID
  primary key, so walking ids to find what exists is not available regardless of
  how many attempts are allowed.
* **Keying it on IP would have starved staff.** Staff sit behind one office
  gateway and browse many portals; a per-IP daily cap counts them as one caller.
  That is the exact cross-caller starvation the per-endpoint limits in
  ``RATE_LIMITS`` were introduced to remove (see the comments there), and
  reintroducing it for no security gain is a bad trade.

So the endpoint inherits ``user_burst`` and nothing else. If a reason to add one
appears, note that the ``_rl`` helpers in inquiries/urls.py and accounts/urls.py
both hardcode ``method="POST"`` and would need a GET variant.
"""

from django.urls import path

from . import file_views

urlpatterns = [
    # GET — mint a 60s signed URL for one private file, after an ownership check.
    path("files/<str:file_type>/<str:obj_id>/", file_views.mint_file_url, name="mint_file_url"),
]
