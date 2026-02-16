# TLS

Anteroom supports HTTPS with self-signed certificates for localhost.

## Enabling TLS

Set `app.tls: true` in your config:

```yaml
app:
  tls: true
```

Anteroom generates a self-signed certificate using the `cryptography` package and starts the server on `https://127.0.0.1:8080`.

## What Changes with TLS

When TLS is enabled:

- Server binds to HTTPS instead of HTTP
- HSTS header is set: `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- Cookies get the `Secure` flag
- Browser will show a certificate warning (expected for self-signed certs)

## When to Use TLS

TLS is useful when:

- Running Anteroom on a non-localhost network interface
- Accessing Anteroom from another device on your network
- Testing TLS-dependent features (Secure cookies, HSTS)

!!! info
    For localhost-only usage (the default `127.0.0.1` bind), TLS is optional. The `Secure` cookie flag is automatically skipped for localhost, and all other security headers still apply.
