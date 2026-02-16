# Security

Anteroom is hardened for use on corporate networks and shared machines. Built to OWASP ASVS Level 1 standards.

## Security Layers

| Layer | What it does |
|---|---|
| **Authentication** | Random session token, HttpOnly cookies, HMAC-SHA256 timing-safe comparison |
| **CSRF** | Per-session tokens validated on all state-changing requests |
| **CSP** | `script-src 'self'`, `frame-ancestors 'none'`, no inline scripts |
| **Security Headers** | X-Frame-Options DENY, X-Content-Type-Options nosniff, strict Referrer-Policy, Permissions-Policy |
| **Database** | Column-allowlisted SQL builder, parameterized queries, `0600` file permissions, path validation |
| **Input Sanitization** | DOMPurify on all rendered HTML, UUID validation on all IDs, filename sanitization |
| **Rate Limiting** | 120 req/min per IP with LRU eviction |
| **Body Size** | 15 MB max request |
| **CORS** | Locked to configured origin, explicit method/header allowlist |
| **File Safety** | MIME type allowlist + magic-byte verification, path traversal prevention, forced download for non-images |
| **MCP Safety** | SSRF protection with DNS resolution, shell metacharacter rejection in tool args |
| **SRI** | SHA-384 hashes on all vendor scripts |
| **API Surface** | OpenAPI/Swagger docs disabled |
| **CLI Safety** | Destructive command confirmation, path validation blocks `/etc/shadow`, `/proc/`, etc. |

## Threat Model

Anteroom is a **personal, single-user application** intended to run on a user's local machine. The threat model accounts for:

- Unauthorized local access
- Cross-site attacks (XSS, CSRF, clickjacking)
- Transport security
- File upload abuse and path traversal
- Malicious MCP servers (SSRF, injection)
- Request flooding and oversized payloads
- Information leakage

See the full [SECURITY.md](https://github.com/troylar/anteroom/blob/main/SECURITY.md) for the complete OWASP ASVS compliance matrix.

## Pages

- [Authentication](authentication.md) --- sessions, cookies, CSRF
- [Tool Safety](tool-safety.md) --- path blocking, command confirmation
- [Hardening](hardening.md) --- CSP, HSTS, rate limiting, SRI
