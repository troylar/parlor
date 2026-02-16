# Authentication

Anteroom uses token-based authentication with HttpOnly session cookies.

## Session Management

- **Token generation**: 32-byte cryptographic random token via `secrets.token_urlsafe`
- **Storage**: Token hash stored server-side, compared with `hmac.compare_digest` (timing-safe)
- **Cookie flags**: `HttpOnly`, `Secure` (non-localhost), `SameSite=Strict`
- **Session expiry**: 12-hour absolute timeout, 30-minute idle timeout
- **Logout**: Cookie deletion on `POST /api/logout`

## CSRF Protection

Anteroom uses the double-submit cookie pattern:

- A CSRF token is generated per session
- The token is included in a cookie and must be sent as a header on state-changing requests
- All `POST`, `PATCH`, `PUT`, and `DELETE` requests are validated
- Token comparison uses HMAC-SHA256 for timing-safe verification

## How It Works

On first visit, Anteroom generates a session token and sets it as an HttpOnly cookie. All subsequent API requests must include this cookie. For state-changing operations, the CSRF token must also be included as a request header.

No passwords are involved --- this is a single-user local application. The session token prevents unauthorized access from other processes or users on the same machine.
