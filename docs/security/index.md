# Security

Anteroom is built to **OWASP ASVS Level 2** standards for enterprise teams behind firewalls who need agentic AI without compromising security posture. Security is structural — baked into the architecture, not bolted on as an afterthought.

## Security Architecture

Every request passes through a layered middleware stack before reaching application logic:

```
Browser / CLI
     │
     ▼
  TLS (ECDSA P-256, optional)
     │
     ▼
  IP Allowlist (CIDR / exact, fails closed)
     │
     ▼
  Authentication (Ed25519-derived HMAC-SHA256 token)
     │
     ▼
  Session Validation (idle + absolute timeouts, concurrent limits)
     │
     ▼
  CSRF (double-submit cookie + origin validation)
     │
     ▼
  Rate Limiting (120 req/min per IP)
     │
     ▼
  Body Size Limit (15 MB)
     │
     ▼
  Security Headers (CSP, HSTS, X-Frame-Options, etc.)
     │
     ▼
  Router → Agent Loop
                │
                ▼
          Tool Safety Gate
          (tier check → hard-block → rate limit → approval)
                │
                ▼
          Tool Execution
                │
                ▼
          DLP Scan (redact / block / warn)
                │
                ▼
          Audit Log (HMAC-chained JSONL)
```

## Feature Overview

| Feature | Config Section | Key Highlights |
|---|---|---|
| [Authentication](authentication.md) | `session.*` | Ed25519 identity, HMAC-SHA256 stable tokens, HttpOnly cookies |
| [Session Management](authentication.md#session-stores) | `session.*` | Memory or SQLite stores, idle/absolute timeouts, concurrent limits |
| [IP Allowlisting](authentication.md#ip-allowlisting) | `session.allowed_ips` | CIDR and exact IP, IPv4/IPv6, fails closed |
| [Tool Safety Gate](tool-safety.md) | `safety.*` | 4 risk tiers, 4 approval modes, 3 permission scopes |
| [Bash Sandboxing](bash-sandboxing.md) | `safety.bash.*` | Timeout, output limits, path/command blocking, network/package restrictions |
| [Hard-Block Patterns](tool-safety.md#hard-block-patterns) | — | 16 catastrophic command patterns, unconditionally blocked |
| [Prompt Injection Defense](prompt-injection-defense.md) | `mcp_servers[].trust_level` | Trust classification, defensive XML envelopes, tag breakout prevention |
| [Audit Log](audit-log.md) | `audit.*` | HMAC-SHA256 chained JSONL, daily rotation, SIEM integration |
| [Tool Rate Limiting](tool-safety.md#tool-rate-limiting) | `safety.tool_rate_limit.*` | Per-minute, per-conversation, consecutive failure caps |
| [Token Budgets](hardening.md#token-budget-enforcement) | `cli.usage.budgets.*` | Per-request, per-conversation, per-day limits (denial-of-wallet prevention) |
| [Read-Only Mode](hardening.md#read-only-mode) | `safety.read_only` | Restrict AI to READ-tier tools only |
| [Sub-Agent Isolation](hardening.md#sub-agent-safety) | `safety.subagent.*` | Concurrency, depth, iteration, timeout, and output limits |
| [Team Config Enforcement](hardening.md#team-config-enforcement) | Team config `enforce` list | Lock security settings across team members |
| [MCP Tool Safety](hardening.md#mcp-tool-safety) | `mcp_servers[].*` | SSRF protection, metachar rejection, tool filtering, trust levels |
| [Data Loss Prevention](dlp.md) | `safety.dlp.*` | Regex-based PII scanning, redact/block/warn actions, custom patterns |

## Threat Model

Anteroom is a **single-user, local-first application** intended to run on a user's machine or behind a corporate firewall. The threat model accounts for:

| Threat | Primary Mitigation | Details |
|---|---|---|
| Unauthorized local access | Ed25519-derived HMAC-SHA256 auth token | [Authentication](authentication.md) |
| Session hijacking | HttpOnly/Secure/SameSite cookies, idle + absolute timeouts | [Authentication](authentication.md#cookie-configuration) |
| Cross-site request forgery | Double-submit cookie + origin validation | [Authentication](authentication.md#csrf-protection) |
| Cross-site scripting | Content Security Policy, DOMPurify, SRI | [Hardening](hardening.md#security-headers) |
| Clickjacking | X-Frame-Options DENY, frame-ancestors 'none' | [Hardening](hardening.md#security-headers) |
| Destructive AI tool use | 4 risk tiers, approval gates, hard-block patterns | [Tool Safety](tool-safety.md) |
| Bash command injection | Hard-block patterns, sandbox controls, path blocking | [Bash Sandboxing](bash-sandboxing.md) |
| Indirect prompt injection | Trust classification, defensive envelopes, tag sanitization | [Prompt Injection Defense](prompt-injection-defense.md) |
| Malicious MCP servers (SSRF) | DNS resolution validation, private IP rejection | [Hardening](hardening.md#mcp-tool-safety) |
| MCP tool injection | Shell metacharacter rejection, tool filtering | [Hardening](hardening.md#mcp-tool-safety) |
| Runaway AI costs | Token budgets (per-request, per-conversation, per-day) | [Hardening](hardening.md#token-budget-enforcement) |
| Audit tampering | HMAC-SHA256 chain, append-only writes, file locking | [Audit Log](audit-log.md) |
| Request flooding | Per-IP rate limiting (120 req/min) | [Hardening](hardening.md#rate-limiting) |
| Token reuse / session proliferation | Concurrent session limits, IP tracking | [Authentication](authentication.md#concurrent-session-limits) |
| Dependency vulnerabilities | pip-audit in CI, Dependabot | [SECURITY.md](https://github.com/troylar/anteroom/blob/main/SECURITY.md) |

## Pages

- [Authentication](authentication.md) — Ed25519 identity, sessions, cookies, CSRF, IP allowlisting
- [Tool Safety](tool-safety.md) — risk tiers, approval modes, hard-block patterns, rate limiting
- [Bash Sandboxing](bash-sandboxing.md) — execution limits, path/command/network controls, OS sandbox
- [Audit Log](audit-log.md) — JSONL format, HMAC chain, rotation, SIEM integration
- [Prompt Injection Defense](prompt-injection-defense.md) — trust classification, defensive envelopes
- [Deployment Hardening](hardening.md) — headers, TLS, budgets, sub-agents, MCP, team enforcement
- [Data Loss Prevention](dlp.md) — sensitive data scanning and redaction
