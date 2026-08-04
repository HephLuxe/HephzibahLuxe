from rest_framework.exceptions import ValidationError

from apps.core import deeplinks

from .models import ConversationTag

VALID_TAGS = ConversationTag.values


def validate_tags(tags: list) -> list:
    invalid = [t for t in tags if t not in VALID_TAGS]
    if invalid:
        raise ValidationError(
            f"Invalid tags: {invalid}. Valid options are: {VALID_TAGS}"
        )
    return tags


def validate_links(links: list, engagement=None) -> list:
    """
    The deep-link pills at the bottom of a conversation card ("View Updated Key
    Event Contacts"). `links` is a JSONField, not a related model, so this is
    the one place the shape is enforced.

    Two accepted forms per entry:

      * **Targeted (preferred)** — name the object the pill points at, and the
        URL is derived on read from apps/core/deeplinks.py, exactly as a
        reminder's target is:

            {"target_type": "event_contact", "target_id": "<uuid>"}
            {"target_type": "invoice", "target_id": "<uuid>", "label": "Your invoice"}

        The target is checked against `engagement` — a pill cannot be aimed at
        another client's data, and cannot name a row that doesn't exist.

      * **Free-text (fallback)** — for links with no object behind them (a
        static portal page, an external URL):

            {"label": "View Event Details", "url": "/portal/event-details"}

    `label` is optional on a targeted link (the target supplies a default) and
    required on a free-text one (nothing else can name it).
    """
    if not isinstance(links, list):
        raise ValidationError("links must be a list.")

    for index, link in enumerate(links):
        if not isinstance(link, dict):
            raise ValidationError(f"links[{index}] must be an object.")

        target_type = link.get("target_type")
        target_id = link.get("target_id")

        if target_type or target_id:
            if not (target_type and target_id):
                raise ValidationError(
                    f'links[{index}]: "target_type" and "target_id" must be provided together.'
                )
            # Raises if unknown type / bad id / not this engagement's to link.
            deeplinks.resolve_target(engagement, target_type, target_id)
            continue

        if not link.get("label") or not link.get("url"):
            raise ValidationError(
                f'links[{index}] must be either a targeted link — '
                '{"target_type": "event_contact", "target_id": "<uuid>"} — or a '
                'free-text one with "label" and "url", e.g. '
                '{"label": "View Event Details", "url": "/portal/event-details"}.'
            )

    return links


def resolve_links(conversation) -> list[dict]:
    """
    Render a conversation's stored `links` into what the client actually gets:
    every entry carries a usable {label, url}, with targeted entries resolved
    through core.deeplinks at read time (so a route rename never strands a
    stored pill).

    A targeted entry whose object has since been deleted is **dropped** rather
    than rendered — a pill that 404s on click is worse than no pill.
    """
    rendered: list[dict] = []

    for link in conversation.links or []:
        if not isinstance(link, dict):
            continue

        target_type = link.get("target_type")
        target_id = link.get("target_id")

        if target_type and target_id:
            spec = deeplinks.spec_for_type(target_type)
            if spec is None:
                continue
            try:
                target = spec.get_model().objects.filter(pk=target_id).first()
            except (ValueError, TypeError):
                continue
            if target is None:
                continue  # target deleted — drop the pill
            rendered.append({
                "label": link.get("label") or deeplinks.default_label(target),
                "url": deeplinks.build_url(target),
                "target_type": target_type,
                "target_id": str(target_id),
            })
        elif link.get("url"):
            rendered.append({
                "label": link.get("label"),
                "url": link["url"],
                "target_type": None,
                "target_id": None,
            })

    return rendered
