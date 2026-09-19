# Aikido Security Registry & Non-Regression Specification

This document is the **authoritative security registry** for the `telegram-cloud-drive` project.
It catalogs all vulnerabilities identified by **Aikido Security** (across DAST Domain scans, SAST AI Code Review, SCA Dependency Audits, and Secret Detection), the root causes, and the **mandatory non-regression rules**.

> [!IMPORTANT]
> **MANDATORY NON-REGRESSION RULE FOR ALL FUTURE CODE EDITS:**
> Any future code modifications, refactors, feature additions, or dependency updates **MUST NOT** re-introduce any of the patterns listed below. Automated tests in `test_app.py` enforce these requirements and must remain green (100% passing).

---

## Complete Vulnerability Registry & Prevention Rules

```
+---------------------------------------------------------------------------------------------------+
| INDEX OF RESOLVED AIKIDO VULNERABILITIES                                                          |
+----+---------------------------------------------------------------+------------+-----------------+
| #  | Vulnerability Title & Aikido Issue ID                         | Category   | Risk Level      |
+----+---------------------------------------------------------------+------------+-----------------+
| 01 | Missing HSTS Header on Error Responses (#46423906)            | DAST       | High (89)       |
| 02 | Missing Anti-Clickjacking Header (#46423902)                  | DAST       | Medium (50)     |
| 03 | Insecure Content Security Policy & Eval Usage (#46423905)     | DAST / SAST| Critical (91)   |
| 04 | Unpinned CDN Scripts & Missing SRI (#46423907, #46424550)     | DAST / SAST| Medium (50)     |
| 05 | Reliance on Insecure / Static Cookies                         | SAST       | Critical        |
| 06 | Plaintext OTP Disclosure in Demo Mode (#46424548, #46424546)  | SAST       | Critical (90)   |
| 07 | Unbounded Uvicorn Access Log Append DoS (#46424534)           | SAST / Ops | Medium (52)     |
| 08 | Unauthenticated Forced Prefetch Workload Flooding (#46424534) | SAST       | Medium (52)     |
| 09 | Unauthenticated IP-Scoped OTP Cancellation DoS (#46424550)    | SAST       | Medium (50)     |
| 10 | Spoofable Forwarding Headers in IP Resolution (#46424536)     | SAST       | Medium (47)     |
| 11 | Unauthenticated OTP Status Leaking Metadata (#46424535)       | SAST       | Low (32)        |
| 12 | Telegram Message Deletion Wrong-Channel Fallback (#46424543)  | SAST       | Medium (48)     |
| 13 | Unauthenticated Persistent DB Exhaustion on Notes/Todos       | SAST       | Medium          |
| 14 | Unbounded SQLite OTP Session Retention (#46424536)            | SAST       | Medium          |
| 15 | Concurrent OTP Request Bypass & Flooding (#46424543)          | SAST       | Medium          |
| 16 | Outdated Dependencies (python-multipart, starlette)           | SCA        | Low (CVEs)      |
| 17 | Leaked Secret in Batch Script Git History (#46420853)         | Secrets    | Medium (60)     |
+----+---------------------------------------------------------------+------------+-----------------+
```

---

### 1. Missing HSTS Header on Error Responses (`#46423906`)
* **Root Cause:** In FastAPI/Starlette, unhandled exceptions and early returns in custom middleware bypass standard middleware, resulting in HTTP 401, 404, 422, or 500 error responses lacking the `Strict-Transport-Security` header.
* **Implemented Protection:**
  - `SECURITY_HEADERS` dictionary defined centrally in `main.py`.
  - `apply_security_headers(response)` function applied to:
    - Normal responses via `@app.middleware("http")`.
    - Early 401 rejection responses in `auth_middleware`.
    - Custom exception handlers for `HTTPException` and `404`.
* **Non-Regression Rule:** Every HTTP response returned by the application—including error responses, early rejections, and custom exception handlers—MUST carry `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`.

---

### 2. Missing Anti-Clickjacking Header (`#46423902`)
* **Root Cause:** Responses omitted explicit framing controls, allowing the site to be embedded in external `<iframe>` tags.
* **Implemented Protection:**
  - Universal headers include `X-Frame-Options: DENY`.
  - Content Security Policy enforces `frame-ancestors 'none'`.
