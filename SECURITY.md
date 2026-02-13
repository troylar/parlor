# Security Policy

Parlor is designed with security as a core principle. This document outlines the security posture, threat model, and compliance status.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | Yes        |
| < 0.3   | No         |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue
2. Email security concerns to the repository maintainer
3. Include steps to reproduce, impact assessment, and any suggested fixes
4. Allow 90 days for a fix before public disclosure

## Threat Model

Parlor is a **personal, single-user application** intended to run on a user's local machine. The threat model reflects this:

| Threat | Mitigation | Status |
|--------|-----------|--------|
| Unauthorized local access | Bearer token auth via HttpOnly cookie | Implemented |
| Session hijacking | Session expiry (12h absolute, 30min idle) | Implemented |
| Cross-site request forgery (CSRF) | Double-submit cookie pattern | Implemented |
| Cross-site scripting (XSS) | Content Security Policy, DOMPurify sanitization | Implemented |
| Clickjacking | X-Frame-Options: DENY, frame-ancestors 'none' | Implemented |
| MIME sniffing | X-Content-Type-Options: nosniff | Implemented |
| File upload abuse | MIME allowlist + magic-byte verification (filetype) | Implemented |
| Path traversal (attachments) | Filename sanitization + resolved path validation | Implemented |
| Malicious MCP servers (SSRF) | DNS resolution validation, private IP rejection | Implemented |
| MCP tool injection | Shell metacharacter rejection in tool arguments | Implemented |
| Dependency vulnerabilities | pip-audit in CI, Dependabot enabled | Implemented |
| Request flooding | Per-IP rate limiting (120 req/min) | Implemented |
| Oversized payloads | 15 MB request body limit, 10 MB attachment limit | Implemented |
| Information leakage | Generic error messages, no stack traces in responses | Implemented |
| Sensitive data in cache | Cache-Control: no-store on all API responses | Implemented |

## OWASP ASVS v4.0 Compliance

Parlor targets **ASVS Level 1** (Opportunistic) compliance for a single-user local application.

### V2: Authentication

| Requirement | Status | Notes |
|------------|--------|-------|
| V2.1 Password security | N/A | Token-based auth, no passwords |
| V2.5 Credential recovery | N/A | Single-user, local token |
| V2.7 Session binding | Pass | HttpOnly, Secure, SameSite=Strict cookies |
| V2.8 Session expiry | Pass | 12h absolute + 30min idle timeout |
| V2.10 Logout invalidation | Pass | Cookie deletion on POST /api/logout |

### V3: Session Management

| Requirement | Status | Notes |
|------------|--------|-------|
| V3.1 Session token entropy | Pass | 32-byte cryptographic random token (secrets.token_urlsafe) |
| V3.2 Cookie security flags | Pass | HttpOnly, Secure (non-localhost), SameSite=Strict |
| V3.4 Session timeout | Pass | Absolute + idle timeouts enforced |
| V3.5 Server-side validation | Pass | Token hash comparison (hmac.compare_digest) |

### V4: Access Control

| Requirement | Status | Notes |
|------------|--------|-------|
| V4.1 Authorization checks | Pass | All /api/ endpoints require valid token |
| V4.2 CSRF protection | Pass | Double-submit cookie on state-changing methods |

### V5: Validation, Sanitization, Encoding

| Requirement | Status | Notes |
|------------|--------|-------|
| V5.1 Input validation | Pass | Pydantic models, max_length constraints |
| V5.2 Sanitization | Pass | DOMPurify for HTML, filename sanitization |
| V5.3 Output encoding | Pass | JSON serialization, CSP headers |
| V5.5 File upload validation | Pass | MIME allowlist + magic-byte verification |

### V7: Error Handling and Logging

| Requirement | Status | Notes |
|------------|--------|-------|
| V7.1 Generic error messages | Pass | No stack traces or internal details exposed |
| V7.2 Security event logging | Pass | Dedicated parlor.security logger |

### V8: Data Protection

| Requirement | Status | Notes |
|------------|--------|-------|
| V8.1 Sensitive data in transit | Pass | Secure cookie flag, HTTPS support |
| V8.2 Anti-caching | Pass | Cache-Control: no-store on API responses |
| V8.3 Sensitive data in responses | Pass | API key presence shown as boolean, never exposed |

### V9: Communication

| Requirement | Status | Notes |
|------------|--------|-------|
| V9.1 TLS for external calls | Partial | Configurable via verify_ssl (default: enabled) |

### V13: API Security

| Requirement | Status | Notes |
|------------|--------|-------|
| V13.1 Rate limiting | Pass | 120 requests/minute per IP |
| V13.2 Request size limits | Pass | 15 MB body limit enforced via middleware |
| V13.3 Content-Type validation | Pass | Explicit content-type handling in chat endpoint |

### V14: Configuration

| Requirement | Status | Notes |
|------------|--------|-------|
| V14.1 Security headers | Pass | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy |
| V14.2 Dependency management | Pass | pip-audit in CI, Dependabot for automated updates |
| V14.4 HTTP security headers | Pass | Full suite applied via SecurityHeadersMiddleware |

## Security Architecture

```
Browser ──HTTPS──▶ Parlor (FastAPI)
                      │
                      ├── BearerTokenMiddleware (auth)
                      ├── RateLimitMiddleware (120/min)
                      ├── MaxBodySizeMiddleware (15 MB)
                      ├── SecurityHeadersMiddleware (CSP, etc.)
                      ├── CSRF validation (double-submit)
                      │
                      ├──▶ SQLite (local, file-based)
                      ├──▶ AI Backend (OpenAI-compatible)
                      └──▶ MCP Servers (validated, SSRF-protected)
```

## Configuration Hardening

For production-like deployments (non-localhost):

- `Secure` flag automatically set on cookies
- SSL verification enabled by default for AI backend
- All security headers enforced on every response
- Rate limiting active on all endpoints

## Dependency Security

- **Automated scanning**: `pip-audit` runs in CI on every push and PR
- **Automated updates**: Dependabot monitors pip and GitHub Actions dependencies weekly
- **Minimal dependencies**: Only essential packages included to reduce attack surface
