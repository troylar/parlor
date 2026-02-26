# Hardening

Security headers and protections applied to every response.

## Security Headers

| Header | Value | Purpose |
|---|---|---|
| `Content-Security-Policy` | `script-src 'self'; frame-ancestors 'none'` | Prevents XSS and clickjacking |
| `X-Frame-Options` | `DENY` | Prevents clickjacking |
| `X-Content-Type-Options` | `nosniff` | Prevents MIME sniffing |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Controls referrer information |
| `Permissions-Policy` | Restrictive | Limits browser feature access |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Enforces HTTPS (when TLS enabled) |
| `Cache-Control` | `no-store` | Prevents caching of API responses |

## Subresource Integrity (SRI)

All vendor scripts (marked.js, highlight.js, KaTeX, DOMPurify) include SHA-384 hashes. If a CDN or file is tampered with, the browser refuses to execute it.

## Rate Limiting

- **120 requests per minute** per IP address
- Uses LRU eviction for the IP tracking map
- Applied to all endpoints

## Request Size Limits

- **15 MB** maximum request body size
- **10 MB** maximum per file attachment
- **10 files** maximum per message

## CORS

- Locked to the configured origin
- Explicit method allowlist (`GET`, `POST`, `PATCH`, `PUT`, `DELETE`)
- Explicit header allowlist
- No wildcard origins

## API Surface

- OpenAPI/Swagger documentation is disabled in production
- Server version headers (`Server`, `X-Powered-By`) are not exposed

## File Upload Security

- MIME type allowlist with magic-byte verification (using `filetype` library)
- Filenames sanitized: path components stripped, special characters replaced
- Non-image files force-download (never rendered in-browser)
- Attachments stored outside webroot with path traversal prevention

## Database Security

- Column-allowlisted SQL builder prevents injection
- All queries use parameterized statements
- Database files created with `0600` permissions (owner-only)
- Data directory created with `0700` permissions
- UUID validation on all ID parameters

## Read-Only Mode

For untrusted or shared environments, enable read-only mode to restrict the AI to read-only operations:

```yaml
safety:
  read_only: true
```

When enabled:
- Only READ-tier tools are available (read_file, glob_files, grep, introspect)
- All WRITE, EXECUTE, and DESTRUCTIVE tools are blocked
- AI cannot modify files, run bash commands, or create canvases
- Can be toggled at runtime: `aroom chat --read-only` or `AI_CHAT_READ_ONLY=true`

Use this in shared environments, demo settings, or when auditing AI behavior in a sandbox.

## Token Budget Enforcement (Denial-of-Wallet Prevention)

Enterprise teams can enforce token consumption limits to control API costs and prevent runaway spending:

- **Per-request limit** — Blocks individual requests exceeding the limit
- **Per-conversation limit** — Caps total token consumption within a conversation thread
- **Per-day limit** — Caps total daily consumption across all conversations
- **Warning threshold** — Emits warnings when approaching limits (configurable percentage)
- **Exceeding actions** — Administrators choose: `block` (reject requests) or `warn` (allow but notify)

See [Configuration: Token Budgets](../configuration/config-file.md#usagebudgets) for setup details.