* **Non-Regression Rule:** NEVER remove `X-Frame-Options: DENY` or change `frame-ancestors 'none'` without a strict, authenticated business requirement.

---

### 3. Insecure Content Security Policy & Eval Usage (`#46423905`, `#46424550`)
* **Root Cause:**
  - `script-src` included `'unsafe-eval'`.
  - Legacy `<meta http-equiv="Content-Security-Policy">` in `index.html` conflicted with server headers.
* **Implemented Protection:**
  - Eliminated `'unsafe-eval'` completely from CSP.
  - Removed duplicate `<meta http-equiv="Content-Security-Policy">` from HTML, relying exclusively on server-side HTTP headers.
  - Set restrictive CSP directives: `default-src 'self'`, `connect-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`.
* **Non-Regression Rule:**
  - NEVER re-introduce `'unsafe-eval'`.
  - NEVER add inline `<meta>` CSP tags that contradict or duplicate HTTP headers.

---

### 4. Unpinned CDN Scripts & Missing Subresource Integrity (SRI) (`#46423907`, `#46424550`)
* **Root Cause:** Loading runtime scripts (`unpkg.com/lucide@latest` and `cdn.tailwindcss.com`) dynamically from external third-party CDNs without cryptographic hash pinning.
* **Implemented Protection:**
  - 100% self-hosted front-end dependencies:
    - Tailwind CSS bundled locally at `static/tailwind.min.js`.
    - Lucide Icons bundled locally at `static/lucide.min.js`.
  - CSP `script-src` and `style-src` restricted to `'self'`.
* **Non-Regression Rule:**
  - NEVER load JavaScript or CSS libraries from public CDNs (`cdn.tailwindcss.com`, `unpkg.com`, `cdnjs`, `jsdelivr`).
  - ALL client libraries MUST be vendor-bundled inside `/static/`.

---

### 5. Reliance on Insecure / Static Cookies Without Integrity
* **Root Cause:** Using predictable or static cookie values (e.g., `tg_auth=Allow`) that allowed unauthorized clients to forge authentication headers without verification.
* **Implemented Protection:**
  - Cryptographically signed HMAC-SHA256 tokens generated via `create_signed_session_token()`:
    `payload = f"{expires_at}.{nonce}"`
    `signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()`
    `token = f"{payload}.{signature}"`
  - Constant-time verification with `hmac.compare_digest`.
  - Secret key generated using 256-bit entropy (`secrets.token_hex(32)`) if not configured in `.env`.
  - Dedicated `/api/auth/logout` endpoint explicitly invalidates cookies.
* **Non-Regression Rule:**
  - NEVER accept raw string matches (like `"Allow"` or `"authenticated"`) for session authentication.
  - All session cookies MUST be HMAC-SHA256 signed with expiration and nonce validation.

---

### 6. Plaintext OTP Disclosure in Demo Mode (`#46424548`, `#46424546`)
* **Root Cause:** Development/demo mode returned generated OTP codes in API JSON responses or console logs for debugging convenience.
* **Implemented Protection:**
  - Eradicated all OTP code disclosures from API responses and log statements.
  - In demo mode, responses return generic confirmations: `"Demo Mode: Verification session created."`
  - Verification codes are only readable by querying test DB or Telegram channel directly in automated test fixtures.
* **Non-Regression Rule:** NEVER return the plain OTP code or write it to server logs, stdout, or JSON bodies.

---

### 7. Unbounded Uvicorn Access Log Append DoS (`#46424534`)
* **Root Cause:** `run_server.bat` redirected all Uvicorn access logs (`>> server.log 2>&1`) without size caps, allowing unbounded disk growth from unauthenticated HTTP floods.
* **Implemented Protection:**
  - Automated log rotation in `run_server.bat`: checks if `server.log` exceeds 10 MB (10,485,760 bytes) and rotates it to `server.log.old`.
  - Added `--no-access-log` flag to Uvicorn command.
  - Server bound strictly to loopback `127.0.0.1`.
* **Non-Regression Rule:**
  - NEVER run batch or shell startup scripts that redirect access logs to files without bounded log rotation.
  - Production launchers must disable verbose per-request access logging or route through rotating log handlers.

