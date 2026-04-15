# Security review — `modal-mcp_v2.md`

**Bottom line.** The plan names the right primitives (HMAC for refs and approval tokens, envelope encryption for hosted persistence, OriginGuard for DNS rebinding, dry-run + single-use approval tokens, redaction before logger formatter, hard OS sandbox for Expert) but every one of them is specified at prose level — the byte-level constructions a security reviewer needs to audit are absent or contradictory. The HMAC canonicalisation is undefined, approval-token replay protection is hand-waved across restarts, redaction ordering is asserted but never pinned to a processor position, envelope encryption names no cipher/mode/AAD/nonce discipline, the Expert sandbox's `preexec_fn` / env-scrubbing / cgroup-owner story is missing, and `OriginGuard`'s placement relative to FastMCP middleware is drawn in ASCII but never stated as an ordering invariant. As specified this cannot ship: an auditor would require a revision round before any hosted or mutating milestone. Self-hosted v1 is closer to defensible but still has at least one Critical (HMAC canonicalisation) and multiple Majors.

---

### F1. HMAC canonicalisation for Refs, Cursors, and approval tokens is undefined — parser-differential forgery and cross-kind confusion are both reachable

**Severity:** Critical

**Why it matters:** The spec commits to HMAC-SHA256 over "a payload encoding `{kind, id, env, ws}`" for `Ref`, and "scope-bound to `(tool_name, target_refs, actor, workspace)`" for approval tokens, but never fixes a canonical byte form. Two implementations (or an implementer and a drift-probe refactor six months later) will disagree on field order, optional-field presence, MessagePack vs. JSON, integer encoding width, unicode normalisation, or handling of `None`. Worse, the spec says cursor/ref payloads "are serialised as MessagePack **or** strict JSON" (§10.3 L1516–1518) — i.e., the *format is not pinned at all*, so a verifier that accepts both can be fed a JSON payload whose MessagePack re-encoding hashes to the same MAC as a different logical payload. This is the exact parser-differential class that broke JWT "alg=none" and multiple JWS implementations. A concrete attack: if `kind` is stored alongside `id` and the HMAC is computed over a JSON re-serialisation that drops `kind` when equal to a default, a ref minted for `kind=environment` can be replayed where `kind=workspace` is expected. The spec never states `kind` is part of the signed input, never states a domain-separator prefix, and never names the canonical serialiser.

**Evidence from the plan:** v2:§5.5 L765–777; v2:§7.4 L1187–1189; v2:§10.3 L1516–1518 ("serialised as MessagePack **or** strict JSON" — the "or" is the bug); v2:§10.2 L1479–1482.

**Recommended change:** Pin exactly one canonicalisation and write the byte recipe into §5.5. Recommended construction: a domain-separated MAC input of the form `HMAC-SHA256(K, "modal-mcp/v1" || 0x00 || type_tag || 0x00 || keyid || 0x00 || canonical_cbor_deterministic(payload))` where `type_tag` ∈ {`"ref"`, `"cursor"`, `"approval"`}, `keyid` is a key-identifier byte (see F3), and `canonical_cbor_deterministic` is RFC 8949 §4.2.1 ("Core Deterministic Encoding"). CBOR deterministic encoding fixes map ordering, integer width, and string form; MessagePack has no equivalent standard. Reject any payload whose re-encoding does not round-trip. State explicitly that `kind`, `env`, `ws`, and the token version are in the MAC input. Reference: RFC 8949, NIST SP 800-107 Rev.1 §5.3.4, OWASP ASVS 6.2.3. Name `hmac.compare_digest` for verification.

---

### F2. Approval-token "single-use" is only in-memory — trivial replay across process restart, and no cross-worker consistency is specified

**Severity:** Blocker

**Why it matters:** The plan says approval tokens are single-use, TTL 60–180 s, scope-bound to `(tool_name, target_refs, actor, workspace)`. It never says where the used-token set lives. For a single-process self-hosted server, an in-memory set is barely tolerable; for the hosted milestones (v2–v4) the plan already describes a Starlette/uvicorn app with rate-limit state — which in any realistic deployment will be multi-worker (`uvicorn --workers N`) or scale-out behind a load balancer. An attacker who obtains a valid approval token (e.g., via LLM-side logging, client-side prompt-injection exfil, or a shoulder-surf of the dry-run response) can replay it within the 60–180 s window against any worker that hasn't seen it, or against a single worker after an `async` crash + supervisor restart. `modal_stop_app` is documented as irreversible — one successful replay ends a production deployment. The spec also gives no guarantee that the same token cannot be consumed twice concurrently: the natural implementation is a TOCTOU race unless wrapped in an asyncio lock.

