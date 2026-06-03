"""Gateway unit tests for F-042: add_dataset_partition() and launch_dataset_backfill().

Mirrors test_gateway_chunks_backfill.py exactly in structure (agreed.md §8.2).
Each method gets 5 tests.

For add_dataset_partition:
  (a) AddDynamicPartitionSuccess → returns None; payload uses partitionsDefName='dataset_versions'
  (b) DuplicateDynamicPartitionError → returns None (idempotent no-op)
  (c) UnauthorizedError → raises DagsterGatewayError
  (d) PythonError → raises DagsterGatewayError
  (e) httpx.ConnectError → raises DagsterGatewayError("Cannot connect to Dagster")

For launch_dataset_backfill:
  (a) LaunchBackfillSuccess → returns backfillId; payload has assetSelection=[{"path": ["dataset"]}]
  (b) PythonError → raises DagsterGatewayError("launchPartitionBackfill failed")
  (c) UnauthorizedError → raises DagsterGatewayError
  (d) httpx.ConnectError → raises DagsterGatewayError("Cannot connect to Dagster")
  (e) InvalidSubsetError → raises DagsterGatewayError

All tests are pure unit tests (no live Dagster required).
Pattern follows test_gateway_chunks_backfill.py: _gateway_with_response() + pytest.mark.asyncio.
conftest.py autouse fixtures handle engine/SSL mocking.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError

# ── Shared helpers ─────────────────────────────────────────────────────────────

_TEST_BACKFILL_ID = "backfill-dataset-test-xyz-789"
_TEST_PARTITION_KEY = "ds_5_v1"
_TEST_PARTITION_KEYS = ["ds_5_v1"]


def _make_mock_response(json_body: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for DagsterGateway unit tests."""
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode(),
        headers={"content-type": "application/json"},
    )


def _gateway_with_response(json_body: dict, status_code: int = 200) -> DagsterGateway:
    """Return a DagsterGateway whose _client.post is mocked to return json_body."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        return_value=_make_mock_response(json_body, status_code)
    )
    return gw


# =============================================================================
# add_dataset_partition tests
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# (a) AddDynamicPartitionSuccess
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dataset_partition_success() -> None:
    """AddDynamicPartitionSuccess → returns None; payload sent with partitionsDefName='dataset_versions'."""
    gw = _gateway_with_response(
        {
            "data": {
                "addDynamicPartition": {
                    "__typename": "AddDynamicPartitionSuccess",
                    "partitionKey": _TEST_PARTITION_KEY,
                    "partitionsDefName": "dataset_versions",
                }
            }
        }
    )
    result = await gw.add_dataset_partition(_TEST_PARTITION_KEY)
    assert result is None

    # Verify the payload was sent with correct partitionsDefName
    call_kwargs = gw._client.post.call_args
    sent_payload = (
        call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
    )
    assert sent_payload["variables"]["partitionsDefName"] == "dataset_versions"
    assert sent_payload["variables"]["partitionKey"] == _TEST_PARTITION_KEY


# ─────────────────────────────────────────────────────────────────────────────
# (b) DuplicateDynamicPartitionError → idempotent no-op
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dataset_partition_duplicate() -> None:
    """DuplicateDynamicPartitionError → returns None (idempotent no-op, logged at DEBUG)."""
    gw = _gateway_with_response(
        {
            "data": {
                "addDynamicPartition": {
                    "__typename": "DuplicateDynamicPartitionError",
                    "partitionsDefName": "dataset_versions",
                    "partitionName": _TEST_PARTITION_KEY,
                    "message": "Partition ds_5_v1 already exists in dataset_versions",
                }
            }
        }
    )
    result = await gw.add_dataset_partition(_TEST_PARTITION_KEY)
    assert result is None  # no exception raised — idempotent


# ─────────────────────────────────────────────────────────────────────────────
# (c) UnauthorizedError → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dataset_partition_unauthorized() -> None:
    """UnauthorizedError from Dagster → raises DagsterGatewayError."""
    gw = _gateway_with_response(
        {
            "data": {
                "addDynamicPartition": {
                    "__typename": "UnauthorizedError",
                    "message": "The repository does not contain a dynamic partitions definition named 'dataset_versions'.",
                }
            }
        }
    )
    with pytest.raises(DagsterGatewayError, match="UnauthorizedError"):
        await gw.add_dataset_partition(_TEST_PARTITION_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# (d) PythonError → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dataset_partition_python_error() -> None:
    """PythonError from Dagster → raises DagsterGatewayError."""
    gw = _gateway_with_response(
        {
            "data": {
                "addDynamicPartition": {
                    "__typename": "PythonError",
                    "message": "An internal error occurred in Dagster",
                }
            }
        }
    )
    with pytest.raises(DagsterGatewayError, match="PythonError"):
        await gw.add_dataset_partition(_TEST_PARTITION_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# (e) Network error → DagsterGatewayError("Cannot connect to Dagster")
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_dataset_partition_network_error() -> None:
    """httpx.ConnectError → raises DagsterGatewayError('Cannot connect to Dagster')."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(DagsterGatewayError, match="Cannot connect to Dagster"):
        await gw.add_dataset_partition(_TEST_PARTITION_KEY)