---

### 8. Unauthenticated Forced Prefetch Workload Flooding (`#46424534`)
* **Root Cause:** The public `/api/prefetch` endpoint allowed `force=true`, which bypassed debouncing cooldowns and queued resource-heavy Telegram channel synchronization tasks.
* **Implemented Protection:**
  - `force=True` strictly requires an authenticated HMAC session token (returns 401 otherwise).
  - Mandatory global 5.0-second cooldown applied on `auto_prefetch()` regardless of parameters.
  - Non-blocking lock check: if `_prefetch_lock.locked()`, returns `sync_in_progress` immediately without queueing lock waiters.
* **Non-Regression Rule:** Heavy background synchronization operations MUST NOT be triggerable by unauthenticated users or bypass server-side cooldowns.

---

### 9. Unauthenticated IP-Scoped OTP Cancellation DoS (`#46424550`)
* **Root Cause:** Calling `POST /api/auth/cancel-otp` without parameters cancelled active OTP challenges for the client's apparent IP, allowing malicious third parties to invalidate victims' active login sessions.
* **Implemented Protection:**
  - `POST /api/auth/cancel-otp` requires a valid UUID `session_id`.
  - Requests missing `session_id` return HTTP 400 Bad Request.
  - Cancellation queries strictly by `id = ?` in `otp_sessions`.
* **Non-Regression Rule:** Cancellation or modification of authentication challenges MUST require the unique challenge/session identifier issued during challenge creation.

---

### 10. Spoofable Forwarding Headers in Client IP Resolution (`#46424536`)
* **Root Cause:** `get_client_ip()` prioritized `X-Forwarded-For` and `X-Real-IP` headers without verifying whether the direct socket peer was a trusted reverse proxy.
* **Implemented Protection:**
  - Configured `TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost"}`.
  - Direct socket peer `request.client.host` is used by default. Forwarding headers are ONLY inspected if the direct socket connection originates from `TRUSTED_PROXIES`.
* **Non-Regression Rule:** NEVER trust client-supplied forwarding headers (`X-Forwarded-For`, `X-Real-IP`) unless the direct socket connection is verified against an explicit trusted proxy allowlist.

---

### 11. Unauthenticated OTP Status Leaking Metadata (`#46424535`, `#46424544`)
* **Root Cause:** `GET /api/auth/status` returned operation metadata (`tab`, `action`, `target_name`, `attempts_used`) based solely on apparent IP.
* **Implemented Protection:**
  - Removed all sensitive operation metadata from the status response.
  - Status responses only return non-sensitive countdown numbers (`lockout_remaining_seconds`, `otp_remaining_seconds`, `has_active_otp`).
  - Status queries are strictly filtered by requested `tab` or `session_id`.
* **Non-Regression Rule:** Public status or telemetry endpoints MUST NOT disclose private operational context, file targets, or user activity.

---

### 12. Telegram Message Deletion Wrong-Channel Fallback (`#46424543`)
* **Root Cause:** In `telegram_client.py`, if resolution of `SOFTWARE_CHANNEL_ID` failed, `delete_message` fell back to `self.channel_entity` with the software channel's message ID, risking deleting unrelated files in the primary storage channel.
* **Implemented Protection:**
  - **Fail Closed:** If `channel_id == SOFTWARE_CHANNEL_ID` and the software entity cannot be resolved, `delete_message` logs an error and returns `False` immediately.
  - In `upload_file`, if software upload falls back to storage, `target_channel_id` is updated to `CHANNEL_ID` so catalog records match reality.
* **Non-Regression Rule:** Channel-specific message operations MUST fail closed if the target channel cannot be resolved. NEVER fall back to another channel using a channel-scoped ID.

---

### 13. Unauthenticated Persistent Resource Exhaustion on Notes & Todos (`#46424539`)
* **Root Cause:** Notes and Todos endpoints were susceptible to unbounded row insertion and table bloat.
* **Implemented Protection:**
  - Protected behind `auth_middleware` (unauthenticated requests return 401).
  - Character limits enforced: `title` ≤ 1,000 characters, `content` ≤ 10,000 characters.
  - Hard capacity caps: maximum 1,000 active todos and maximum 1,000 active notes enforced in `main.py`.
