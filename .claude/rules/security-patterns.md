# Security Patterns (OWASP ASVS Level 2)

> Vision principle: **"Security is structural, not optional."** Enterprise security teams should be able to audit this codebase and find nothing to object to. This isn't a feature — it's the foundation.

These patterns are enforced on ALL code in this repository. Violations must be fixed before committing.

## Forbidden — Never Generate These

- `eval()`, `exec()`, `compile()` with any user-controlled input
- SQL string concatenation or f-strings in queries — use parameterized queries only
- `innerHTML` with unsanitized input in JavaScript
- Hardcoded secrets, API keys, passwords, or tokens
- `subprocess` calls with `shell=True` and user input
- `verify=False` or `rejectUnauthorized: false` in HTTP/TLS clients
- `pickle.loads()` on untrusted data
- Wildcard CORS (`Access-Control-Allow-Origin: *`) on authenticated endpoints
- Logging of passwords, session tokens, API keys, or PII

## Required Patterns

### Database
- ALL queries use parameterized placeholders (`?` for SQLite)
- Column names validated against allowlists (see `storage.py` pattern)
- User input never interpolated into SQL

### Input Validation
- Validate all input server-side at system boundaries
- Allowlists over denylists
- File uploads: validate MIME type, extension, size

### Authentication & Sessions
- Cookies: `HttpOnly`, `Secure`, `SameSite=Strict`
- Session regeneration after auth state changes
- No credentials in URLs or logs

### Tool/Command Safety
- Path traversal prevention (see `tools/security.py`)
- Command injection prevention in bash tool
- Destructive command blocking

### API Endpoints
- Every state-changing endpoint has CSRF protection
- Content-Type validation on JSON endpoints
- Rate limiting applied
- Security headers: CSP, X-Content-Type-Options, X-Frame-Options

## When Adding New Endpoints

1. Add to the appropriate router with auth middleware
2. Validate all input parameters server-side
3. Use parameterized queries for any DB access
4. Add CSRF protection for state-changing operations
5. Add rate limiting if publicly accessible
6. Log security-relevant events (access denied, invalid input)
