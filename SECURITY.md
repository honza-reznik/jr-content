# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Reporting a Vulnerability

**Please do NOT open a public issue for security vulnerabilities.**

Instead, report vulnerabilities by emailing **support@techiebutler.com** or via [GitHub Security Advisories](https://github.com/Techiebutler/freeframe/security/advisories/new).

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge your report within **48 hours** and provide a timeline for the fix.

## Security Practices

- All authentication uses JWT with short-lived access tokens (15 min) and refresh tokens (7 days)
- Passwords are hashed with bcrypt
- Magic codes use `secrets.randbelow()` (cryptographically secure)
- Global rate limiting (600 read / 300 write per minute per user)
- Per-endpoint rate limiting on sensitive routes (auth, share, setup)
- Share links support password protection with Redis-backed sessions
- Upload endpoints verify ownership before presigning
- SSE events require project membership
- HLS streaming uses token-authenticated proxy with directory traversal prevention
- All entities use soft delete for audit trails