**Evidence from the plan:** v2:§7.4 L1184–1196; v2:§10.2 L1492–1498; v2:§7.5 L1206–1218; v2:§12 L1664.

**Recommended change:** Specify the used-token store as a named component with four invariants:
1. Constant-time check-and-insert under a per-process `asyncio.Lock` (or an atomic Redis `SET NX PX` for multi-worker/hosted). Name the store `ApprovalTokenLedger` and mandate it in §7.4.
2. Persistence across crashes: for self-hosted the ledger is an append-only file fsync'd before `AppStop` is dispatched; for hosted, a Redis with `PX = token_ttl_ms` and `NX` semantics.
3. Forbid token issuance and consumption on different workers without a shared ledger — i.e., if `MODAL_MCP_APPROVAL_LEDGER` is unset, reject `--workers > 1` at startup.
4. Bind tokens to `mcp_session_id` in addition to the existing scope tuple (see F5).

Add a §9.1 test: "replay a consumed approval token within TTL → `POLICY_BLOCKED`; replay across a process restart → still `POLICY_BLOCKED`".

---

### F3. No key-ID / key-rotation discipline for `MODAL_MCP_SIGNING_KEY`

**Severity:** Major

**Why it matters:** `MODAL_MCP_SIGNING_KEY` is the single key used for Refs, Cursors, and approval tokens. The spec says nothing about rotation. The implications: (a) on rotation, every outstanding ref a client has cached instantly becomes invalid; (b) there is no mechanism to revoke a specific leaked key because there is no key-identifier field in the MAC input; (c) if an operator rotates the key by restarting the server under a new env var, every already-issued but-not-yet-consumed approval token silently becomes unredeemable; (d) no discussion of where this key lives — `MODAL_MCP_SIGNING_KEY` is declared "required" in §11 L1643 as an env var alongside Modal tokens, exposing it to the same `/proc/self/environ` / core-dump leak surface that §10.3 tries to close for Modal tokens.

**Evidence from the plan:** v2:§11 L1643; v2:§7.5 L1206–1221; v2:§10.3 L1515–1521.

**Recommended change:** Add a key-ID byte to the MAC input (see F1), reserve a current-key-ID and a set of still-valid verify-key-IDs, and state the rotation protocol: "`MODAL_MCP_SIGNING_KEYS` accepts a comma-separated list `kid1:hex,kid2:hex`; the first entry is the signing key, all entries are valid verify keys; operators rotate by prepending a new entry, deploying, and removing the old entry after max(ref-TTL, approval-TTL, session-TTL)." For the storage-at-rest concern, require loading the signing key from a file path (`MODAL_MCP_SIGNING_KEY_FILE`) or from a KMS reference, not an env var. Reference: NIST SP 800-57 Part 1 Rev.5 §5.3.5, OWASP ASVS 6.4.1.

---

### F4. Envelope encryption for hosted persistence names no cipher, no AAD, no nonce discipline

**Severity:** Critical

**Why it matters:** §7.5 says "envelope encryption. Operator provides a master key via KMS / Secret Store; Server generates a per-session data key, encrypts the session data with it, encrypts the data key with the master key, stores only `(encrypted_data_key, ciphertext)`." That is pseudo-code. A security auditor needs: (a) AEAD name and mode (AES-256-GCM, ChaCha20-Poly1305, AES-GCM-SIV?); (b) nonce construction — AES-GCM with a random 96-bit nonce has a ~2^32 birthday bound and a single reuse leaks the authentication key; (c) AAD composition; (d) data-key reuse semantics; (e) the "on rotation, only data keys need re-encryption" claim implies no forward secrecy, even though the plan's wording ("sealed") implies otherwise.

**Evidence from the plan:** v2:§7.5 L1211–1218; v2:§7.1 L1121; v2:§10.2 L1475–1477.

