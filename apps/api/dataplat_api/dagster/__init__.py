"""DagsterGateway package — S004-F-004.

Re-exports the public surface so callers can import from
`dataplat_api.dagster` directly.
"""

from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError

__all__ = ["DagsterGateway", "DagsterGatewayError"]
