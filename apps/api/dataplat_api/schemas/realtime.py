"""Pydantic models for the WebSocket run-subscription protocol — S051-F-051.

These models are WS-only and are NOT referenced by any HTTP route, so they do
not appear in the OpenAPI schema and make codegen produces no diff (hard
invariant #6 satisfied trivially — see agreed.md §3a).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


# ── Server → Client ───────────────────────────────────────────────────────────


class RunStatusChangedEvent(BaseModel):
    """Primary event emitted when a Run status transition occurs.

    Matches design doc §9.3 (lines 912–921).
    """

    type: Literal["run.status_changed"] = "run.status_changed"
    run_id: int
    kind: str
    from_status: str | None = None
    to: str
    metadata: dict[str, Any] = {}

    model_config = {"populate_by_name": True}

    def model_dump_json(self, **kwargs: Any) -> str:  # type: ignore[override]
        # Serialise `from_status` as `from` in the wire format (design doc field name).
        data = self.model_dump()
        data["from"] = data.pop("from_status")
        import json
        return json.dumps(data)


class ServerAck(BaseModel):
    """Acknowledgement sent after a successful subscribe / unsubscribe."""

    type: Literal["subscribed", "unsubscribed"]
    run_id: int


class ServerError(BaseModel):
    """Error frame sent when a client command cannot be fulfilled."""

    type: Literal["error"] = "error"
    code: str
    run_id: int | None


# ── Client → Server ───────────────────────────────────────────────────────────


class ClientSubscribe(BaseModel):
    """Client command to subscribe to a run's status events."""

    type: Literal["subscribe"]
    run_id: int


class ClientUnsubscribe(BaseModel):
    """Client command to unsubscribe from a run's status events."""

    type: Literal["unsubscribe"]
    run_id: int
