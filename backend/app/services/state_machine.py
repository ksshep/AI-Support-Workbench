"""Explicit ticket state machine.

The transition table is the single source of truth for which ``(status,
event)`` pairs are legal and which status they lead to. Terminal states
(``closed``, ``canceled``) have empty maps so every event on them is rejected.

Permission rules live here too, as data, so the state machine cannot be
bypassed by forgetting an ``if`` in a route handler.
"""

# Event names the API accepts. The task spec names the review->replied event
# ``reply`` (docs use ``send_reply``); the task spec wins.
VALID_EVENTS = ("start_review", "reply", "close", "cancel")

# Explicit transition table: current status -> {event: next status}.
STATUS_TRANSITIONS: dict[str, dict[str, str]] = {
    "open": {
        "start_review": "in_review",
        "cancel": "canceled",
    },
    "in_review": {
        "reply": "replied",
        "cancel": "canceled",
    },
    "replied": {
        "close": "closed",
    },
    "closed": {},   # terminal state
    "canceled": {},  # terminal state
}

# Which roles may trigger each event.
EVENT_ROLES: dict[str, tuple[str, ...]] = {
    "start_review": ("agent", "admin"),
    "reply": ("agent", "admin"),
    "close": ("agent", "admin"),
    "cancel": ("customer", "agent", "admin"),
}

# ``cancel`` has an extra rule: a customer may only cancel their own open
# ticket, never a ticket that staff has already picked up.
CUSTOMER_CANCEL_ALLOWED_STATUSES = ("open",)


class InvalidTransitionError(ValueError):
    """Raised when the (status, event) pair is not allowed by the table.

    Carries ``current`` and ``allowed`` so callers can build the rich
    ``detail`` payload (current_status + allowed_events) for the client.
    """

    def __init__(
        self, message: str, current: str | None = None, allowed: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.current = current
        self.allowed = allowed or []


class ForbiddenTransitionError(ValueError):
    """Raised when the actor's role may not trigger the event."""


def next_status(current: str, event: str) -> str:
    """Return the status reached by firing ``event`` from ``current``.

    Raises ``InvalidTransitionError`` for unknown events or transitions the
    table does not allow. The database CHECK constraint on ``tickets.status``
    is the final backstop behind this table.
    """
    if event not in VALID_EVENTS:
        raise InvalidTransitionError(
            f"unknown event '{event}'; valid events: {', '.join(VALID_EVENTS)}"
        )
    allowed = STATUS_TRANSITIONS.get(current)
    if not allowed or event not in allowed:
        allowed_events = list(STATUS_TRANSITIONS.get(current, {}).keys())
        raise InvalidTransitionError(
            f"Cannot move ticket from '{current}' with event '{event}'.",
            current=current,
            allowed=allowed_events,
        )
    return allowed[event]


def allowed_events(status: str) -> list[str]:
    """Return the events that can currently fire from ``status``."""
    return list(STATUS_TRANSITIONS.get(status, {}).keys())


def check_role_can_transition(
    role: str, current: str, event: str
) -> None:
    """Raise ``ForbiddenTransitionError`` when ``role`` may not fire ``event``.

    ``cancel`` needs the extra customer rule from the spec: only from ``open``.
    """
    allowed_roles = EVENT_ROLES.get(event, ())
    if role not in allowed_roles:
        raise ForbiddenTransitionError(
            f"role '{role}' is not allowed to fire event '{event}'"
        )
    if role == "customer" and current not in CUSTOMER_CANCEL_ALLOWED_STATUSES:
        raise ForbiddenTransitionError(
            f"customer can only cancel their own ticket from 'open', "
            f"not from '{current}'"
        )
