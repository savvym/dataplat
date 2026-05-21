---
name: openapi-cycle
description: Read whenever modifying any API route, request model, or response model in apps/api. The TS types in packages/api-types/ MUST stay in sync.
---

# OpenAPI ↔ TS type sync is enforced by CI

§11.7 #3: **OpenAPI codegen 一定在 CI 强制**. If you forget, CI will reject the PR.

## After any of these, run `make codegen`:
- Adding / removing / renaming a route
- Changing a Pydantic request or response model field
- Changing field types or making fields optional/required
- Changing path parameters or query parameters
- Changing HTTP status codes returned

## The cycle

```bash
# 1. Make your API change
$EDITOR apps/api/dataplat_api/routers/repos.py

# 2. Regenerate
make codegen

# 3. Verify the diff in packages/api-types/
git diff packages/api-types/

# 4. Commit BOTH backend and codegen artifacts in the SAME commit
git add apps/api/ packages/api-types/
git commit -m "api: <change>"
```

## How `make codegen` works (so you can debug it)

1. Boots a transient FastAPI app instance and dumps `openapi.json` to `packages/api-types/openapi.json`.
2. Runs `pnpm --filter @dataplat/api-types run generate` which uses `openapi-typescript` to produce `src/generated.ts`.
3. Frontend imports from `@dataplat/api-types`.

## Hard NOs

- Editing `packages/api-types/src/generated.ts` by hand. (It will be overwritten.)
- Committing backend changes without running codegen.
- Importing API types in the frontend from anywhere except `@dataplat/api-types`.
- Adding API types to the frontend manually "because codegen is slow".
