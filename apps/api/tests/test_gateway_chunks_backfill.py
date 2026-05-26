"""Gateway unit tests for F-024: launch_chunks_backfill().

Five test cases covering the full DagsterGateway.launch_chunks_backfill() path:
  (a) Happy path — LaunchBackfillSuccess → returns backfillId string.
  (b) PythonError __typename → raises DagsterGatewayError.
  (c) UnauthorizedError __typename → raises DagsterGatewayError.
  (d) Network error (httpx.ConnectError) → raises DagsterGatewayError.
  (e) InvalidSubsetError __typename → raises DagsterGatewayError.

All tests are pure unit tests (no live Dagster required).
Pattern follows test_dagster_notify.py: _gateway_with_response() + pytest.mark.asyncio.
conftest.py autouse fixtures handle engine/SSL mocking.
"""

from __future__ import annotations

import json

import httpx
import pytest

from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError

# ── Shared helpers ─────────────────────────────────────────────────────────────

_TEST_BACKFILL_ID = "backfill-chunks-xyz-456"
_TEST_PARTITION_KEYS = ["src_1", "src_2"]


def _make_mock_response(json_body: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for DagsterGateway unit tests."""
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode(),
        headers={"content-type": "application/json"},
    )


def _gateway_with_response(json_body: dict, status_code: int = 200) -> DagsterGateway:
    """Return a DagsterGateway whose _client.post is mocked to return json_body."""
    from unittest.mock import AsyncMock

    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        return_value=_make_mock_response(json_body, status_code)
    )
    return gw


# ─────────────────────────────────────────────────────────────────────────────
# (a) Happy path — LaunchBackfillSuccess
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_chunks_backfill_success() -> None:
    """LaunchBackfillSuccess → returns the backfillId string."""
    gw = _gateway_with_response({
        "data": {
            "launchPartitionBackfill": {
                "__typename": "LaunchBackfillSuccess",
                "backfillId": _TEST_BACKFILL_ID,
            }
        }
    })
    result = await gw.launch_chunks_backfill(_TEST_PARTITION_KEYS)
    assert result == _TEST_BACKFILL_ID

    # Verify the mutation payload was sent with the correct assetSelection.
    call_kwargs = gw._client.post.call_args
    sent_payload = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
    backfill_params = sent_payload["variables"]["backfillParams"]
    assert backfill_params["assetSelection"] == [{"path": ["chunks"]}]
    assert backfill_params["partitionNames"] == _TEST_PARTITION_KEYS
    assert backfill_params["title"] == "F-024 chunks"


# ─────────────────────────────────────────────────────────────────────────────
# (b) PythonError __typename → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_chunks_backfill_python_error() -> None:
    """PythonError __typename from Dagster → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "launchPartitionBackfill": {
                "__typename": "PythonError",
                "message": "Something went wrong in Dagster",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_chunks_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (c) UnauthorizedError __typename → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_chunks_backfill_unauthorized() -> None:
    """UnauthorizedError __typename → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "launchPartitionBackfill": {
                "__typename": "UnauthorizedError",
                "message": "Not authorized",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_chunks_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (d) Network error (ConnectError) → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_chunks_backfill_network_error() -> None:
    """httpx.ConnectError → raises DagsterGatewayError('Cannot connect to Dagster')."""
    from unittest.mock import AsyncMock

    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(DagsterGatewayError, match="Cannot connect to Dagster"):
        await gw.launch_chunks_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (e) InvalidSubsetError __typename → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_chunks_backfill_invalid_subset() -> None:
    """InvalidSubsetError __typename → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "launchPartitionBackfill": {
                "__typename": "InvalidSubsetError",
                "message": "Asset key not in job subset",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_chunks_backfill(_TEST_PARTITION_KEYS)
