# Authentication

Anteroom uses token-based authentication with HttpOnly session cookies.

## Session Management

### Token Generation & Validation

- **Token generation**: Stable HMAC-SHA256 token derived from the Ed25519 identity key (`_derive_auth_token()` in `app.py`). Uses the private key PEM as HMAC key with context string `anteroom-session-v1`, producing a deterministic token that survives server restarts. Falls back to `secrets.token_urlsafe(32)` when no identity is configured
- **Token validation**: Hash-based comparison using `hmac.compare_digest` (timing-safe). Token is hashed with SHA-256 before comparison
- **Session ID derivation**: Deterministic session ID (SHA-256 hash of token, truncated to 32 chars) enables per-token session tracking and concurrent session limits

### Session Persistence

Sessions can be stored in-memory or persisted to SQLite for durability:

- **Session state tracked**: id, user_id, ip_address, created_at, last_activity_at
- **Memory store** — Volatile, suitable for development. Sessions lost on restart
- **SQLite store** — Persistent, survives restarts. Configured via `session.store = "sqlite"` in config.yaml

See [Configuration: Session](../configuration/config-file.md#session) for setup.

### Session Lifecycle

- **Creation**: Session created on first successful auth. Logs creation if `session.log_session_events = true`
- **Activity tracking**: `last_activity_at` updated on every authenticated request (touch operation)
- **Timeout mechanisms**:
  - **Idle timeout** (configurable, default 30 min) — expires if no activity within window
  - **Absolute timeout** (configurable, default 12 hours) — forces re-auth after fixed duration
- **Cleanup**: Expired sessions automatically deleted on next request (cheap for small stores)
- **Logout**: Session deleted on `POST /api/logout`, cookie cleared

### Cookie Configuration

- **Cookie flags**: `HttpOnly` (prevents JavaScript access), `Secure` (HTTPS only, non-localhost), `SameSite=Strict` (prevents cross-site requests)
- **Max-Age**: Set to absolute_timeout value for consistency with session lifetime
- **Path**: `/api/` for session cookie, `/` for CSRF cookie

### Network-Level Controls

IP allowlisting gates access before session validation:

- Configure via `session.allowed_ips` in config.yaml (empty = allow all)
- Supports exact IPs (`192.168.1.5`) and CIDR ranges (`10.0.0.0/8`, IPv6 too)
- Fails closed: invalid/unlisted IPs return 403 Forbidden
- Applied in middleware before session creation/lookup

## CSRF Protection

Anteroom uses the double-submit cookie pattern:

- A CSRF token is generated per session
- The token is included in a cookie and must be sent as a header on state-changing requests
- All `POST`, `PATCH`, `PUT`, and `DELETE` requests are validated
- Token comparison uses HMAC-SHA256 for timing-safe verification

## How It Works

On first visit, Anteroom generates a session token and sets it as an HttpOnly cookie. All subsequent API requests must include this cookie. For state-changing operations, the CSRF token must also be included as a request header.

No passwords are involved --- this is a single-user local application. The session token prevents unauthorized access from other processes or users on the same machine.

## Session Expiry Handling

When an API request returns 401 (session expired or invalid token), the browser redirects to `/` to obtain a fresh session cookie. To prevent infinite reload loops (e.g., if the server is unreachable or the token is permanently invalid), the client tracks redirect timestamps in `sessionStorage`. If two 401 redirects occur within 5 seconds, a fixed banner is shown instead of reloading again, instructing the user to manually refresh.