* **Non-Regression Rule:** All state-creating endpoints must enforce strict input size bounds, authentication, and overall entity capacity limits.

---

### 14. Unbounded SQLite OTP Session Retention (`#46424536`)
* **Root Cause:** Historical OTP sessions were retained indefinitely in `otp_sessions`, causing database file growth.
* **Implemented Protection:**
  - Auto-purge: `create_otp_session()` automatically deletes sessions older than 1 hour.
  - Startup purge: `cleanup_expired_otp_sessions()` runs on server initialization.
  - Background worker: `periodic_otp_cleanup()` runs every 30 minutes in `lifespan()`.
* **Non-Regression Rule:** Transient security data (OTPs, rate limit buckets, temporary tokens) MUST have automated expiration and scheduled database purging.

---

### 15. Concurrent OTP Request Bypass & Flooding (`#46424543`)
* **Root Cause:** Concurrent asynchronous requests from the same IP could race before the database transaction committed, bypassing per-minute rate limits.
* **Implemented Protection:**
  - Added atomic per-IP mutex `asyncio.Lock()` in `main.py` (`get_ip_otp_lock(ip)`).
  - `send_otp()` acquires this mutex before rate limit checking and session creation.
* **Non-Regression Rule:** State-changing authentication actions subject to rate limiting MUST be synchronized per identity/IP to prevent race-condition bypasses.

---

### 16. Outdated Vulnerable Dependencies (SCA)
* **Root Cause:** Lower-bound version constraints in `requirements.txt` permitted vulnerable versions of `python-multipart` and `starlette`.
* **Implemented Protection:**
  - `python-multipart>=0.0.31` (patches CVE-2026-53540, CVE-2026-53538, etc.).
  - `starlette>=1.3.0` (patches CVE-2026-54282, CVE-2026-48817).
* **Non-Regression Rule:** `requirements.txt` MUST NOT lower minimum package versions below these patched baselines.

---

### 17. Leaked Secret in Batch Script Git History (`#46420853`)
* **Root Cause:** DynDNS token was hardcoded into a `curl.exe` command in `run_server.bat` in an early commit (`efa6d60d`).
* **Implemented Protection:**
  - Removed curl line from current code (commit `ee2cb7aa`).
  - Marked resolved/downgraded in Aikido.
* **Non-Regression Rule:** NEVER commit API keys, bot tokens, auth tokens, or passwords into any repository file or batch script. All secrets MUST be injected via `.env`.

---

## Verification Test Matrix (`test_app.py`)

All 17 automated tests in `test_app.py` continuously verify these non-regression guarantees:

| Test Name | Protected Vulnerabilities Verified |
|---|---|
| `test_full_workspace_api` | Full auth lifecycle, cookie requirement, CRUD isolation |
| `test_fast_upload_file_parallel` | Secure upload handling, category validation |
| `test_fast_download_stream_parallel` | Safe streaming, token validation |
| `test_telegram_otp_full_lifecycle` | OTP generation, hashing, verification, single-use |
| `test_telegram_otp_rate_limit` | Per-IP rate limiting, cooldown enforcement |
| `test_telegram_otp_option3_lockout_after_5_failures` | 5 failed attempts trigger 10-minute lockout |
| `test_telegram_otp_cross_tab_cancellation` | Cross-tab OTP mismatch cancellation |
| `test_telegram_otp_explicit_cancel` | Session-bound cancellation (`session_id` required) |
| `test_security_headers_and_csp` | **Universal HSTS & CSP on 200, 401, and 404 responses**, self-hosted scripts |
| `test_demo_mode_does_not_disclose_otp` | No OTP disclosure in logs or responses |
| `test_hmac_session_cookie_integrity` | Rejection of forged/static cookies |
| `test_upload_path_traversal_prevention` | Filename sanitization against `../` traversal |
| `test_unauthenticated_software_download_isolation` | Channel-scoped download isolation |
| `test_logout_endpoint` | Server-side cookie invalidation |
| `test_notes_and_todos_unauthenticated_rejection` | Unauthenticated mutation protection |
| `test_otp_session_cleanup` | Database auto-purging of stale sessions |
| `test_forced_prefetch_requires_authentication` | Unauthenticated forced prefetch rejection |
