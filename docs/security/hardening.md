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
