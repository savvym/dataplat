"""In-process asyncio event broker for run status notifications — S051-F-051.

# Single-worker only: this is an in-process asyncio structure.
# Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
# event loss for WS connections on a different worker than the one receiving
# the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class RunEventBroker:
    """In-process asyncio event broker for run status notifications.

    # Single-worker only: this is an in-process asyncio structure.
    # Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
    # event loss for WS connections on a different worker than the one receiving
    # the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.

    Delivery semantics: best-effort, fire-and-forget, no ack, no replay.
    Overflow policy: drop oldest (queue.get_nowait() then put_nowait the new
    event) when the queue is full (maxsize=100 per subscriber connection).
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, list[asyncio.Queue[dict]]] = {}

    def subscribe(self, run_id: int, queue: asyncio.Queue[dict]) -> None:
        """Register queue to receive events for run_id."""
        self._subscribers.setdefault(run_id, []).append(queue)

    def unsubscribe(self, run_id: int, queue: asyncio.Queue[dict]) -> None:
        """Remove queue from the subscriber list for run_id."""
        subs = self._subscribers.get(run_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: int, event: dict) -> None:
        """Fan event out to all queues subscribed to run_id.

        Overflow policy: drop oldest item when queue is full so the new event
        is always enqueued and slow consumers lose stale events rather than new
        ones. Consistent with F-050 best-effort delivery semantics.
        """
        for queue in list(self._subscribers.get(run_id, [])):
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
                    "broker: queue full for run_id=%s after drop-oldest attempt; "
                    "dropping newest event",
                    run_id,
                )
