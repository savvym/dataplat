---
name: plugin-protocol
description: The SourceAdapter and Processor protocols. Read before writing or modifying any plugin under plugins/.
---

# Plugin protocols — the rules of the game

All plugins implement one of two Protocols. They live in `packages/core/`.

## SourceAdapter — external world → Bronze Commit

```python
from typing import Protocol
from pathlib import Path
from pydantic import BaseModel

class SourceAdapter(Protocol):
    name: str               # "firecrawl-url"
    version: str            # "1.2.0"
    input_schema: type[BaseModel]  # Pydantic model class describing accepted input
    output_subtype: str     # "webpage-collection"

    def ingest(
        self,
        spec: BaseModel,       # validated instance of input_schema
        workspace: Path,       # platform-managed scratch dir
        ctx: "AdapterContext", # ctx.llm, ctx.log, ctx.progress, ctx.fs
    ) -> "IngestResult":
        ...
```

`IngestResult` must contain:
- `files: list[FileEntry]` — paths relative to workspace + their content types
- `manifest: dict` — for the bronze repo's `manifest.yaml`
- `card_yaml: dict` — populates `dataset-card.yaml`'s `source_spec` field
- `notes: str` — for the commit message

## Processor — upstream Repository@version → downstream Commit

```python
class Processor(Protocol):
    name: str
    version: str
    input_schema: type[BaseModel]
    accepts: list[str]      # accepted upstream subtypes (e.g., ["pdf", "webpage-collection"])
    produces: str           # output subtype (e.g., "text-corpus")

    def process(
        self,
        inputs: list["RepoSnapshot"],   # frozen views of upstream commits
        config: BaseModel,
        workspace: Path,
        ctx: "ProcessorContext",
    ) -> "ProcessResult":
        ...
```

## Idempotency contract

For the same `(input_refs, config_hash, plugin_version)`, the result MUST be reproducible. The platform may cache.

## Error handling

Raise typed errors, never bare `Exception`:

```python
from dataplat_core.errors import PluginError, RetryablePluginError

raise RetryablePluginError("Source returned 503")  # platform will retry
raise PluginError("Invalid input: missing 'url'")  # platform will fail the job
```

## Workspace contract

- Workspace is a fresh empty dir; do not assume any pre-existing files.
- Clean up large temp files in `try/finally` even though the platform will delete workspace after.
- Do NOT write outside `workspace`. Reading is OK; writing only inside the workspace.

## LLM access

If you need an LLM, use `ctx.llm.call(...)`. NEVER import anthropic/openai SDKs directly. See `skills/llm-gateway/SKILL.md`.

## Testing pattern

```
plugins/<name>/tests/
├── test_unit.py        # mocks ctx, tests pure logic
├── test_integration.py # uses a real workspace dir, real fixtures
└── ../fixtures/        # tiny but real input samples
```

Each test must exercise both success and at least one error path.
