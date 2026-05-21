---
name: repository-invariants
description: The non-negotiable invariants of the Repository/Commit/Lineage model. Read before any change touching apps/api/dataplat_api/services/{repo,commit,lineage}.py or packages/core/ types.
---

# Repository model invariants

From design doc §1.2 design principles + §2.2 concepts. Violating any fails review.

## INV-1: Lineage is mandatory

Every Commit MUST have a `lineage_info` field with:
- `parents: list[str]` — parent commit hashes (may be empty for true initial commits)
- `processor: str | None` — `"<plugin-name>@<version>"` if derived; null if direct upload
- `config_hash: str | None` — sha256 of the config used, if any
- `inputs: list[InputRef]` — upstream `(repo_id, commit_hash)` pairs if derived

There is no "anonymous" commit. There is no "we'll backfill lineage later".

## INV-2: Content addressed by sha256

Blobs are addressed by `sha256(content)`. The blob store path is derived from the hash. Identical content across repos shares one blob. If you find yourself giving a blob a path based on filename or upload time, **stop**.

## INV-3: Schema frozen post-publish

Once a Silver/Gold repo has a commit on a non-dev ref, its schema MUST NOT be modified in place. Schema changes require:
- A new commit (which can change schema only if the migration is documented in `lineage_info.schema_change`)
- Usually a new version tag, since schema changes are breaking

## INV-4: Metadata in DB, content in object store

- Postgres: repos, commits, refs, lineage edges, file entries (path + blob_hash + size).
- Object store (MinIO/S3): blobs only.
- Never store blob bytes in Postgres (no BYTEA columns for content).
- Never store metadata in object store as source of truth (manifests etc. are materialized views).

## INV-5: Tree is path → blob_hash

A commit's tree is `dict[path, blob_hash]`. No path-based content inheritance; every file lists its full path. (Git-style nested trees are an internal optimization, not the abstraction.)

## INV-6: Bronze is faithful

Bronze layer commits preserve source content. **Semantic cleaning (dedup beyond exact-hash, content rewriting, language filtering) belongs in Silver processors.** What IS allowed in Bronze adapters: format normalization (PDF→md, HTML→md, encoding fixes).

## Self-check before declaring done

If your change touches Repository/Commit logic, walk through INV-1 through INV-6 with a concrete example. If you can't articulate why each holds in your change, you haven't checked.