**Recommended change:** Rewrite §7.5's "opt-in persistence" paragraph to name an exact construction. Recommended: AES-256-GCM-SIV (RFC 8452) for nonce-reuse-resistance. If AES-256-GCM is chosen instead, mandate a 96-bit nonce composed as `random(64) || counter(32)` with a per-data-key counter kept in memory and the data key rotated *before* counter exhaustion; reject any design that reuses a `(key, nonce)` pair. AAD must be `"modal-mcp/session/v1" || session_id || actor_hash || master_key_id`. Specify that data keys are generated fresh per session, ciphertexts store `nonce || ciphertext || tag`, and the data key is derived via HKDF-SHA256 with the session ID as `info`. Explicitly state whether forward secrecy is claimed. Reference: RFC 5116, RFC 8452, NIST SP 800-38D §8.3, OWASP ASVS 6.2.4–6.2.6.

---

### F5. Approval tokens are not session-bound — a token minted in one MCP session can be consumed from another

**Severity:** Major

**Why it matters:** The scope binding is `(tool_name, target_refs, actor, workspace)` (§7.4 L1188). `actor` is an opaque identity; `mcp_session_id` is deliberately *not* in the scope. Consequence: if a client caches the approval token in a stored context and a second session under the same `actor` starts (e.g., a reconnected client, a second tab, a parallel agent run, or a deliberately-confused-deputy where an attacker hijacks the actor identity via cookie reuse), the second session can consume the token. The design intent in the sequence diagram shows the same client consuming the token, but the contract does not enforce that. In hosted mutating mode, where `actor` is a JWT subject, a stolen or accidentally-shared JWT means the approval token is effectively bearer credential.

**Evidence from the plan:** v2:§7.4 L1185–1196; v2:§10.5 L1613–1621; v2:§7.5 L1200–1204.

**Recommended change:** Extend the approval-token scope tuple to `(tool_name, target_refs_sorted, actor, workspace, mcp_session_id, auth_session_id, nonce, exp)`. State explicitly in §7.4 that the verifier rejects if any tuple element differs, with `target_refs` canonicalised by sorting their canonical form before hashing. Add §9.1 security test: "approval token issued in session A, presented in session B → `POLICY_BLOCKED`".

---

### F6. "Redaction runs before any logger formatter" is asserted as prose but never pinned to a structlog processor position

**Severity:** Critical

**Why it matters:** §7.5 L1219–1221 and §10 item 9 L1437–1441 both say "redaction runs before any logger formatter — the logger wraps the redactor, not the other way around". In structlog this is a processor-chain ordering. The spec never gives the concrete processor list in §8.1. Three concrete bypasses:

1. **Python exception tracebacks.** `structlog.processors.format_exc_info` stringifies the full traceback, *including argument reprs*. If a Modal adapter call raises with `MODAL_TOKEN_SECRET` in the call frame's locals, Python's traceback formatter includes it. Unless the redactor runs after `format_exc_info` and recursively on the rendered string, the token is in the log.
2. **Pydantic `repr` on domain objects with secret fields.** Pydantic v2 defaults to exposing field values in `repr`. FastAPI / Starlette / any uncaught-exception middleware will `repr` the request or the `Settings` object in its error page. The only defence is `SecretStr` on every secret field — not mentioned.
3. **Adapter `debug` dict.** §6.4 defines `ModalAdapterError(debug=...)`, but the error is also `audit.record_error(ctx, tool_name, e)` in §7.6, which stringifies the exception into the audit log regardless of `MODAL_MCP_DEBUG`.

**Evidence from the plan:** v2:§7.5 L1219–1221; v2:§8.1 L1260–1286; v2:§7.6 L1241–1248; v2:§10 L1437–1441; v2:§11 L1634.

