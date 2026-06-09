# S056-HF1 — hotfix Redis unauth (CVE-class) on host:16379

**Type:** hotfix (out-of-cycle; security). Not driven by `spec/feature_list.json`.
**Trigger:** external report — `9.134.60.24:16379` advertised an unauthenticated Redis instance.
**Approved by:** human request 2026-06-09.

---

## 1. Problem

`docker/docker-compose.dev.yml` exposed Redis with two compounding issues:

1. **No authentication.** The `redis:7-alpine` container started with default config. No `requirepass`, no ACLs. Any TCP client that reached the port could `CONFIG SET dir`, `SLAVEOF`, write keys, or pivot via `CONFIG SET` + `SAVE` write-primitive attacks (the canonical "redis-unauth → RCE" chain).
2. **Host port bound to all interfaces.** The mapping `"${REDIS_HOST_PORT:-16379}:6379"` defaults to `0.0.0.0:16379` in Docker. On a machine with a public IP (`9.134.60.24` here), this exposed Redis to the internet.

`REDIS_URL=redis://redis:6379/0` (no password component) was consistent with the misconfiguration but is unused by any current `apps/api/` or `apps/dagster/` code path (verified with `grep -rn 'redis\|REDIS' apps/`). The risk surface today is purely the open port; the URL change is forward-looking for upcoming F-* RQ work.

### Why nothing in the verifier caught this

`verify/checks.sh smoke` checks API/DB/MinIO/Dagster connectivity but does NOT probe Redis (no current consumer). The compose file is treated as infra, not a code artifact, and no rule asserts "no service binds to 0.0.0.0". This hotfix does not add such a check (out of scope) but the regression surface is now small because both fixes layer.

---

## 2. Fix (defense in depth — three independent layers)

### 2a. Require a password (`--requirepass`)

`redis-server` is launched with `--requirepass "${REDIS_PASSWORD:-devredispassword}"`. The dev default is intentionally non-empty (`devredispassword`) so even the local stack rejects unauth commands; documentation in `.env.example` instructs operators to override with `openssl rand -hex 32` for any non-laptop deployment.

The healthcheck is updated to `redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping` so the password is sent and the noisy `Warning: Using a password with -a` line is suppressed.

### 2b. Bind host port to loopback only

```yaml
ports:
  - "127.0.0.1:${REDIS_HOST_PORT:-16379}:6379"
```

This collapses the network-attack surface to the host itself. Compose-internal service-to-service traffic (`fastapi → redis`) is unaffected because that path uses the compose default bridge network and the container hostname `redis:6379`, not the host port mapping.

### 2c. Embed the password in `REDIS_URL`

`.env.example` now ships `REDIS_URL=redis://:devredispassword@redis:6379/0`. Any future Python consumer (rq, redis-py) will auth automatically. No code change required today — confirmed `grep -rn "REDIS_URL\|redis://" apps/` returns zero matches in `apps/`.

### Why all three, not just one

- 2a alone: leaves the port public and relies on password strength + Redis having no auth-bypass CVE.
- 2b alone: protects from external attackers but a local user (or any container on the host network) can still talk unauth.
- 2c alone: only useful once code consumes Redis; today it does nothing.

Combining them means an attacker needs both host access AND the password.

---

## 3. Files changed

| File | Action | Purpose |
|---|---|---|
| `docker/docker-compose.dev.yml` | MOD | redis service: add `command: [redis-server, --requirepass, ...]`; bind host port to `127.0.0.1`; update healthcheck to AUTH-aware `redis-cli -a … ping`; explanatory comment block |
| `docker/.env.example` | MOD | Add `REDIS_PASSWORD=devredispassword`; update `REDIS_URL` to `redis://:devredispassword@redis:6379/0`; production-rotation note |
| `claude-progress.txt` | APPEND | hotfix open + close entries |
| `contracts/S056-HF1/agreed.md` | NEW | this file |

No `apps/api/`, no `apps/dagster/`, no `apps/web/`, no migrations. Invariants #1–#6 all N/A (no schema, no API change, no LLM call site, no async-SQLA boundary, no OpenAPI surface).

---

## 4. Verification

V1. `docker compose -f docker/docker-compose.dev.yml up -d redis` brings the redis container to **healthy** within 30s (proves the new healthcheck and password-aware AUTH are wired correctly).

V2. From the host:
- `redis-cli -h 127.0.0.1 -p 16379 ping` → `(error) NOAUTH Authentication required.`
- `redis-cli -h 127.0.0.1 -p 16379 -a devredispassword --no-auth-warning ping` → `PONG`.

V3. From outside the host (or simulated via `redis-cli -h <non-loopback-IP> -p 16379 ping`): connection refused / timeout (port no longer published to `0.0.0.0`).

V4. `bash verify/checks.sh smoke` still exits 0 — no consumer regressed.

V5. `git grep -E '^\s*-\s*"\$\{REDIS_HOST_PORT' docker/` shows the `127.0.0.1:` prefix is in place.

V6. `git grep -nE 'requirepass' docker/docker-compose.dev.yml` matches.

---

## 5. Rollback

Revert the single compose-file commit. No data migration, no schema, no client breakage to undo (no consumer reads the new `REDIS_PASSWORD`/updated `REDIS_URL` yet).

---

## 6. Follow-up (NOT this hotfix)

- Add a check to `verify/checks.sh` (network layer) that asserts `docker compose ... ps --format json | jq` shows no service published on a non-loopback interface unless explicitly allowlisted. Track as a future infra feature.
- When the RQ worker lands (deferred per CLAUDE.md MVP boundaries §11.2), add an explicit Redis ping with AUTH to `verify/checks.sh smoke` so future regressions of 2a alone are caught.
