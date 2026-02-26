# Data Loss Prevention

Anteroom includes configurable Data Loss Prevention (DLP) scanning to detect and handle sensitive data patterns in AI responses and optionally in user input.

## Overview

DLP scanning helps prevent accidental disclosure of sensitive information like credit card numbers, Social Security numbers, emails, and other personally identifiable information (PII). The scanner runs via regex pattern matching and applies configurable actions to detected patterns.

## Configuration

Enable DLP in your config file or via environment variables:

```yaml
dlp:
  enabled: true
  scan_output: true          # Scan AI responses (default: true)
  scan_input: false          # Scan user messages (default: false)
  action: redact             # "redact" (default), "block", or "warn"
  redaction_string: "[REDACTED]"
  log_detections: true       # Log matches to security log (default: true)
  # patterns: []             # Loaded from built-in patterns
  # custom_patterns: []      # Add custom regex rules
```

## Built-in Patterns

By default, DLP scans for:

| Pattern | Description | Regex |
|---------|-------------|-------|
| `ssn` | US Social Security Number | `\b\d{3}-\d{2}-\d{4}\b` |
| `credit_card` | Credit/debit card number | `\b(?:\d[ -]*?){13,19}\b` |
| `email` | Email address | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b` |
| `phone_us` | US phone number | `\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b` |
| `iban` | International Bank Account Number | `\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]){0,16}\b` |

## Actions

### Redact (default)

Matches are replaced with the redaction string (default `[REDACTED]`):

```
Input:  "Call me at 555-123-4567 for payment"
Output: "Call me at [REDACTED] for payment"
```

### Block

The AI response is rejected entirely if any matches are found. The error is logged securely:

```
DLP scan blocked response: 3 credit_card patterns detected
```

Use this in high-security environments where any disclosure is unacceptable.

### Warn

Matches are allowed through but logged as security events. Useful for monitoring without enforcement:

```
Input:  "Call me at 555-123-4567"
Output: "Call me at 555-123-4567"
(logged as warning with match details)
```

## Custom Patterns

Add domain-specific patterns in your config:

```yaml
dlp:
  enabled: true
  custom_patterns:
    - name: internal_api_key
      pattern: '\bAK_[A-Za-z0-9]{20,}\b'
      description: Internal API key format
    - name: database_password
      pattern: 'password\s*=\s*["\x27]([^"\']+)["\x27]'
      description: Database password in config
```

## Behavior

- **Streaming**: DLP scans streamed response chunks in real-time. If `action: block`, the stream is halted and the partial response discarded.
- **Reassembly**: After streaming completes, a final DLP pass scans the complete assembled text (catches patterns split across chunks).
- **Input scanning**: If `scan_input: true`, user messages are scanned before sending to the AI. Redacted input is sent to the AI; `block` rejects the message.
- **Logging**: All detections are logged to the security logger (`anteroom.security`) with match counts and rules.
- **Performance**: Regex matching is fast; impact is minimal even with many patterns.

## Events

The agent loop emits two DLP-related events:

- **`dlp_blocked`**: Fired when `action: block` and matches are found. Blocks response/input from proceeding.
- **`dlp_warning`**: Fired when `action: warn` and matches are found. Response/input is allowed.

Both events include:
- `direction`: `"input"` or `"output"`
- `matches`: list of matched rule names (e.g., `["credit_card", "phone_us"]`)

## Security Considerations

DLP patterns are heuristic-based and not foolproof:

- **False positives**: Patterns like email and phone may match valid non-sensitive data (e.g., documentation examples).
- **Evasion**: Sophisticated attackers can obscure patterns (e.g., "555-1-2-3-4-567"). Use `redact` mode for best coverage, or `block` for maximum caution.
- **Custom patterns**: Ensure regex patterns are tested thoroughly before deployment to avoid blocking legitimate content.

## Team Configuration

Lock DLP settings in team config to enforce compliance:

```yaml
enforce:
  - dlp.enabled
  - dlp.action
```

This prevents individual users from disabling DLP or changing the action, ensuring organizational policy is maintained.
