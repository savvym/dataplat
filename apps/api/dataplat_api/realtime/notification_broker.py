"""In-process asyncio event broker for user-scoped asset notifications — S052-F-052.

# Single-worker only: this is an in-process asyncio structure.
# Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
# event loss for WS connections on a different worker than the one receiving
# the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.

Distinct from RunEventBroker (broker.py), which is keyed by run_id.
NotificationBroker is keyed by user_id (int) — events are routed to the user
who triggered the underlying Dagster run (Run.triggered_by).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class NotificationBroker:
    """In-process asyncio event broker for user-scoped asset notifications.

    Keyed by user_id (int). All subscribers for a given user_id receive
    every notification event published for that user.

    # Single-worker only: this is an in-process asyncio structure.
    # Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
    # event loss for WS connections on a different worker than the one receiving
    # the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.

    Delivery semantics: best-effort, fire-and-forget, no ack, no replay.
    Overflow policy: drop oldest (queue.get_nowait() then put_nowait the new
    event) when the queue is full (maxsize=100 per subscriber connection).

    Subscribe+publish race: events fired between the POST webhook arriving and
    the client connecting are permanently lost. Frontend MUST poll REST endpoints
    to get current state on (re)connect. This constraint is inherited from
    F-051 and is documented here for clarity.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, list[asyncio.Queue[dict]]] = {}

    def subscribe(self, user_id: int, queue: asyncio.Queue[dict]) -> None:
        """Register queue to receive events for user_id."""
        self._subscribers.setdefault(user_id, []).append(queue)

    def unsubscribe(self, user_id: int, queue: asyncio.Queue[dict]) -> None:
        """Remove queue from the subscriber list for user_id."""
        subs = self._subscribers.get(user_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(user_id, None)

    def publish(self, user_id: int, event: dict) -> None:
        """Fan event out to all queues subscribed to user_id.

        Overflow policy: drop oldest item when queue is full so the new event
        is always enqueued and slow consumers lose stale events rather than new
        ones. Consistent with F-051 RunEventBroker semantics.
        """
        for queue in list(self._subscribers.get(user_id, [])):
            if queue.full():
                # Drop oldest: make room for the new event.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Extremely unlikely (race between full-check and put),
                # drop newest silently.
                logger.debug(
                    "notification_broker: queue full for user_id=%s after drop-oldest "
                    "attempt; dropping newest event",
                    user_id,
                )
