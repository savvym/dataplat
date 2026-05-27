# dataplat_api/llm/__init__.py
# F-028: LLM Gateway package — exports LLMGateway.
# Hard invariant #4: this is the ONLY package in the entire codebase that may
# import the Anthropic SDK. All other code must call via HTTP or dependency injection.

from dataplat_api.llm.gateway import LLMGateway

__all__ = ["LLMGateway"]