**Recommended change:** Write the concrete structlog processor chain into §8.1 with ordering fixed:

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,        # stringify exc before redacting
        modal_mcp.redact.structlog_redact_processor, # **redactor MUST follow exc_info**
        structlog.processors.JSONRenderer(),
    ],
    ...
)
```

Mandate `pydantic.SecretStr` for every field in `Settings` that holds a secret. Mandate that `audit.record_error` passes the exception through `format_exc_info → redact` before writing. Add §9.1 test: "raise an exception containing `MODAL_TOKEN_SECRET=...` from inside a tool; assert the token never appears in the log JSONL or the audit JSONL".

---

### F7. Approval-gate threat model trusts the MCP client to enforce human-in-the-loop — an autonomous agent can self-approve in one round-trip

**Severity:** Major

**Why it matters:** §7.4 L1194–1196 explicitly says "Human-in-the-loop is expected at the MCP client: the client surfaces the plan, the human approves, the client resubmits." The TTL is 60–180 s. Nothing in the protocol distinguishes a human-approved resubmission from an agent that parses the dry-run response, extracts `approval_token`, and resubmits 50 ms later — because the model is the client's tool-use planner. This is a known MCP confused-deputy pattern: prompt injection steers the model into "run the diagnostic, then run the stop". The dry-run plan is returned as structured content, which the model reads; nothing in the plan prevents the model from treating `approval_token` as "just another field to echo back".

**Evidence from the plan:** v2:§7.4 L1184–1196; v2:§10.5 L1606–1623; v2:§13 item 5 L1750–1754.

**Recommended change:** Add to §7.4: (1) a minimum 2-second delay between approval-token issuance and consumption; (2) a separate server-side approval channel at `POST /mcp/approvals/{token}` that the MCP client must hit outside the `tools/call` flow, such that the LLM cannot emit it as a synthetic tool call; (3) a per-actor rate limit on mutating tool calls of 1-per-N-seconds. Document explicitly in §10 that "the server assumes the MCP client cannot auto-approve because approval is a separate endpoint — not because the client is honest". Reference: OWASP LLM Top 10 2025 LLM06 (excessive agency), NIST AI 600-1 §3.3.

---

### F8. `MODAL_MCP_DEBUG_EXPOSE_IDS` and `MODAL_MCP_DEBUG` enforcement is "checked at call time" — misconfigured operator flips both on without a startup refusal

**Severity:** Major

**Why it matters:** §5.6 L904–908 says `MODAL_MCP_DEBUG_EXPOSE_IDS` "is honoured only in `self_hosted_byo_token` mode — ignored in any hosted credential mode". The plan never states **where** this check happens. Three concrete hazards: (1) if the check is inside the ref-minting path and reads `settings.auth_mode`, a hosted server with a misconfigured `MODAL_MCP_AUTH_MODE` will happily leak native IDs; (2) if `auth_mode` transitions at runtime, there is a TOCTOU race; (3) the operator-footgun case: an operator sets both simultaneously, expecting the server to loudly refuse to start. The spec says it's "ignored" — silently ignoring is the wrong posture.

**Evidence from the plan:** v2:§5.6 L904–908; v2:§11 L1649, L1651; v2:§3.2 L174–200.

**Recommended change:** Specify in §11 that (a) `MODAL_MCP_DEBUG_EXPOSE_IDS=true` with any `MODAL_MCP_AUTH_MODE` other than `self_hosted_byo_token` causes the server to **refuse to start** with a fatal error; (b) the check lives at `Settings` validation (Pydantic `model_validator`), not at emission time; (c) the same rule applies to `MODAL_MCP_DEBUG`; (d) `settings.auth_mode` is frozen at startup. Add §9.1 test: "start with hosted mode + `MODAL_MCP_DEBUG_EXPOSE_IDS=true` → process exits non-zero with `CONFIG_CONFLICT`".

---

### F9. `OriginGuard` middleware ordering and DNS-rebinding story is wrong on the facts

**Severity:** Major

**Why it matters:** §3.2 and §3.4 draw `OriginGuard` "outside" FastMCP's StreamableHTTP app in the Starlette middleware stack, but the spec never states this is a load-bearing invariant, never forbids inserting anything between `OriginGuard` and FastMCP's ASGI app, and never handles the case of FastMCP installing its own middleware.

Worse, the DNS-rebinding story is wrong on the facts. Binding to `127.0.0.1` does **not** prevent DNS rebinding. §3.4 L311 says "bind to `127.0.0.1` in local mode; in remote mode the reverse proxy is expected to terminate TLS and set a canonical `Origin` header which `OriginGuard` checks against `MODAL_MCP_ALLOWED_ORIGINS`." Two issues: (a) "reverse proxy sets Origin" is backwards — reverse proxies normally pass `Origin` through; (b) allowing an operator to configure `MODAL_MCP_ALLOWED_ORIGINS` without a deny-on-missing default is a footgun.

**Evidence from the plan:** v2:§3.2 L166–200; v2:§3.4 L285–293 and L307–311; v2:§11 L1638–1639; v2:§10.1 L1445–1448.

**Recommended change:** In §3.4, state as an invariant: "`OriginGuard` is the first Starlette middleware in the stack; nothing may be installed before it. FastMCP's own middleware runs after `OriginGuard`." Name a test in §9.1 that asserts middleware ordering by reflection. Additionally: (1) require `MODAL_MCP_ALLOWED_ORIGINS` to be non-empty — refuse to start if empty, do not default-allow; (2) for the `127.0.0.1` local binding, state explicitly that the `Host` header must also be validated, because a rebinding attacker's request will have `Host: attacker.com` even on the loopback socket; (3) reject any request whose `Origin` scheme is `null` or missing; (4) fix the "reverse proxy sets Origin" prose. Reference: OWASP ASVS 13.1.1, OWASP Cheat Sheet "DNS Rebinding".

---

### F10. Per-session rate limiting is keyed on `Mcp-Session-Id` — trivially rotated by the client, bypass is guaranteed

**Severity:** Major

**Why it matters:** §10.1 L1462–1464 says "Global + per-session + per-tool token bucket. Conservative defaults (`MODAL_MCP_RATE_LIMIT_RPS=5` per session by default)." The per-session key is `Mcp-Session-Id` — client-generated, client-rotated, unauthenticated. Any abusive client can call `initialize` repeatedly to mint fresh session IDs and bypass the per-session limit. For the approval-gate abuse case in F7, the attacker wants a low ceiling on *mutating* calls, which per-session rate limiting does not provide when session IDs are cheap.

**Evidence from the plan:** v2:§10.1 L1462–1464; v2:§11 L1646; v2:§7.3 L1153; v2:§7.5 L1200–1204.

**Recommended change:** Specify the rate-limit key hierarchy with a concrete fallback chain: `auth_session_id → actor_principal → remote_address → global`. Reject any approach where the limit key is purely client-supplied and unauthenticated. For self-hosted single-operator mode, state that rate limiting is against `remote_address + tool_name` and the global bucket. For hosted modes, require the auth-session ID to be present for all non-`initialize` calls and use it as the rate-limit key. Add a hard cap on `initialize` rate per remote address. Tie mutating-tool rate limits to `actor_principal` with a default of 1 per 30 seconds.

---

### F11. Expert sandbox §10.4 is intent, not implementation — `preexec_fn`, env scrubbing, cgroup owner, and FD inheritance are unspecified or wrong

**Severity:** Major

**Why it matters:** §10.4 L1529–1562 lists six constraints for the v3 Expert sandbox. Concrete problems:

1. **`preexec_fn=set_limits` after `subprocess.Popen`.** `preexec_fn` runs in the child after `fork()` and before `exec()`. In a multi-threaded Python server (FastMCP + asyncio + thread pools), `fork()` can leave the child in an inconsistent state because inherited locks may be held by other threads. `preexec_fn` is also not safe for Python allocation. The plan does not acknowledge this.
2. **Env-var scrubbing.** `subprocess.Popen(env=None)` passes the full parent env. `PYTHONPATH`, `PYTHONSTARTUP`, `LD_PRELOAD`, `LD_AUDIT`, `LD_LIBRARY_PATH`, `PYTHONINSPECT`, `PYTHONBREAKPOINT`, `BROWSER` can all inject code into the child *before* the DSL parser runs. If the parent process has Modal tokens in `os.environ` (which is the whole point of `self_hosted_byo_token` mode), those tokens are inherited by the Expert child by default — contradicting the "only capability is internal tool RPC bridge" claim.
3. **Cgroup owner.** Creating a cgroup requires the server process to own a cgroup subtree, which typically requires `--privileged` or a delegated cgroup. The plan's hosting model is "Python 3.12 slim base + docker-compose" — no privileged flag, no delegated subtree.
4. **FD inheritance for the RPC bridge.** `close_fds=True` is default in 3.2+, but the spec wants to pass *one* specific FD. That requires `pass_fds=[sock.fileno()]`, and the child must know which FD number to read. The spec does not say how the FD number is communicated, whether the socket is SOCK_SEQPACKET or SOCK_STREAM, or whether messages are framed.
5. **`/proc` masking.** Not mentioned. A sandboxed child can read `/proc/self/environ` (credential exfil if env scrubbing is imperfect), `/proc/self/maps` (ASLR bypass), `/proc/*/cmdline` (parent's argv).
6. **RPC bridge contract.** §10.4 claims the bridge applies full policy engine, but the plan gives no contract — no auth, no nonce, no request shape.

**Evidence from the plan:** v2:§10.4 L1529–1562; v2:§13 item 4 L1744–1749; v2:§4 L418.

**Recommended change:** Rewrite §10.4 as an implementation contract:
1. Replace `subprocess.Popen(preexec_fn=...)` with `os.posix_spawn` + a C helper, or spawn a long-lived sandbox-runner subprocess at server startup (before any threads exist) as a fork-server.
2. Pass `env=` a strict whitelist: `{"PATH": "/usr/bin", "LANG": "C.UTF-8", "HOME": "/tmp/expert-home"}` — **nothing else**, explicitly not any `MODAL_*`, `LD_*`, `PYTHON*` variable. State as test invariant in §9.1.
3. Document the container-image story for cgroup delegation: privileged init container, systemd `Delegate=yes`, or drop cgroup claim and use `rlimits` only.
4. Name the RPC bridge protocol: `SOCK_SEQPACKET` Unix socket, length-prefixed messagepack frames, per-request nonce signed with `MODAL_MCP_SIGNING_KEY`, policy engine runs on every message. FD number passed via dedicated env var in the child's whitelisted env.
5. Mandate a bind-mount mask of `/proc/self/environ`, `/proc/self/maps`, `/proc/*/cmdline` via `mount --bind /dev/null <path>` inside the child's mount namespace.
6. Gate the entire Expert toolset behind a startup check that refuses to enable `expert` on unsupported hosts.

Reference: OWASP ASVS 14.4.7, CPython `subprocess` docs on `fork()` safety, `man 7 cgroup_namespaces`, `man 2 unshare`.

---

### F12. Cross-environment ref replay is possible because `MODAL_MCP_ALLOW_CROSS_ENV=true` removes the only enforcement layer

**Severity:** Major

**Why it matters:** §7.2 and §10.2 read as if HMAC ref-signing stops cross-env misuse. But §7.2 L1137–1139 says "Cross-environment operations are a privileged, explicit opt-in via `MODAL_MCP_ALLOW_CROSS_ENV=true`". When that flag is on, the scope check at the adapter boundary is skipped. The HMAC signature proves the ref was minted by *this* server, but it does not prove it was minted for the current invocation's target environment — the environment is a field *inside* the signed payload, and the check that "payload.env == requested env" is exactly what `MODAL_MCP_ALLOW_CROSS_ENV=true` turns off. An operator enables it once to unblock a migration and forgets; every subsequent tool call with a stale ref for the wrong environment goes through.

**Evidence from the plan:** v2:§7.2 L1136–1139; v2:§10.2 L1479–1482; v2:§11 L1650.

**Recommended change:** State in §7.2 that `MODAL_MCP_ALLOW_CROSS_ENV=true` does **not** disable the ref-payload-to-target-environment check — it only allows a tool call's target environment to differ from `MODAL_ENVIRONMENT`, not from the ref's signed environment. A ref signed for `env=prod` can *never* be used against `env=dev`, regardless of the flag. Additionally: (a) every audit log entry for a call made under `ALLOW_CROSS_ENV=true` must be tagged `cross_env=true`; (b) the flag's enablement is itself an audit event; (c) it cannot be toggled at runtime. Add §9.1 test.

---

### F13. `MODAL_TOKEN_*` env inheritance into subprocess children, crash dumps, and `/proc/self/environ` is explicitly unaddressed

**Severity:** Major

**Why it matters:** §7.1 and §10 treat env-var tokens as the safe default. The security properties of environment variables as a secret carrier are weaker than the plan acknowledges:

1. **`/proc/self/environ` is world-readable for any process running as the same UID.**
2. **Core dumps contain `environ`.** Unless `prctl(PR_SET_DUMPABLE, 0)` is called at startup, a Python crash (segfault — e.g., in `cryptography`'s C bindings or the gRPC C extension) produces a core file that contains the full env.
3. **`subprocess.Popen` default-inherits env.** The plan's Expert sandbox (F11) does not have the same whitelist as the dead-code CLI fallback.
4. **Crash reporters (Sentry).** Default integration captures `os.environ` into event payload.
5. **`docker inspect`.** The container's env is visible via `docker inspect`.

**Evidence from the plan:** v2:§7.1 L1120; v2:§7.5 L1205–1209; v2:§10 L1436–1438; v2:§10.3 L1508–1512; v2:§11 L1634.

**Recommended change:** Add to §10.3: (1) at startup the server calls `prctl(PR_SET_DUMPABLE, 0)` (Linux) to suppress core dumps; (2) at startup the server reads `MODAL_TOKEN_*` and `MODAL_MCP_SIGNING_KEY` into memory via `SecretStr`, then **deletes them from `os.environ`**; (3) support loading credentials from files (`MODAL_TOKEN_ID_FILE`, Kubernetes Secret mount pattern) as the preferred mechanism; (4) forbid Sentry / crash-reporter integration in the default config; (5) audit that no code path ever passes `env=os.environ` to `subprocess.Popen`. Reference: OWASP ASVS 6.4.2.

---

### F14. Redaction categories list is not exhaustive and uses shape-based regex — false negatives on rotated token formats and structured payloads

**Severity:** Minor

**Why it matters:** §10.1 L1454–1461 lists the redaction categories: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, Modal service-user pattern `as-*`, AWS-style `AKIA*`, JWT shape, operator-configured regex. Problems: (a) Modal rotates token formats historically; (b) JWT-shape regex is both overbroad and under-broad — compact JWS with unencoded payload (RFC 7797) won't match; (c) base64-encoded JSON payloads inside logs won't be peeled and matched; (d) the redactor operates on string fields and may not recurse into nested structures.

**Evidence from the plan:** v2:§10.1 L1454–1461.

**Recommended change:** Replace the shape-based regex list with a layered approach: (1) a **known-secret set** populated at startup from the actual configured values — `Settings.__init__` inserts each `SecretStr.get_secret_value()` into a `frozenset[str]` that the redactor scans for and replaces with `***REDACTED***`; (2) shape-based regexes as a defence-in-depth second layer; (3) recursive descent into nested dicts/lists/tuples explicitly specified; (4) encoded-payload step. Add §9.1 test matrix covering top-level / nested / list / base64 / exception-formatted variants.

---

### F15. The `_cli_fallback.py` "dead code" is a reachable attack surface — tests enforce its safety rules but nothing enforces it stays dead

**Severity:** Minor

**Why it matters:** §6.3 describes `adapters/_cli_fallback.py` as "ships as dead code behind a documented emergency flag". Three issues: (a) "emergency flag" is never named; (b) the policy engine's "tools → adapter" path is not specified to run after adapter selection; (c) dead code rots: nothing enforces the fallback module is *unreachable* from the default build.

**Evidence from the plan:** v2:§6.3 L1069–1073; v2:§10.3 L1501–1514; v2:§13 L1730–1732.

**Recommended change:** In §6.3, name the activation env var (`MODAL_MCP_CLI_FALLBACK=true`), forbid enabling it in any hosted mode via the same `Settings`-level refusal as F8, and require a startup log line at WARNING level. Add a CI check that greps the default adapter import tree for any reference to `_cli_fallback` and fails if found.

---

### F16. No threat model entry for tool-metadata poisoning via `modal_discovery_server_info`

**Severity:** Minor

**Why it matters:** §5.1 L575–579 positions `modal_discovery_server_info` as the safety banner the client calls first. In an MCP client, this response is read by the LLM as part of tool-selection reasoning. An attacker who can influence the operator config (e.g., via a supply-chain PR or an unvetted `docker-compose.yml` template) can inject prompt-steering strings into the LLM's context. This is the exact MCP tool-metadata-poisoning threat.

**Evidence from the plan:** v2:§5.1 L575–579; v2:§10.2 L1471–1498.

**Recommended change:** Add to §10.2 a "tool-metadata poisoning" threat entry. Specify that `modal_discovery_server_info` emits only fixed-schema fields with allowlisted values (enum for mode, boolean for read-only, list for toolsets), and explicitly no operator-supplied free-text fields. Reference: INVARIANT Labs "MCP tool poisoning", OWASP LLM Top 10 2025 LLM01.

---

**Severity counts.** Blockers: 1 (F2). Criticals: 3 (F1, F4, F6). Majors: 9 (F3, F5, F7, F8, F9, F10, F11, F12, F13). Minors: 3 (F14, F15, F16). Sixteen findings, four in the top two severities. The plan's security prose is competent but the document is at the wrong level of specificity for any of its cryptographic or sandboxing claims to be implemented correctly without this round of tightening.
