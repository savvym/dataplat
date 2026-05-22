---
name: bucket-naming
description: Use `documents-vlm` (hyphen) NOT `documents_vlm` (underscore) for the VLM bucket in all S3/MinIO storage URIs — S3 naming rules forbid underscores.
metadata:
  type: project
---

The MinIO bucket for VLM-enriched documents is named `documents-vlm` (hyphen).

`docs/data_platform_design.md` §4.3 and `spec/feature_list.json` F-003 both use the underscore form `documents_vlm`, but S3/MinIO bucket naming rules (RFC 1123: lowercase letters, digits, hyphens; 3-63 chars; no underscores) prohibit underscores. The actual bucket created during F-003 (commit 8e1e22b on 2026-05-22) is `documents-vlm`.

**Why:** S3 bucket names must be DNS-compatible. `mc mb documents_vlm` returns "Bucket name contains invalid characters." There is no way to create the underscore form. The implementer (S003-F-003) discovered this during bucket creation; reviewer Mode B approved the rename as forced and unavoidable.

**How to apply:** Any code that constructs S3 storage URIs for the VLM bucket — F-011 PDF upload, F-102 VLM enrichment, any future document_variant for VLM, etc. — must use `documents-vlm`. If you see `documents_vlm` in code or a SQL/Lance config, treat it as a typo and fix it. The other four buckets (`sources`, `documents`, `lance`, `datasets`) are unaffected — they were already hyphen-compatible.

Related: [[verifier-role-scope]] is the other process memory captured from sprint S002-F-002.
