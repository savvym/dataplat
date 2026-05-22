"""DagsterGateway package — S004-F-004 / S005-F-005.

Re-exports the public surface so callers can import from
`dataplat_api.dagster` directly.
"""

from dataplat_api.dagster.gateway import (
    DagsterGateway,
    DagsterGatewayError,
    DagsterRunNotFoundError,
)

__all__ = ["DagsterGateway", "DagsterGatewayError", "DagsterRunNotFoundError"]
