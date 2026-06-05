"""Pydantic models for the WebSocket protocols — S051-F-051 / S052-F-052.

These models are WS-only and are NOT referenced by any HTTP route, so they do
not appear in the OpenAPI schema and make codegen produces no diff (hard
invariant #6 satisfied trivially — see agreed.md §3a for F-051, §9 for F-052).
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


# ── F-052 — Server → Client notification events ───────────────────────────────


class AssetMaterializedEvent(BaseModel):
    """Fired when a Dagster asset materializes successfully (F-052).

    Matches verification criterion §1.2:
      {"type": "asset.materialized", "asset_key": "extract_mineru", "partition_key": "src_..."}
    """

    type: Literal["asset.materialized"] = "asset.materialized"
    asset_key: str        # e.g. "extract_mineru"
    partition_key: str    # e.g. "src_42"


class ChunksAddedEvent(BaseModel):
    """Fired when the chunks asset completes for a source partition (F-052).

    Matches verification criterion §1.3:
      {"type": "chunks.added", "source_id": <id>, "count": <N>}

    count is the real chunk count from Dagster materialization metadata
    (asset_event.asset_materialization.metadata["chunk_count"].value).
    """

    type: Literal["chunks.added"] = "chunks.added"
    source_id: int    # parsed from partition_key "src_{N}"
    count: int        # number of chunks produced (real value from Dagster metadata)