# =============================================================================
# launch_dataset_backfill tests
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# (a) LaunchBackfillSuccess
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_dataset_backfill_success() -> None:
    """LaunchBackfillSuccess → returns backfillId string.

    Payload must have assetSelection=[{"path": ["dataset"]}] and title="F-042 dataset".
    """
    gw = _gateway_with_response(
        {
            "data": {
                "launchPartitionBackfill": {
                    "__typename": "LaunchBackfillSuccess",
                    "backfillId": _TEST_BACKFILL_ID,
                }
            }
        }
    )
    result = await gw.launch_dataset_backfill(_TEST_PARTITION_KEYS)
    assert result == _TEST_BACKFILL_ID

    # Verify the mutation payload
    call_kwargs = gw._client.post.call_args
    sent_payload = (
        call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
    )
    backfill_params = sent_payload["variables"]["backfillParams"]
    assert backfill_params["assetSelection"] == [{"path": ["dataset"]}]
    assert backfill_params["partitionNames"] == _TEST_PARTITION_KEYS
    assert backfill_params["title"] == "F-042 dataset"


# ─────────────────────────────────────────────────────────────────────────────
# (b) PythonError → DagsterGatewayError("launchPartitionBackfill failed")
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_dataset_backfill_python_error() -> None:
    """PythonError __typename → raises DagsterGatewayError('launchPartitionBackfill failed')."""
    gw = _gateway_with_response(
        {
            "data": {
                "launchPartitionBackfill": {
                    "__typename": "PythonError",
                    "message": "Something went wrong in Dagster",
                }
            }
        }
    )
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_dataset_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (c) UnauthorizedError → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_dataset_backfill_unauthorized() -> None:
    """UnauthorizedError __typename → raises DagsterGatewayError."""
    gw = _gateway_with_response(
        {
            "data": {
                "launchPartitionBackfill": {
                    "__typename": "UnauthorizedError",
                    "message": "Not authorized to launch backfills",
                }
            }
        }
    )
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_dataset_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (d) Network error → DagsterGatewayError("Cannot connect to Dagster")
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_dataset_backfill_network_error() -> None:
    """httpx.ConnectError → raises DagsterGatewayError('Cannot connect to Dagster')."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(DagsterGatewayError, match="Cannot connect to Dagster"):
        await gw.launch_dataset_backfill(_TEST_PARTITION_KEYS)


# ─────────────────────────────────────────────────────────────────────────────
# (e) InvalidSubsetError → DagsterGatewayError
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_dataset_backfill_invalid_subset() -> None:
    """InvalidSubsetError __typename → raises DagsterGatewayError."""
    gw = _gateway_with_response(
        {
            "data": {
                "launchPartitionBackfill": {
                    "__typename": "InvalidSubsetError",
                    "message": "Asset key 'dataset' not found in job subset",
                }
            }
        }
    )
    with pytest.raises(DagsterGatewayError, match="launchPartitionBackfill failed"):
        await gw.launch_dataset_backfill(_TEST_PARTITION_KEYS)
